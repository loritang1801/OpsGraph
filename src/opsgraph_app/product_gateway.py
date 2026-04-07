from __future__ import annotations

import json
import re
from typing import Any

from .shared_runtime import load_shared_agent_platform


class _HeuristicOpsGraphProductModelGateway:
    def __init__(self) -> None:
        self._shared_platform = load_shared_agent_platform()

    def generate(self, *, assembled_prompt) -> Any:
        bundle_id = str(assembled_prompt.bundle_id)
        if bundle_id == "opsgraph.triage":
            payload = self._triage_response(assembled_prompt)
        elif bundle_id == "opsgraph.investigator":
            payload = self._investigator_response(assembled_prompt)
        elif bundle_id == "opsgraph.runbook_advisor":
            payload = self._runbook_advisor_response(assembled_prompt)
        elif bundle_id == "opsgraph.comms":
            payload = self._comms_response(assembled_prompt)
        elif bundle_id == "opsgraph.postmortem_reviewer":
            payload = self._postmortem_response(assembled_prompt)
        else:
            raise ValueError(f"Unsupported OpsGraph product bundle: {bundle_id}")
        return self._shared_platform.ModelGatewayResponse.model_validate(payload)

    @staticmethod
    def _tool_versions(assembled_prompt) -> dict[str, str]:
        return {
            str(tool.tool_name): str(tool.tool_version)
            for tool in assembled_prompt.tool_manifest
        }

    @classmethod
    def _planned_tool_call(
        cls,
        assembled_prompt,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        tool_versions = cls._tool_versions(assembled_prompt)
        tool_version = tool_versions.get(tool_name)
        if tool_version is None:
            return None
        return {
            "tool_name": tool_name,
            "tool_version": tool_version,
            "arguments": arguments,
        }

    @staticmethod
    def _signal_summaries(assembled_prompt) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in (assembled_prompt.resolved_variables.get("signal_summaries") or [])
            if isinstance(item, dict)
        ]

    @staticmethod
    def _ref_list(value: object, *, fallback_kind: str, fallback_id: str) -> list[dict[str, str]]:
        refs = [
            {
                "kind": str(item.get("kind") or fallback_kind),
                "id": str(item.get("id") or fallback_id),
            }
            for item in (value or [])
            if isinstance(item, dict) and item.get("id")
        ]
        return refs or [{"kind": fallback_kind, "id": fallback_id}]

    @classmethod
    def _first_ref(cls, value: object, *, fallback_kind: str, fallback_id: str) -> dict[str, str]:
        return cls._ref_list(value, fallback_kind=fallback_kind, fallback_id=fallback_id)[0]

    @staticmethod
    def _humanize_service(service_id: str) -> str:
        label = service_id.replace("_", "-").strip("-")
        if not label:
            return "service"
        words = [part for part in label.split("-") if part]
        if not words:
            return label
        return " ".join(words)

    @classmethod
    def _extract_service_id(cls, assembled_prompt) -> str | None:
        variables = assembled_prompt.resolved_variables
        explicit_service_id = variables.get("service_id")
        if explicit_service_id not in {None, ""}:
            return str(explicit_service_id)
        for signal in cls._signal_summaries(assembled_prompt):
            correlation_key = str(signal.get("correlation_key") or "")
            if ":" in correlation_key:
                candidate = correlation_key.split(":", 1)[0].strip()
                if candidate:
                    return candidate
            summary = str(signal.get("summary") or "")
            match = re.search(r"\b([a-z0-9]+(?:-[a-z0-9]+)+)\b", summary.lower())
            if match is not None:
                return match.group(1)
        return None

    @classmethod
    def _triage_response(cls, assembled_prompt) -> dict[str, Any]:
        variables = assembled_prompt.resolved_variables
        signal_ids = [str(value) for value in (variables.get("signal_ids") or []) if value]
        signal_summaries = cls._signal_summaries(assembled_prompt)
        first_signal = signal_summaries[0] if signal_summaries else {}
        service_id = cls._extract_service_id(assembled_prompt) or "service-1"
        summary_text = str(first_signal.get("summary") or service_id)
        dedupe_group_key = str(first_signal.get("correlation_key") or signal_ids[0] if signal_ids else service_id)
        combined_text = "\n".join(
            str(item.get("summary") or item.get("correlation_key") or "")
            for item in signal_summaries
        ).lower()
        severity = "sev1" if any(token in combined_text for token in ("5xx", "error", "outage", "latency")) else "sev2"
        severity_confidence = 0.88 if severity == "sev1" else 0.74
        human_service = cls._humanize_service(service_id)
        if "5xx" in combined_text:
            title = f"Elevated 5xx on {service_id}"
        elif "latency" in combined_text:
            title = f"Elevated latency on {service_id}"
        else:
            title = summary_text or f"Incident impacting {service_id}"
        blast_radius_summary = (
            f"{human_service.capitalize()} traffic is degraded in the primary production path."
        )
        tool_calls = [
            tool_call
            for tool_call in (
                cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="signal.read",
                    arguments={"signal_ids": signal_ids[:5] or ["signal-unknown"]},
                ),
                cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="service_registry.lookup",
                    arguments={"service_id": service_id},
                ),
            )
            if tool_call is not None
        ]
        return {
            "agent_output": {
                "status": "success",
                "summary": "Triaged the incident.",
                "structured_output": {
                    "dedupe_group_key": dedupe_group_key,
                    "severity": severity,
                    "severity_confidence": severity_confidence,
                    "title": title,
                    "service_id": service_id,
                    "blast_radius_summary": blast_radius_summary,
                },
            },
            "planned_tool_calls": tool_calls,
        }

    @classmethod
    def _investigator_response(cls, assembled_prompt) -> dict[str, Any]:
        variables = assembled_prompt.resolved_variables
        incident_id = str(variables.get("incident_id") or "incident-unknown")
        context_bundle_id = str(variables.get("context_bundle_id") or f"context-{incident_id}")
        confirmed_fact_refs = cls._ref_list(
            variables.get("confirmed_fact_refs"),
            fallback_kind="incident_fact",
            fallback_id="fact-unknown",
        )
        primary_ref = confirmed_fact_refs[0]
        missing_sources = [str(item) for item in (variables.get("context_missing_sources") or []) if item]
        verification_steps = [
            {
                "step_order": 1,
                "instruction_text": "Review the context bundle and timeline for the first correlated change.",
            },
            {
                "step_order": 2,
                "instruction_text": "Validate whether the leading hypothesis still matches the confirmed fact set.",
            },
        ]
        if missing_sources:
            verification_steps.append(
                {
                    "step_order": 3,
                    "instruction_text": (
                        "Backfill missing context sources before confirming mitigation. "
                        f"Missing: {', '.join(missing_sources)}."
                    ),
                }
            )
        tool_calls = [
            tool_call
            for tool_call in (
                cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="context_bundle.read",
                    arguments={
                        "incident_id": incident_id,
                        "context_bundle_id": context_bundle_id,
                    },
                ),
                cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="incident.read_timeline",
                    arguments={
                        "incident_id": incident_id,
                        "limit": 25,
                        "visibility": "all",
                    },
                ),
            )
            if tool_call is not None
        ]
        return {
            "agent_output": {
                "status": "success",
                "summary": "Generated incident hypotheses.",
                "structured_output": {
                    "hypotheses": [
                        {
                            "title": "Recent service change likely explains the current failure pattern.",
                            "confidence": 0.82,
                            "rank": 1,
                            "evidence_refs": confirmed_fact_refs[:2],
                            "verification_steps": verification_steps,
                        }
                    ]
                },
                "citations": [primary_ref],
            },
            "planned_tool_calls": tool_calls,
        }

    @classmethod
    def _runbook_advisor_response(cls, assembled_prompt) -> dict[str, Any]:
        variables = assembled_prompt.resolved_variables
        incident_id = str(variables.get("incident_id") or "incident-unknown")
        service_id = str(variables.get("service_id") or "service-1")
        confirmed_fact_refs = cls._ref_list(
            variables.get("confirmed_fact_refs"),
            fallback_kind="incident_fact",
            fallback_id="fact-unknown",
        )
        evidence_refs = cls._ref_list(
            variables.get("top_hypothesis_refs"),
            fallback_kind=confirmed_fact_refs[0]["kind"],
            fallback_id=confirmed_fact_refs[0]["id"],
        )
        risk_level = "high_risk" if any(ref["kind"] in {"deployment", "hypothesis"} for ref in evidence_refs) else "medium"
        requires_approval = risk_level in {"high_risk", "medium"}
        instructions_markdown = (
            f"1. Stabilize `{service_id}` using the documented rollback or mitigation path.\n"
            f"2. Re-check confirmed facts for incident `{incident_id}` before publishing status externally.\n"
            "3. Hold the change if impact is still growing after mitigation."
        )
        tool_calls = [
            tool_call
            for tool_call in (
                cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="service_registry.lookup",
                    arguments={"service_id": service_id},
                ),
                cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="runbook.search",
                    arguments={
                        "service_id": service_id,
                        "query": "rollback elevated error rate",
                        "limit": 3,
                    },
                ),
                cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="deployment.lookup",
                    arguments={
                        "service_id": service_id,
                        "incident_id": incident_id,
                        "limit": 3,
                    },
                ),
            )
            if tool_call is not None
        ]
        return {
            "agent_output": {
                "status": "success",
                "summary": "Recommended mitigation steps.",
                "structured_output": {
                    "recommendations": [
                        {
                            "recommendation_type": "mitigate",
                            "risk_level": risk_level,
                            "requires_approval": requires_approval,
                            "title": f"Stabilize {service_id} using the latest rollback-safe mitigation",
                            "instructions_markdown": instructions_markdown,
                            "evidence_refs": evidence_refs[:2],
                        }
                    ]
                },
                "citations": [evidence_refs[0]],
            },
            "planned_tool_calls": tool_calls,
        }

    @classmethod
    def _comms_response(cls, assembled_prompt) -> dict[str, Any]:
        variables = assembled_prompt.resolved_variables
        incident_id = str(variables.get("incident_id") or "incident-unknown")
        fact_set_version = int(variables.get("current_fact_set_version") or 1)
        target_channels = [str(value) for value in (variables.get("target_channels") or []) if value] or ["internal_slack"]
        fact_refs = cls._ref_list(
            variables.get("confirmed_fact_refs"),
            fallback_kind="incident_fact",
            fallback_id="fact-unknown",
        )
        draft_bodies = []
        tool_calls: list[dict[str, Any]] = []
        for index, channel_type in enumerate(target_channels):
            body_markdown = (
                f"Incident `{incident_id}` remains under active investigation.\n\n"
                f"- Fact set version: `{fact_set_version}`\n"
                "- Updates are grounded in confirmed incident facts only.\n"
                "- Next update will follow once mitigation status changes."
            )
            draft_bodies.append(
                {
                    "channel_type": channel_type,
                    "fact_set_version": fact_set_version,
                    "body_markdown": body_markdown,
                    "fact_refs": fact_refs[:2],
                }
            )
            if index == 0:
                preview_call = cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="comms.channel_preview",
                    arguments={
                        "channel_type": channel_type,
                        "draft_body": body_markdown,
                    },
                )
                if preview_call is not None:
                    tool_calls.append(preview_call)
        return {
            "agent_output": {
                "status": "success",
                "summary": "Generated incident communication drafts.",
                "structured_output": {"drafts": draft_bodies},
                "citations": [fact_refs[0]],
            },
            "planned_tool_calls": tool_calls,
        }

    @classmethod
    def _postmortem_response(cls, assembled_prompt) -> dict[str, Any]:
        variables = assembled_prompt.resolved_variables
        incident_id = str(variables.get("incident_id") or "incident-unknown")
        fact_set_version = int(variables.get("current_fact_set_version") or 1)
        resolution_summary = str(variables.get("resolution_summary") or "Resolution summary pending.")
        fact_refs = cls._ref_list(
            variables.get("confirmed_fact_refs"),
            fallback_kind="incident_fact",
            fallback_id="fact-unknown",
        )
        timeline_refs = cls._ref_list(
            variables.get("timeline_refs"),
            fallback_kind="timeline_event",
            fallback_id="timeline-unknown",
        )
        citation_ref = fact_refs[0]
        markdown = (
            f"# Incident {incident_id}\n\n"
            "## Resolution\n"
            f"{resolution_summary}\n\n"
            "## Confirmed Scope\n"
            f"- Final fact set version: `{fact_set_version}`\n"
            f"- Confirmed fact refs: {', '.join(ref['id'] for ref in fact_refs)}\n"
            f"- Timeline refs: {', '.join(ref['id'] for ref in timeline_refs)}\n"
        )
        tool_calls = [
            tool_call
            for tool_call in (
                cls._planned_tool_call(
                    assembled_prompt,
                    tool_name="incident.read_timeline",
                    arguments={
                        "incident_id": incident_id,
                        "limit": 50,
                        "visibility": "all",
                    },
                ),
            )
            if tool_call is not None
        ]
        return {
            "agent_output": {
                "status": "success",
                "summary": "Generated incident postmortem.",
                "structured_output": {
                    "postmortem_markdown": markdown,
                    "follow_up_actions": [
                        {
                            "title": "Capture the leading mitigation decision in the service runbook.",
                            "owner_hint": "incident-commander",
                        },
                        {
                            "title": "Preserve correlated change metadata for future replay coverage.",
                            "owner_hint": "service-owner",
                        },
                    ],
                    "replay_capture_hints": [
                        "capture correlated deployment metadata",
                        "persist incident timeline excerpts used in retrospective drafting",
                    ],
                },
                "citations": [citation_ref],
            },
            "planned_tool_calls": tool_calls,
        }


