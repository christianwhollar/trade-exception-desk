import json
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from trade_desk.api import create_app
from trade_desk.auth import DEMO_KEYS, Identity
from trade_desk.demo import SAMPLE
from trade_desk.domain import bond_price, yield_to_maturity, Investigation, reconcile
from trade_desk.store import Conflict, Store
from trade_desk.workflow import execute


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def create(store, key="one"):
    return store.create(
        Identity("alpha", "analyst", "analyst"),
        key,
        Investigation.model_validate(SAMPLE).model_dump(mode="json"),
    )[0]


def test_cash_difference_and_currency_mismatch():
    result = reconcile(Investigation.model_validate(SAMPLE))
    assert result["cash_difference"] == "-9912.50"
    changed = json.loads(json.dumps(SAMPLE))
    changed["counterparty"]["currency"] = "EUR"
    result = reconcile(Investigation.model_validate(changed))
    assert result["cash_difference"] is None
    assert result["severity"] == "high"


@pytest.mark.parametrize("yield_rate", [-0.02, 0, 0.03, 0.2])
def test_bond_round_trip(yield_rate):
    p = bond_price(0.04, yield_rate, 7)
    assert yield_to_maturity(p, 0.04, 7) == pytest.approx(yield_rate)


def test_idempotency_under_concurrency(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: create(store)["id"], range(20)))
    assert len(set(ids)) == 1
    assert len(store.audit("alpha")["events"]) == 1
    with pytest.raises(Conflict):
        store.create(Identity("alpha", "a", "analyst"), "one", {"different": True})


def test_lease_recovery_and_stale_completion(store):
    row = create(store)
    _, stale = store.claim("alpha", row["id"], now=time.time() - 120)
    _, current = store.claim("alpha", row["id"])
    with pytest.raises(Conflict):
        store.finish("alpha", row["id"], stale, {"wrong": True})
    store.finish("alpha", row["id"], current, {"ok": True})


def test_retry_exhaustion(store):
    row = create(store)
    for _ in range(3):
        _, token = store.claim("alpha", row["id"])
        store.finish("alpha", row["id"], token, error=True)
    assert store.get("alpha", row["id"])["status"] == "failed"
    with pytest.raises(Conflict):
        store.claim("alpha", row["id"])


def test_last_attempt_crash_is_reaped(store):
    row = create(store)
    with store.connect() as db:
        db.execute(
            "UPDATE cases SET status='working',attempts=3,lease_until=? WHERE id=?",
            (time.time() - 100, row["id"]),
        )
    assert store.reap_exhausted() == 1
    assert store.get("alpha", row["id"])["status"] == "failed"
    assert store.reap_exhausted() == 0


def test_separation_of_duties_and_audit_tamper_detection(store):
    row = create(store)
    execute(store, "alpha", row["id"])
    with pytest.raises(PermissionError):
        store.decide(Identity("alpha", "analyst", "reviewer"), row["id"], True, "self approve")
    store.decide(
        Identity("alpha", "independent", "reviewer"), row["id"], True, "confirmed against evidence"
    )
    assert store.audit("alpha")["valid"]
    with store.connect() as db:
        db.execute("UPDATE audit SET body='changed' WHERE sequence=1")
    assert not store.audit("alpha")["valid"]


def test_api_tenant_isolation_and_auth(tmp_path):
    with TestClient(create_app(tmp_path / "api.db", DEMO_KEYS)) as client:
        assert client.post("/cases", json=SAMPLE).status_code == 401
        headers = {"Authorization": "Bearer demo-analyst", "Idempotency-Key": "test"}
        row = client.post("/cases", json=SAMPLE, headers=headers).json()
        beta = {"Authorization": "Bearer demo-beta"}
        assert client.get("/cases/" + row["id"], headers=beta).status_code == 404
        assert client.post(f"/cases/{row['id']}/investigate", headers=beta).status_code == 404
        response = client.post(f"/cases/{row['id']}/investigate", headers=headers)
        assert response.json()["status"] == "awaiting_review"
        assert "x-request-id" in response.headers
        assert client.get("/metrics", headers=headers).status_code == 200
