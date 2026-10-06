"""Atomic, validated feed ingestion for the desk HTTP interface."""

import csv
import hashlib
import io
from .domain import Investigation, Trade, reconcile


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    expected = set(Trade.model_fields)
    if set(reader.fieldnames or []) != expected:
        raise ValueError("CSV headers must be: " + ", ".join(sorted(expected)))
    records = {}
    for number, row in enumerate(reader, start=2):
        if number > 5001:
            raise ValueError("A batch supports at most 5000 records per feed")
        trade = Trade.model_validate(row)
        if trade.trade_id in records:
            raise ValueError(f"Duplicate trade identifier at row {number}: {trade.trade_id}")
        records[trade.trade_id] = trade
    if not records:
        raise ValueError("Feed must contain records")
    return records


def ingest(store, actor, key, internal_csv, counterparty_csv):
    left, right = parse_csv(internal_csv), parse_csv(counterparty_csv)
    pairs, matched = [], 0
    for trade_id in sorted(left.keys() & right.keys()):
        pair = Investigation(internal=left[trade_id], counterparty=right[trade_id])
        if reconcile(pair)["differences"]:
            pairs.append(pair.model_dump(mode="json"))
        else:
            matched += 1
    quarantined = [
        {"trade_id": key, "reason": "missing_confirmation" if key in left else "missing_booking"}
        for key in sorted(left.keys() ^ right.keys())
    ]
    digest = hashlib.sha256((internal_csv + "\0" + counterparty_csv).encode()).hexdigest()
    return store.import_batch(actor, key, digest, pairs, quarantined, matched)