class _OpenAIResponsesOpsGraphProductGateway:
    def __init__(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        client=None,
    ) -> None:
        self._shared_platform = load_shared_agent_platform()
        self._response_model = self._shared_platform.ModelGatewayResponse
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
            )
        self._client = client
        self.model_name = model_name

    def generate(self, *, assembled_prompt) -> Any:
        response = self._client.responses.parse(
            model=self.model_name,
            instructions=self._build_instructions(assembled_prompt),
            input=self._render_prompt(assembled_prompt),
            text_format=self._response_model,
            max_output_tokens=1400,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ValueError("OPSGRAPH_MODEL_RESPONSE_EMPTY")
        if isinstance(parsed, self._response_model):
            return parsed
        return self._response_model.model_validate(parsed)

    @staticmethod
    def _build_instructions(assembled_prompt) -> str:
        tool_lines = [
            f"- {tool.tool_name}@{tool.tool_version}"
            for tool in assembled_prompt.tool_manifest
        ]
        tool_manifest = "\n".join(tool_lines) if tool_lines else "- none"
        return (
            "Return strictly valid JSON matching the provided schema. "
            "Do not invent tool names or citations not grounded in the prompt. "
            "If you plan a tool call, it must come from the allowed tool manifest.\n\n"
            f"Allowed tool manifest:\n{tool_manifest}"
        )

    @staticmethod
    def _render_prompt(assembled_prompt) -> str:
        rendered_parts: list[str] = [
            f"bundle_id: {assembled_prompt.bundle_id}",
            f"bundle_version: {assembled_prompt.bundle_version}",
            f"agent_name: {assembled_prompt.agent_name}",
            f"workflow_type: {assembled_prompt.workflow_type}",
            f"citation_policy_id: {assembled_prompt.citation_policy_id}",
            f"response_schema_ref: {assembled_prompt.response_schema_ref}",
        ]
        for part in assembled_prompt.parts:
            rendered_parts.append(f"\n## {part.name}")
            rendered_parts.append(part.description)
            if part.instructions:
                rendered_parts.append("instructions:")
                rendered_parts.extend(f"- {instruction}" for instruction in part.instructions)
            rendered_parts.append("variables:")
            rendered_parts.append(json.dumps(part.variables, ensure_ascii=True, indent=2, default=str))
        return "\n".join(rendered_parts)


class OpsGraphProductModelGateway:
    def __init__(
        self,
        *,
        primary_gateway=None,
        fallback_gateway=None,
        allow_fallback: bool | None = None,
    ) -> None:
        self._shared_platform = load_shared_agent_platform()
        self._fallback_gateway = fallback_gateway or _HeuristicOpsGraphProductModelGateway()
        self._requested_provider_mode = self._shared_platform.normalize_requested_mode(
            self._env_value("OPSGRAPH_MODEL_PROVIDER"),
            allowed_modes=("auto", "local", "openai"),
            default="auto",
        )
        self._configured_model_name = self._env_value("OPSGRAPH_OPENAI_MODEL")
        self._configured_allow_fallback = self._env_bool("OPSGRAPH_MODEL_ALLOW_FALLBACK")
        self._fallback_policy_source = "default"
        self._provider_mode_decision = None
        self._last_primary_error = None
        self._last_runtime_fallback_reason = None
        if primary_gateway is None:
            primary_gateway, provider_mode_decision = self._build_primary_gateway()
        else:
            provider_mode_decision = self._shared_platform.RuntimeModeDecision(
                requested_mode=self._requested_provider_mode,
                effective_mode="openai",
                use_remote=True,
                allow_fallback=True,
                fallback_reason=None,
            )
        self._primary_gateway = primary_gateway
        if allow_fallback is not None:
            resolved_allow_fallback = bool(allow_fallback)
            self._fallback_policy_source = "explicit"
        elif self._configured_allow_fallback is not None:
            resolved_allow_fallback = self._configured_allow_fallback
            self._fallback_policy_source = "env"
        else:
            resolved_allow_fallback = provider_mode_decision.allow_fallback
        if provider_mode_decision.requested_mode == "local":
            resolved_allow_fallback = False
        self._allow_fallback = resolved_allow_fallback
        self._provider_mode_decision = self._shared_platform.RuntimeModeDecision(
            requested_mode=provider_mode_decision.requested_mode,
            effective_mode=provider_mode_decision.effective_mode,
            use_remote=provider_mode_decision.use_remote,
            allow_fallback=self._allow_fallback,
            fallback_reason=provider_mode_decision.fallback_reason,
        )
        if (
            self._primary_gateway is None
            and self._provider_mode_decision.fallback_reason is not None
            and not self._allow_fallback
        ):
            raise ValueError(str(self._provider_mode_decision.fallback_reason))

    @staticmethod
    def _env_value(name: str) -> str | None:
        return load_shared_agent_platform().env_value(name)

    @classmethod
    def _env_bool(cls, name: str) -> bool | None:
        raw = cls._env_value(name)
        if raw is None:
            return None
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"INVALID_{name}")

    def _build_primary_gateway(self):
        provider = self._requested_provider_mode
        api_key = self._env_value("OPENAI_API_KEY")
        model_name = self._env_value("OPSGRAPH_OPENAI_MODEL")
        decision = self._shared_platform.resolve_remote_mode(
            requested_mode=provider,
            allowed_modes=("auto", "local", "openai"),
            local_mode="local",
            remote_mode="openai",
            has_remote_configuration=api_key is not None and model_name is not None,
            strict_remote_mode="openai",
            strict_missing_error="OPSGRAPH_OPENAI_MODEL",
            auto_fallback_reason="MODEL_PROVIDER_NOT_CONFIGURED",
        )
        if not decision.use_remote:
            return None, decision
        try:
            gateway = _OpenAIResponsesOpsGraphProductGateway(
                model_name=str(model_name),
                api_key=str(api_key),
                base_url=self._env_value("OPSGRAPH_OPENAI_BASE_URL"),
                timeout_seconds=float(self._env_value("OPSGRAPH_OPENAI_TIMEOUT_SECONDS") or 30.0),
            )
            self._last_primary_error = None
        except Exception as exc:
            self._last_primary_error = exc.__class__.__name__
            if provider == "openai":
                raise
            fallback_decision = self._shared_platform.RuntimeModeDecision(
                requested_mode=decision.requested_mode,
                effective_mode="local",
                use_remote=False,
                allow_fallback=True,
                fallback_reason="MODEL_PROVIDER_INIT_FAILED",
            )
            return None, fallback_decision
        return gateway, decision

    def generate(self, *, assembled_prompt) -> Any:
        if self._primary_gateway is None:
            self._last_runtime_fallback_reason = None
            return self._fallback_gateway.generate(assembled_prompt=assembled_prompt)
        try:
            response = self._primary_gateway.generate(assembled_prompt=assembled_prompt)
        except Exception as exc:
            self._last_primary_error = exc.__class__.__name__
            if not self._allow_fallback:
                raise
            self._last_runtime_fallback_reason = "MODEL_PROVIDER_REQUEST_FAILED"
            return self._fallback_gateway.generate(assembled_prompt=assembled_prompt)
        self._last_primary_error = None
        self._last_runtime_fallback_reason = None
        return response

    def describe_capability(self) -> dict[str, object]:
        decision = self._provider_mode_decision or self._shared_platform.RuntimeModeDecision(
            requested_mode=self._requested_provider_mode,
            effective_mode="local" if self._primary_gateway is None else "openai",
            use_remote=self._primary_gateway is not None,
            allow_fallback=self._allow_fallback,
            fallback_reason=(
                "MODEL_PROVIDER_NOT_CONFIGURED"
                if self._primary_gateway is None and self._requested_provider_mode == "auto"
                else None
            ),
        )
        effective_mode = decision.effective_mode
        backend_id = "openai-responses" if decision.effective_mode == "openai" else "heuristic-local"
        fallback_reason = decision.fallback_reason
        if self._last_runtime_fallback_reason is not None:
            effective_mode = "local"
            backend_id = "heuristic-local"
            fallback_reason = self._last_runtime_fallback_reason
        return self._shared_platform.RuntimeCapabilityDescriptor(
            requested_mode=decision.requested_mode,
            effective_mode=effective_mode,
            backend_id=backend_id,
            fallback_reason=fallback_reason,
            details={
                "configured_model": self._configured_model_name,
                "fallback_enabled": self._allow_fallback,
                "fallback_policy_source": self._fallback_policy_source,
                "strict_remote_required": (
                    decision.requested_mode != "local" and not self._allow_fallback
                ),
                "last_primary_error": self._last_primary_error,
            },
        ).as_dict()
