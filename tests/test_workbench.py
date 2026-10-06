from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import time

import pytest
from fastapi.testclient import TestClient
from trade_desk.api import create_app
from trade_desk.auth import DEMO_KEYS, Identity
from trade_desk.feeds import ingest
from trade_desk.pricing import FixedBond, price, implied_yield, shift_months
from trade_desk.seed import sample_feeds
from trade_desk.store import Store, Conflict
from trade_desk.workflow import execute

A = {"Authorization": "Bearer demo-analyst"}
R = {"Authorization": "Bearer demo-reviewer"}
B = {"Authorization": "Bearer demo-beta"}


def test_atomic_feed_validation_idempotency_and_missing_records(tmp_path):
    store = Store(tmp_path / "desk.db")
    actor = Identity("alpha", "maker", "analyst")
    left, right = sample_feeds()
    first = ingest(store, actor, "batch", left, right)
    assert first["quarantined"] and first["matched"] and first["cases"]
    assert ingest(store, actor, "batch", left, right)["replayed"]
    assert not any(c["created"] for c in ingest(store, actor, "another-key", left, right)["cases"])
    before = store.list_cases("alpha")["total"]
    with pytest.raises(ValueError):
        ingest(store, actor, "invalid", left + "bad row\n", right)
    assert store.list_cases("alpha")["total"] == before
    with pytest.raises(Conflict):
        ingest(store, actor, "batch", left.replace("TW-6100", "TW-9999"), right)


def test_complete_review_flow_and_version_conflict(tmp_path):
    with TestClient(create_app(tmp_path / "desk.db", DEMO_KEYS)) as c:
        left, right = sample_feeds(8)
        result = c.post(
            "/imports",
            headers={**A, "Idempotency-Key": "ui-import"},
            json={"internal_csv": left, "counterparty_csv": right},
        )
        case_id = result.json()["cases"][0]["id"]
        assert c.get("/cases/" + case_id, headers=B).status_code == 404
        row = c.post("/cases/" + case_id + "/investigate", headers=A).json()
        update = {
            "expected_version": row["version"],
            "note": "Checking the source records",
            "priority": "urgent",
        }
        assert c.patch("/cases/" + case_id, headers=A, json=update).status_code == 200
        decision = {
            "approved": True,
            "note": "Evidence reviewed and accepted",
            "expected_version": row["version"],
        }
        assert (
            c.post("/cases/" + case_id + "/decision", headers=R, json=decision).status_code == 409
        )
        decision["expected_version"] += 1
        assert (
            c.post("/cases/" + case_id + "/decision", headers=A, json=decision).status_code == 403
        )
        assert (
            c.post("/cases/" + case_id + "/decision", headers=R, json=decision).json()["status"]
            == "approved"
        )
        assert (
            c.patch(
                "/cases/" + case_id,
                headers=A,
                json={**update, "expected_version": decision["expected_version"] + 1},
            ).status_code
            == 409
        )
        assert c.get("/audit", headers=R).json()["valid"]


def test_racing_workers_and_crash_recovery(tmp_path):
    store = Store(tmp_path / "desk.db")
    actor = Identity("alpha", "maker", "analyst")
    first = ingest(store, actor, "batch", *sample_feeds(8))
    case_id = first["cases"][0]["id"]

    def claim(_):
        try:
            return store.claim("alpha", case_id)
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(claim, range(24)))
    assert sum(x is not None for x in results) == 1
    token = next(x[1] for x in results if x)
    with store.connect() as db:
        db.execute("UPDATE cases SET lease_until=? WHERE id=?", (time.time() - 1, case_id))
    execute(store, "alpha", case_id)
    with pytest.raises(Conflict):
        store.finish("alpha", case_id, token, {"invalid": "stale"})
    assert store.get("alpha", case_id)["status"] == "awaiting_review"
    assert Store(tmp_path / "desk.db").audit("alpha")["valid"]


def test_pricing_cashflows_and_inverse_yield():
    bond = FixedBond(
        settlement=date(2026, 11, 10),
        maturity=date(2031, 11, 30),
        coupon_rate=0.045,
        yield_rate=0.048,
    )
    result = price(bond)
    assert result["dirty_price"] == pytest.approx(
        sum(c["present_value"] for c in result["cashflows"])
    )
    assert result["clean_price"] + result["accrued_interest"] == pytest.approx(
        result["dirty_price"]
    )
    assert implied_yield(bond, result["clean_price"]) == pytest.approx(bond.yield_rate, abs=1e-10)
    assert result["dv01"] > 0 and result["modified_duration"] > 0
    assert all(
        a["clean_price"] > b["clean_price"] for a, b in zip(result["shocks"], result["shocks"][1:])
    )
    assert shift_months(date(2028, 8, 31), -6) == date(2028, 2, 29)
    assert shift_months(date(2027, 8, 31), -6) == date(2027, 2, 28)


def test_audit_modification_is_detected(tmp_path):
    store = Store(tmp_path / "desk.db")
    ingest(store, Identity("alpha", "maker", "analyst"), "batch", *sample_feeds(8))
    with store.connect() as db:
        row = db.execute("SELECT sequence,body FROM audit LIMIT 1").fetchone()
        body = json.loads(row["body"])
        body["actor"] = "intruder"
        db.execute("UPDATE audit SET body=? WHERE sequence=?", (json.dumps(body), row["sequence"]))
    assert not store.audit("alpha")["valid"]


def test_service_credentials_never_fall_back_across_tenants(monkeypatch):
    from trade_desk.workflow import service_key, summarize

    monkeypatch.setenv("SERVICE_TENANT", "alpha")
    monkeypatch.setenv("ROUTER_API_KEY", "alpha-only-key")
    monkeypatch.setenv("ROUTER_URL", "http://127.0.0.1:1")
    assert service_key("alpha", "router") == "alpha-only-key"
    with pytest.raises(KeyError):
        service_key("beta", "router")
    result = summarize({"differences": [], "severity": "normal"}, "beta")
    assert result["narrative_source"] == "deterministic"
    assert result["stages"][-1]["status"] == "fallback"
