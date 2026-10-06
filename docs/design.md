# Design decisions

The expensive uncertainty belongs in explanatory text. Arithmetic and state transitions are deterministic. Trade prices use currency per unit, while the separate bond example uses price per 100 face; conflating those conventions would produce incorrect exposure calculations.

The case table stores an idempotency key scoped to a tenant and a canonical input hash. Repeating the same request returns the same case. Reusing the key with different input returns a conflict. A `BEGIN IMMEDIATE` transaction ensures concurrent creators cannot both insert a case.

Work is leased, not held in an in-memory background queue. A lease token prevents a stale worker from committing after another worker has reclaimed its job. Leases last 60 seconds. The optional router request has a 35-second client deadline, leaving room to commit. Recovery reruns the small workflow; it does not resume inside an LLM request, and repeated provider requests may incur cost.

LangGraph gives the stages an explicit state schema and a hard recursion limit. It does not confer security by itself. The terminal state remains awaiting review, even when records match. A reviewer must have a different user identity from the maker.

The annual bond solver uses monotonic bisection with a bracket and tests price/yield round trips. It is an educational pricing function, separate from discrepancy processing.

Next work for a real deployment: PostgreSQL migrations, an identity provider, approved market data and calendars, independent penetration testing, durable external audit storage, and workload-specific load tests. The current tests demonstrate invariants, not a throughput SLA.

## Walkthrough

1. Run the demo and explain why the cash difference is negative.
2. Repeat a case submission concurrently and show one case/audit event.
3. Describe a worker crash before and after lease expiry.
4. Attempt self-approval and cross-tenant lookup.
5. Explain why the model cannot change the trade and why an audit hash alone is not immutable storage.

Useful extension: introduce an approved price tolerance, test values at the boundary, and explain how the policy is versioned in audit records.

Reference: [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
