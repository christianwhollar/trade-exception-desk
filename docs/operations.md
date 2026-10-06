# Operating guide

## Start, stop, and inspect

Start the loopback demo with `python -m trade_desk.serve --demo` and open port 8101. Stop with Ctrl-C. Persistent data lives under the configured runtime directory. Restarting does not reset edited demo data. Remove or choose a fresh runtime directory only when you deliberately want a fresh synthetic workspace.

For hosted use, configure `API_KEYS_JSON` with strong random keys and server-owned tenant/user/role claims; omit `APP_DEMO`. Terminate TLS, restrict network access and persist application state. Do not publish the built-in demo credentials on an externally reachable service. The included Compose configurations publish to loopback only.

`/health` checks application availability. The three operational services expose authenticated `/metrics` and optional OpenTelemetry export through `OTEL_EXPORTER_OTLP_ENDPOINT`. These metrics do not log raw prompts, API keys, or document bodies. The evaluation and model services expose health and their explicit run/inference results.

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


## Five-minute technical walkthrough

Explain the central data flow in the README, run one successful user workflow, deliberately trigger one failure above, inspect its recorded evidence, and explain one limitation you would address before a broader deployment. Describe measured results as results of this repository's experiments rather than as prior employer production outcomes.
