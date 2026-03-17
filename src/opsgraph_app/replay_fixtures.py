from __future__ import annotations

from pathlib import Path

from .shared_runtime import load_shared_agent_platform


def replay_fixture_root() -> Path:
    return Path(__file__).resolve().parents[2] / "replay_fixtures"


def seed_incident_response_replay_fixtures(*, workflow_run_id: str, fixture_store=None) -> object:
    ap = load_shared_agent_platform()
    fixture_store = fixture_store or ap.FileReplayFixtureStore(replay_fixture_root())

    fixture_specs = [
        (
            1,
            "triage",
            "opsgraph.triage",
            {
                "status": "success",
                "summary": "Triaged the incident.",
                "structured_output": {
                    "dedupe_group_key": "checkout-api:high-error-rate",
                    "severity": "sev1",
                    "severity_confidence": 0.88,
                    "title": "Elevated 5xx on checkout-api",
                    "service_id": "service-1",
                    "blast_radius_summary": "Checkout traffic is impacted across the primary region.",
                },
            },
        ),
        (
            2,
            "hypothesize",
            "opsgraph.investigator",
            {
                "status": "success",
                "summary": "Generated incident hypotheses.",
                "structured_output": {
                    "hypotheses": [
                        {
                            "title": "Recent deploy introduced connection pool exhaustion.",
                            "confidence": 0.82,
                            "rank": 1,
                            "evidence_refs": [{"kind": "deployment", "id": "deploy-123"}],
                            "verification_steps": [
                                {
                                    "step_order": 1,
                                    "instruction_text": "Check DB connection saturation metrics.",
                                }
                            ],
                        }
                    ]
                },
                "citations": [{"kind": "deployment", "id": "deploy-123"}],
            },
        ),
        (
            3,
            "advise",
            "opsgraph.runbook_advisor",
            {
                "status": "success",
                "summary": "Recommended mitigation steps.",
                "structured_output": {
                    "recommendations": [
                        {
                            "recommendation_type": "mitigate",
                            "risk_level": "high_risk",
                            "requires_approval": True,
                            "title": "Roll back deployment 123",
                            "instructions_markdown": "Rollback checkout-api deploy 123.",
                            "evidence_refs": [{"kind": "deployment", "id": "deploy-123"}],
                        }
                    ]
                },
                "citations": [{"kind": "deployment", "id": "deploy-123"}],
            },
        ),
        (
            4,
            "communicate",
            "opsgraph.comms",
            {
                "status": "success",
                "summary": "Generated incident communication drafts.",
                "structured_output": {
                    "drafts": [
                        {
                            "channel_type": "internal_slack",
                            "fact_set_version": 1,
                            "body_markdown": "We are investigating elevated error rates affecting checkout.",
                            "fact_refs": [{"kind": "incident_fact", "id": "fact-1"}],
                        }
                    ]
                },
                "citations": [{"kind": "incident_fact", "id": "fact-1"}],
            },
        ),
    ]

    for checkpoint_seq, node_name, bundle_id, expected_output in fixture_specs:
        fixture_store.save(
            ap.ReplayFixture(
                fixture_key=ap.ReplayFixtureLoader.make_fixture_key(
                    workflow_run_id=workflow_run_id,
                    checkpoint_seq=checkpoint_seq,
                    node_name=node_name,
                ),
                workflow_type="opsgraph_incident",
                node_name=node_name,
                bundle_id=bundle_id,
                bundle_version="2026-03-16.1",
                expected_output=expected_output,
            )
        )
    return fixture_store
