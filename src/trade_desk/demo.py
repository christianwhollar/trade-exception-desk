import json
import tempfile
from pathlib import Path
from .auth import Identity
from .domain import Investigation
from .store import Store
from .workflow import execute

SAMPLE = {
    "internal": {
        "trade_id": "T-1042",
        "instrument": "SYNTH-BOND-5Y",
        "quantity": "1000",
        "price": "99.125",
        "settlement": "2026-10-08",
        "currency": "USD",
    },
    "counterparty": {
        "trade_id": "T-1042",
        "instrument": "SYNTH-BOND-5Y",
        "quantity": "1100",
        "price": "99.125",
        "settlement": "2026-10-09",
        "currency": "USD",
    },
}


def main():
    with tempfile.TemporaryDirectory() as temp:
        store = Store(Path(temp) / "demo.db")
        maker = Identity("alpha", "alex", "analyst")
        reviewer = Identity("alpha", "sam", "reviewer")
        payload = Investigation.model_validate(SAMPLE).model_dump(mode="json")
        row, _ = store.create(maker, "demo-1", payload)
        report = execute(store, "alpha", row["id"])
        store.decide(
            reviewer,
            row["id"],
            False,
            "Counterparty must confirm quantity and settlement before release.",
        )
        print(
            json.dumps(
                {
                    "report": json.loads(report["report"]),
                    "final_status": "rejected",
                    "audit_valid": store.audit("alpha")["valid"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
