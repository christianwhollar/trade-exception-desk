"""Exercise concurrent claims, process-loss recovery and SQLite backup/restore."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from .auth import Identity
from .feeds import ingest
from .seed import sample_feeds
from .store import Conflict, Store
from .workflow import execute


def study(count=512):
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "desk.db")
        actor = Identity("reliability", "maker", "analyst")
        reviewer = Identity("reliability", "reviewer", "reviewer")
        started = time.perf_counter()
        imported = ingest(store, actor, "batch", *sample_feeds(count))
        cases = [r["id"] for r in imported["cases"]]
        stale_tokens = {}
        for case_id in cases[:12]:
            _, token = store.claim(actor.tenant, case_id)
            stale_tokens[case_id] = token
        # Emulate a process dying after acquiring a lease. The subsequent store instance
        # has no in-memory ownership state and must recover through persisted leases.
        with store.connect() as db:
            db.execute("UPDATE cases SET lease_until=? WHERE status='working'", (time.time() - 1,))
        recovered = Store(store.path)

        def run(case_id):
            try:
                execute(recovered, actor.tenant, case_id)
                return "completed"
            except Conflict:
                return "lease_or_state_conflict"

        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(run, cases + cases[:40]))
        rejected_stale = 0
        for case_id, token in stale_tokens.items():
            try:
                recovered.finish(actor.tenant, case_id, token, {"stale": True})
            except Conflict:
                rejected_stale += 1
        for case_id in cases[:50]:
            recovered.decide(reviewer, case_id, True, "Controlled study: independent source review")
        live_audit = recovered.audit(actor.tenant)
        backup = Path(directory) / "restored.db"
        with sqlite3.connect(recovered.path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        restored = Store(backup)
        restored_audit = restored.audit(actor.tenant)
        assert restored_audit["valid"] and restored_audit["head"] == live_audit["head"]
        assert outcomes.count("completed") == len(cases)
        assert rejected_stale == len(stale_tokens)
        with recovered.connect() as db:
            states = dict(
                db.execute("SELECT status,COUNT(*) FROM cases GROUP BY status").fetchall()
            )
        return {
            "scope": "Synthetic feed, local SQLite, deterministic investigations; model calls disabled",
            "input_booking_rows": count,
            "input_confirmation_rows": count - 1,
            "matched_pairs": imported["matched"],
            "exceptions": len(cases),
            "quarantined": len(imported["quarantined"]),
            "concurrent_workers": 12,
            "attempted_executions": len(outcomes),
            "completed_once": outcomes.count("completed"),
            "duplicate_execution_conflicts": outcomes.count("lease_or_state_conflict"),
            "recovered_expired_leases": len(stale_tokens),
            "stale_completions_rejected": rejected_stale,
            "final_states": states,
            "audit_events": len(live_audit["events"]),
            "audit_valid": live_audit["valid"],
            "restore_head_matches": True,
            "wall_seconds": time.perf_counter() - started,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    path = Path(args.output)
    if path.exists():
        parser.error("Choose a new output path")
    result = study()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
