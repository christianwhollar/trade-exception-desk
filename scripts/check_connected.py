"""Exercise three running demo services using a synthetic case and policy document."""

import argparse
import json
from pathlib import Path
import uuid

import httpx
from trade_desk.demo import SAMPLE


def check(desk, evidence, router):
    analyst = {"Authorization": "Bearer demo-analyst"}
    reviewer = {"Authorization": "Bearer demo-reviewer"}
    beta = {"Authorization": "Bearer demo-beta"}
    with httpx.Client(timeout=90) as client:

        def post(url, body, headers=analyst):
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()

        policy = {
            "id": "connected-settlement-policy",
            "title": "Synthetic settlement and quantity review policy",
            "text": "A quantity or settlement date discrepancy requires counterparty confirmation and independent reviewer approval before release. The analyst records both source values and cannot approve their own case.",
            "roles": ["analyst", "reviewer"],
            "edges": [],
        }
        post(evidence + "/documents", policy, reviewer)
        question = {
            "question": "What review is required for a quantity or settlement date discrepancy?",
            "method": "bm25",
            "generate": True,
        }
        answer = post(evidence + "/answer", question)
        assert answer["mode"] == "generated", answer
        assert answer["citations"] and answer["entailment_verified"] is False
        created = post(
            desk + "/cases",
            SAMPLE,
            {**analyst, "Idempotency-Key": "connected-" + str(uuid.uuid4())},
        )
        case_id = created["id"]
        row = post(desk + f"/cases/{case_id}/investigate", {})
        report = row["report"]
        assert row["status"] == "awaiting_review"
        assert report["cash_difference"] == "-9912.50"
        assert report["narrative_source"] == "model_unverified_commentary", report
        assert report["policy_evidence"] and report["requires_independent_review"]
        decision = {
            "approved": False,
            "note": "Synthetic discrepancy requires counterparty confirmation.",
            "expected_version": row["version"],
        }
        assert (
            client.post(
                desk + f"/cases/{case_id}/decision", headers=analyst, json=decision
            ).status_code
            == 403
        )
        assert client.get(desk + f"/cases/{case_id}", headers=beta).status_code == 404
        final = post(desk + f"/cases/{case_id}/decision", decision, reviewer)
        assert final["status"] == "rejected"
        audit = client.get(desk + "/audit", headers=reviewer).json()
        assert audit["valid"]
        post(evidence + "/documents", {**policy, "roles": ["reviewer"]}, reviewer)
        hits = post(evidence + "/search", {"question": question["question"], "method": "bm25"})[
            "hits"
        ]
        assert all(hit["document_id"] != policy["id"] for hit in hits)
        reservations = client.get(router + "/reservations", headers=analyst).json()["items"]
        assert len(reservations) >= 2 and all(r["state"] == "settled" for r in reservations)
        return {
            "status": "passed",
            "scope": "Fresh local demo services, synthetic source records, actual Ollama model responses.",
            "generated_answer": answer,
            "investigation": report,
            "final_case_status": final["status"],
            "independent_review_enforced": True,
            "cross_tenant_case_hidden": True,
            "revoked_policy_hidden_without_reindex": True,
            "audit_valid": audit["valid"],
            "settled_model_calls": len(reservations),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desk", default="http://127.0.0.1:8101")
    parser.add_argument("--evidence", default="http://127.0.0.1:8102")
    parser.add_argument("--router", default="http://127.0.0.1:8104")
    parser.add_argument("--output", default="runtime/connected-check.json")
    args = parser.parse_args()
    report = check(args.desk, args.evidence, args.router)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "report": str(path)}))


if __name__ == "__main__":
    main()
