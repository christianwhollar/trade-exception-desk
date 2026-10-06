# Architecture and decisions

The README describes the current architecture and measured results. This document records the deployment boundary and links decisions to inspectable code.

## State and recovery

`pending → working → awaiting_review → approved | rejected` is the successful path. A worker failure becomes `retry`; after three attempts it becomes `failed`. An expired final lease is reaped. Reviewer-authorized manual retry records a note and starts a new attempt cycle. Closed cases are immutable.

SQLite is the deployment boundary: use a persistent local disk, one host, and WAL-aware backups through `sqlite3.Connection.backup`. Do not copy only the main database while WAL writes are active. `python -m trade_desk.reliability --output runtime/restore-study.json` exercises expired leases, duplicate workers, audit verification, and restore.

The v2 schema migration adds version, timestamps, assignment and priority columns without deleting existing cases. Back up before upgrade. Rollback to old code discards UI functionality but does not require removing columns. Test rollback against a copy.

## Optional service credentials

For a single demo tenant, set `SERVICE_TENANT=alpha`, `EVIDENCE_URL`, `EVIDENCE_API_KEY`, `ROUTER_URL`, and `ROUTER_API_KEY`. For multiple tenants, use a trusted server-side mapping:

```json
{"alpha":{"evidence":"tenant-alpha-service-key","router":"tenant-alpha-router-key"},"beta":{"evidence":"tenant-beta-service-key","router":"tenant-beta-router-key"}}
```

Store it as `SERVICE_KEYS_JSON` through your secret manager. Never reuse an alpha credential as an implicit fallback for beta. The service refuses such a fallback and records optional-service unavailability while preserving deterministic findings. The downstream keys must actually belong to their declared tenants.

## Useful failure demonstrations

- Submit different files with the same import key: expect 409 and no second batch.
- Update a case twice with one expected version: the second update returns 409.
- Review as the maker or analyst: expect 403.
- Kill a worker after claim, let its lease expire, and retry: the old token cannot finish.
- Stop the model service: the case still reaches review with deterministic commentary.
- Restore a backup and compare its audit head with an independently saved head.


## Evolution

The original compact reference implementation is documented in [design-v1.md](design-v1.md). Version 0.2 adds a complete browser workflow, operational state handling, larger experiments or failure studies, and reproducible release artifacts. Earlier studies remain in `reports/`; they have not been replaced with improved numbers under their original names.

See [operations.md](operations.md) for setup and failure demonstrations and [verification.md](verification.md) for the exact validation scope.

## Interface design

Compact tabs, a continuous metrics strip, and dense tables for reviewing exceptions. This trading operations uses its own typography, spacing, navigation and component shapes. Fonts and icons are local system fonts and inline SVG, with no external asset requests. The interface supports narrow screens, visible keyboard focus, a skip link, and active navigation semantics.
