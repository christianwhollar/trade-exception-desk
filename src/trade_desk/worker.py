"""One durable-queue poll. Schedule repeatedly with a supervisor or container job."""

import os
from pathlib import Path
from .store import Conflict, Store
from .workflow import execute


def continuous(store, stop):
    import time

    while not stop.is_set():
        store.reap_exhausted()
        with store.connect() as db:
            rows = db.execute(
                "SELECT tenant,id FROM cases WHERE attempts<3 AND (status IN ('pending','retry') OR (status='working' AND lease_until<?)) ORDER BY created_at LIMIT 20",
                (time.time(),),
            ).fetchall()
        for tenant, case_id in rows:
            if stop.is_set():
                break
            try:
                execute(store, tenant, case_id)
            except Conflict:
                continue
            except Exception:
                # execute records a retry/failed transition; the next poll may retry.
                continue
        stop.wait(2)


def main():
    store = Store(Path(os.getenv("DATA_DIR", "runtime")) / "trades.db")
    store.reap_exhausted()
    with store.connect() as db:
        rows = db.execute(
            "SELECT tenant,id FROM cases WHERE status IN ('pending','retry','working') AND attempts<3 ORDER BY rowid LIMIT 100"
        ).fetchall()
    for tenant, case_id in rows:
        try:
            execute(store, tenant, case_id)
        except Conflict:
            continue
        except Exception:
            print(f"Investigation failed: {case_id}")


if __name__ == "__main__":
    main()
