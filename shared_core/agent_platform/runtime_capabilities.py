from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, Mapping


def env_value(name: str, *, env: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if env is None else env
    value = source.get(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_requested_mode(
    value: str | None,
    *,
    allowed_modes: Iterable[str],
    default: str = "auto",
) -> str:
    allowed = {mode.strip().lower() for mode in allowed_modes if mode}
    normalized_default = default.strip().lower()
    normalized_value = (value or normalized_default).strip().lower()
    if normalized_value in allowed:
        return normalized_value
    return normalized_default


@dataclass(frozen=True, slots=True)
class RuntimeModeDecision:
    requested_mode: str
    effective_mode: str
    use_remote: bool
    allow_fallback: bool
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeCapabilityDescriptor:
    requested_mode: str
    effective_mode: str
    backend_id: str
    fallback_reason: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "backend_id": self.backend_id,
            "fallback_reason": self.fallback_reason,
            "details": dict(self.details),
        }


def resolve_remote_mode(
    *,
    requested_mode: str | None,
    allowed_modes: Iterable[str],
    local_mode: str,
    remote_mode: str,
    has_remote_configuration: bool,
    strict_remote_mode: str | None = None,
    strict_missing_error: str | None = None,
    auto_fallback_reason: str | None = None,
) -> RuntimeModeDecision:
    normalized_requested_mode = normalize_requested_mode(
        requested_mode,
        allowed_modes=allowed_modes,
        default="auto",
    )
    normalized_local_mode = local_mode.strip().lower()
    normalized_remote_mode = remote_mode.strip().lower()
    normalized_strict_remote_mode = (
        strict_remote_mode.strip().lower()
        if strict_remote_mode is not None
        else normalized_remote_mode
    )
    if normalized_requested_mode == normalized_local_mode:
        return RuntimeModeDecision(
            requested_mode=normalized_requested_mode,
            effective_mode=normalized_local_mode,
            use_remote=False,
            allow_fallback=False,
            fallback_reason=None,
        )
    if not has_remote_configuration:
        if normalized_requested_mode == normalized_strict_remote_mode and strict_missing_error is not None:
            raise ValueError(strict_missing_error)
        return RuntimeModeDecision(
            requested_mode=normalized_requested_mode,
            effective_mode=normalized_local_mode,
            use_remote=False,
            allow_fallback=True,
            fallback_reason=auto_fallback_reason,
        )
    return RuntimeModeDecision(
        requested_mode=normalized_requested_mode,
        effective_mode=normalized_remote_mode,
        use_remote=True,
        allow_fallback=normalized_requested_mode == "auto",
        fallback_reason=None,
    )
