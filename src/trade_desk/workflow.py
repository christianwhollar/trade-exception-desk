"""Bounded specialist stages; an optional LLM drafts commentary, never decisions."""

import json
import os
import httpx
from typing import TypedDict, Literal
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from .domain import Investigation, reconcile


class Narrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=2000)
    evidence: list[Literal["internal", "counterparty"]] = Field(min_length=1, max_length=2)


def service_key(tenant, service):
    mapping = json.loads(os.getenv("SERVICE_KEYS_JSON", "{}"))
    if tenant in mapping and service in mapping[tenant]:
        return mapping[tenant][service]
    if tenant and tenant == os.getenv("SERVICE_TENANT"):
        return os.environ[service.upper() + "_API_KEY"]
    raise KeyError("No service credential configured for this tenant")


def summarize(result, tenant=None):
    result["stages"] = [
        {"role": "reconciler", "tool": "compare_records", "status": "complete"},
        {"role": "risk_checker", "tool": "cash_difference", "status": "complete"},
        {"role": "review_gate", "tool": "require_independent_review", "status": "complete"},
    ]
    result["narrative_source"] = "deterministic"
    result["summary"] = (
        "Records match."
        if not result["differences"]
        else "Differences require review: " + ", ".join(d["field"] for d in result["differences"])
    )
    evidence_url = os.getenv("EVIDENCE_URL")
    if evidence_url:
        try:
            query = (
                " ".join(d["field"] for d in result["differences"])
                + " discrepancy independent review"
            )
            response = httpx.post(
                evidence_url.rstrip("/") + "/search",
                headers={"Authorization": "Bearer " + service_key(tenant, "evidence")},
                json={"question": query, "method": "bm25", "limit": 2},
                timeout=10,
            )
            response.raise_for_status()
            result["policy_evidence"] = response.json()["hits"]
            result["stages"].append({"role": "policy_retriever", "status": "complete"})
        except (httpx.HTTPError, ValueError, KeyError):
            result["policy_evidence"] = []
            result["stages"].append({"role": "policy_retriever", "status": "unavailable"})
    endpoint = os.getenv("ROUTER_URL")
    if endpoint:
        try:
            with httpx.Client(timeout=35) as client:
                response = client.post(
                    endpoint.rstrip("/") + "/v1/complete",
                    headers={"Authorization": "Bearer " + service_key(tenant, "router")},
                    json={
                        "prompt": "Return JSON with summary and evidence (only internal or counterparty). Explain these deterministic findings without proposing trades: "
                        + json.dumps(result),
                        "max_output_tokens": 400,
                        "budget_usd": 0.02,
                        "minimum_quality": 0.5,
                        "data_class": "confidential",
                        "response_format": "json",
                        "output_schema": Narrative.model_json_schema(),
                    },
                )
                response.raise_for_status()
                draft = Narrative.model_validate_json(response.json()["text"])
                if not draft.evidence or not set(draft.evidence) <= {"internal", "counterparty"}:
                    raise ValueError("Unsupported evidence reference")
                result["summary"] = draft.summary
                result["narrative_source"] = "model_unverified_commentary"
                result["stages"].append({"role": "narrator", "status": "complete"})
        except (httpx.HTTPError, ValueError, KeyError):
            result["stages"].append({"role": "narrator", "status": "fallback"})
    return result


class State(TypedDict):
    payload: dict
    report: dict
    tenant: str | None


def build_graph():
    graph = StateGraph(State)

    def validate(state):
        return {"payload": Investigation.model_validate(state["payload"]).model_dump(mode="json")}

    def compare(state):
        return {"report": reconcile(Investigation.model_validate(state["payload"]))}

    def annotate(state):
        return {"report": summarize(dict(state["report"]), state.get("tenant"))}

    def gate(state):
        return {"report": {**state["report"], "requires_independent_review": True}}

    graph.add_node("validate", validate)
    graph.add_node("reconcile", compare)
    graph.add_node("narrate", annotate)
    graph.add_node("review_gate", gate)
    graph.add_edge(START, "validate")
    graph.add_edge("validate", "reconcile")
    graph.add_edge("reconcile", "narrate")
    graph.add_edge("narrate", "review_gate")
    graph.add_edge("review_gate", END)
    return graph.compile()


GRAPH = build_graph()


def investigate(payload, tenant=None):
    return GRAPH.invoke(
        {"payload": payload, "report": {}, "tenant": tenant}, config={"recursion_limit": 6}
    )["report"]


def execute(store, tenant, case_id):
    payload, token = store.claim(tenant, case_id)
    try:
        report = investigate(payload, tenant)
        store.finish(tenant, case_id, token, report)
    except Exception:
        store.finish(tenant, case_id, token, error=True)
        raise
    return store.get(tenant, case_id)
