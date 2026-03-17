from __future__ import annotations

import json
from pathlib import Path


def replay_report_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "replay_reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_replay_report_artifacts(*, report_id: str, payload: dict) -> str:
    root = replay_report_root()
    json_path = root / f"{report_id}.json"
    md_path = root / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    return str(json_path)


def _to_markdown(payload: dict) -> str:
    lines = [
        f"# Replay Report {payload['report']['report_id']}",
        "",
        f"- Incident: `{payload['report']['incident_id']}`",
        f"- Replay Run: `{payload['report']['replay_run_id']}`",
        f"- Baseline: `{payload['report']['baseline_id']}`",
        f"- Status: `{payload['report']['status']}`",
        f"- Score: `{payload['report']['score']}`",
        "",
        "## State Comparison",
        "",
        f"- Baseline final state: `{payload['report'].get('baseline_final_state')}`",
        f"- Replay final state: `{payload['report'].get('replay_final_state')}`",
        f"- Baseline checkpoint seq: `{payload['report'].get('baseline_checkpoint_seq')}`",
        f"- Replay checkpoint seq: `{payload['report'].get('replay_checkpoint_seq')}`",
        "",
        "## Node Diffs",
        "",
    ]
    for node in payload["report"].get("node_diffs", []):
        lines.extend(
            [
                f"### Node {node['checkpoint_seq']}",
                f"- Matched: `{node['matched']}`",
                f"- Expected bundle: `{node['expected_bundle_id']}@{node['expected_bundle_version']}`",
                f"- Actual bundle: `{node.get('actual_bundle_id')}@{node.get('actual_bundle_version')}`",
                f"- Baseline elapsed ms: `{node.get('baseline_elapsed_ms')}`",
                f"- Replay elapsed ms: `{node.get('replay_elapsed_ms')}`",
                f"- Latency delta ms: `{node.get('latency_delta_ms')}`",
                f"- Expected summary: {node['expected_output_summary']}",
                f"- Actual summary: {node.get('actual_output_summary')}",
                f"- Mismatch reasons: {', '.join(node.get('mismatch_reasons', [])) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines)
