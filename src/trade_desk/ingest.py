"""Scan two structured feeds and proactively open only discrepant paired records."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from .auth import Identity
from .domain import Investigation, Trade, reconcile
from .store import Store, canonical


def read_feed(path):
    records = {}
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            trade = Trade.model_validate(row)
            if trade.trade_id in records:
                raise ValueError(f"Duplicate trade identifier in feed: {trade.trade_id}")
            records[trade.trade_id] = trade
    return records


def scan(store, actor, internal, counterparty):
    # Fully validate both files before creating any cases.
    left, right = read_feed(internal), read_feed(counterparty)
    quarantined = [
        {"trade_id": key, "reason": "missing_counterparty" if key in left else "missing_internal"}
        for key in sorted(left.keys() ^ right.keys())
    ]
    opened = []
    for key in sorted(left.keys() & right.keys()):
        pair = Investigation(internal=left[key], counterparty=right[key])
        if not reconcile(pair)["differences"]:
            continue
        payload = pair.model_dump(mode="json")
        fingerprint = hashlib.sha256(canonical(payload).encode()).hexdigest()
        case, created = store.create(actor, "feed:" + fingerprint, payload)
        opened.append({"case_id": case["id"], "trade_id": key, "created": created})
    return {"cases": opened, "quarantined": quarantined}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--internal", required=True)
    parser.add_argument("--counterparty", required=True)
    parser.add_argument("--tenant", required=True)
    args = parser.parse_args()
    store = Store(Path(os.getenv("DATA_DIR", "runtime")) / "trades.db")
    result = scan(
        store, Identity(args.tenant, "feed-importer", "analyst"), args.internal, args.counterparty
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
