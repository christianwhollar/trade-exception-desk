from pathlib import Path
import pytest
from trade_desk.auth import Identity
from trade_desk.ingest import scan
from trade_desk.store import Store


def test_feed_replay_matched_filter_and_quarantine(tmp_path):
    root = Path(__file__).resolve().parents[1]
    store = Store(tmp_path / "feed.db")
    actor = Identity("alpha", "importer", "analyst")
    first = scan(store, actor, root / "examples/internal.csv", root / "examples/counterparty.csv")
    second = scan(store, actor, root / "examples/internal.csv", root / "examples/counterparty.csv")
    assert len(first["cases"]) == 1
    assert first["cases"][0]["created"]
    assert not second["cases"][0]["created"]
    assert first["quarantined"] == [{"trade_id": "T-1044", "reason": "missing_counterparty"}]


def test_invalid_feed_fails_before_writing(tmp_path):
    root = Path(__file__).resolve().parents[1]
    malformed = tmp_path / "bad.csv"
    malformed.write_text(
        "trade_id,instrument,quantity,price,settlement,currency\nT,x,not-a-number,2,2026-10-08,USD\n"
    )
    store = Store(tmp_path / "feed.db")
    with pytest.raises(ValueError):
        scan(
            store,
            Identity("alpha", "importer", "analyst"),
            root / "examples/internal.csv",
            malformed,
        )
    assert store.audit("alpha")["events"] == []
