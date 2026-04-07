from __future__ import annotations

import csv
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
    csv_path = root / f"{report_id}.csv"
    normalized_payload = _with_artifact_paths(
        payload,
        json_path=json_path,
        markdown_path=md_path,
        csv_path=csv_path,
    )
    json_path.write_text(json.dumps(normalized_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_to_markdown(normalized_payload), encoding="utf-8")
    _write_node_diff_csv(csv_path, normalized_payload)
    return str(json_path)


def _to_markdown(payload: dict) -> str:
    artifacts = dict(payload.get("artifacts") or {})
    lines = [
        f"# Replay Report {payload['report']['report_id']}",
        "",
        f"- Incident: `{payload['report']['incident_id']}`",
        f"- Replay Run: `{payload['report']['replay_run_id']}`",
        f"- Baseline: `{payload['report']['baseline_id']}`",
        f"- Status: `{payload['report']['status']}`",
        f"- Score: `{payload['report']['score']}`",
        f"- Matched nodes: `{payload['report'].get('matched_node_count')}`",
        f"- Mismatched nodes: `{payload['report'].get('mismatched_node_count')}`",
        f"- Node match rate: `{payload['report'].get('node_match_rate')}`",
        f"- Bundle mismatches: `{payload['report'].get('bundle_mismatch_count')}`",
        f"- Version mismatches: `{payload['report'].get('version_mismatch_count')}`",
        f"- Summary mismatches: `{payload['report'].get('summary_mismatch_count')}`",
        f"- Missing baseline nodes: `{payload['report'].get('missing_baseline_node_count')}`",
        f"- Missing replay nodes: `{payload['report'].get('missing_replay_node_count')}`",
        f"- State mismatches: `{payload['report'].get('state_mismatch_count')}`",
        f"- Checkpoint mismatches: `{payload['report'].get('checkpoint_mismatch_count')}`",
        f"- Latency regressions: `{payload['report'].get('latency_regression_count')}`",
        f"- Latency improvements: `{payload['report'].get('latency_improvement_count')}`",
        f"- Latency regression total ms: `{payload['report'].get('latency_regression_total_ms')}`",
        f"- Avg latency delta ms: `{payload['report'].get('avg_latency_delta_ms')}`",
        f"- Max latency delta ms: `{payload['report'].get('max_latency_delta_ms')}`",
        f"- Semantic checks: `{payload['report'].get('semantic_check_count')}`",
        f"- Semantic mismatches: `{payload['report'].get('semantic_mismatch_count')}`",
        f"- Semantic match rate: `{payload['report'].get('semantic_match_rate')}`",
        f"- Top hypothesis hit rate: `{payload['report'].get('top_hypothesis_hit_rate')}`",
        f"- Recommendation match rate: `{payload['report'].get('recommendation_match_rate')}`",
        f"- Comms match rate: `{payload['report'].get('comms_match_rate')}`",
        "",
        "## Artifacts",
        "",
        f"- JSON report: `{artifacts.get('json_report_path')}`",
        f"- Markdown report: `{artifacts.get('markdown_report_path')}`",
        f"- CSV diff export: `{artifacts.get('csv_report_path')}`",
        "",
        "## State Comparison",
        "",
        f"- Baseline final state: `{payload['report'].get('baseline_final_state')}`",
        f"- Replay final state: `{payload['report'].get('replay_final_state')}`",
        f"- Baseline checkpoint seq: `{payload['report'].get('baseline_checkpoint_seq')}`",
        f"- Replay checkpoint seq: `{payload['report'].get('replay_checkpoint_seq')}`",
        "",
        "## Semantic Checks",
        "",
    ]
    for check in payload["report"].get("semantic_checks", []):
        lines.extend(
            [
                f"### {check['check_name']}",
                f"- Matched: `{check['matched']}`",
                f"- Expected: {check.get('expected_summary')}",
                f"- Actual: {check.get('actual_summary')}",
                f"- Detail: {check.get('detail')}",
                "",
            ]
        )
    lines.extend(
        [
        "## Node Diffs",
        "",
        ]
    )
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


def _with_artifact_paths(
    payload: dict,
    *,
    json_path: Path,
    markdown_path: Path,
    csv_path: Path,
) -> dict:
    normalized_payload = dict(payload)
    normalized_report = dict(normalized_payload.get("report") or {})
    normalized_report["report_artifact_path"] = str(json_path)
    normalized_report["markdown_report_path"] = str(markdown_path)
    normalized_report["csv_report_path"] = str(csv_path)
    normalized_payload["report"] = normalized_report
    normalized_payload["artifacts"] = {
        "json_report_path": str(json_path),
        "markdown_report_path": str(markdown_path),
        "csv_report_path": str(csv_path),
    }
    return normalized_payload


def _write_node_diff_csv(csv_path: Path, payload: dict) -> None:
    fieldnames = [
        "checkpoint_seq",
        "matched",
        "expected_bundle_id",
        "actual_bundle_id",
        "expected_bundle_version",
        "actual_bundle_version",
        "expected_output_summary",
        "actual_output_summary",
        "baseline_elapsed_ms",
        "replay_elapsed_ms",
        "latency_delta_ms",
        "mismatch_reasons",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for node in payload.get("report", {}).get("node_diffs", []):
            writer.writerow(
                {
                    "checkpoint_seq": node.get("checkpoint_seq"),
                    "matched": node.get("matched"),
                    "expected_bundle_id": node.get("expected_bundle_id"),
                    "actual_bundle_id": node.get("actual_bundle_id"),
                    "expected_bundle_version": node.get("expected_bundle_version"),
                    "actual_bundle_version": node.get("actual_bundle_version"),
                    "expected_output_summary": node.get("expected_output_summary"),
                    "actual_output_summary": node.get("actual_output_summary"),
                    "baseline_elapsed_ms": node.get("baseline_elapsed_ms"),
                    "replay_elapsed_ms": node.get("replay_elapsed_ms"),
                    "latency_delta_ms": node.get("latency_delta_ms"),
                    "mismatch_reasons": "|".join(node.get("mismatch_reasons", [])),
                }
            )
