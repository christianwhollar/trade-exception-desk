"""One durable-queue poll. Schedule repeatedly with a supervisor or container job."""

import os
from pathlib import Path
from .store import Conflict, Store
from .workflow import execute


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
