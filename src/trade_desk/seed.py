"""Reproducible synthetic desk inventory; safe to rerun against a demo database."""

import csv
import io
from .auth import Identity
from .feeds import ingest
from .workflow import execute


def sample_feeds(count=32):
    fields = ["trade_id", "instrument", "quantity", "price", "settlement", "currency"]
    internal, confirmation = [], []
    for i in range(count):
        row = dict(
            zip(
                fields,
                [
                    f"TW-{6100 + i}",
                    ["UST-5Y", "CORP-ALPHA", "UST-10Y", "CORP-BETA"][i % 4],
                    str(1000 + i * 100),
                    str(95 + i % 12),
                    f"2026-11-{10 + i % 10}",
                    "USD",
                ],
            )
        )
        other = dict(row)
        if i % 6 == 0:
            other["quantity"] = str(int(row["quantity"]) + 250)
        elif i % 6 == 1:
            other["price"] = str(int(row["price"]) + 0.5)
        elif i % 6 == 2:
            other["settlement"] = "2026-12-01"
        elif i % 6 == 3:
            other["currency"] = "EUR"
        internal.append(row)
        if i != count - 1:
            confirmation.append(other)

    def encode(rows):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()

    return encode(internal), encode(confirmation)


def seed(store):
    maker, reviewer = (
        Identity("alpha", "analyst", "analyst"),
        Identity("alpha", "reviewer", "reviewer"),
    )
    left, right = sample_feeds()
    result = ingest(store, maker, "demo-inventory-v2", left, right)
    if result["replayed"]:
        return
    for i, item in enumerate(result["cases"]):
        if i % 5 == 4:
            continue
        execute(store, maker.tenant, item["id"])
        if i % 7 == 0:
            store.decide(reviewer, item["id"], True, "Synthetic demo: supporting records reviewed.")
        elif i % 7 == 1:
            store.annotate(
                maker,
                item["id"],
                store.get("alpha", item["id"])["version"],
                "Awaiting corrected counterparty confirmation.",
                "operations",
                "urgent",
            )
