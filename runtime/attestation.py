"""Best-effort model attestation without cryptographic claims."""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from .models import Attestation, RuntimeRecordError


OVERRIDE_ENV = "CLAUDE_CODE_SUBAGENT_MODEL"
_MODEL_KEYS = ("model", "model_alias", "model_id")


def _metadata_model(metadata: Optional[Mapping[str, Any]], label: str) -> Optional[str]:
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        raise RuntimeRecordError(f"{label} must be a metadata mapping")
    values = []
    for key in _MODEL_KEYS:
        if key not in metadata:
            continue
        value = metadata[key]
        if isinstance(value, str) and value.strip():
            values.append(value)
        elif value is not None:
            raise RuntimeRecordError(f"{label}.{key} must be a non-empty string")
    if len(set(values)) > 1:
        raise RuntimeRecordError(f"{label} contains conflicting model fields")
    return values[0] if values else None


def _override_evidence(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return "<override-present>"


def attest(
    configured_model: str,
    *,
    invocation_metadata: Optional[Mapping[str, Any]] = None,
    environment: Optional[Mapping[str, str]] = None,
    transcript_metadata: Optional[Mapping[str, Any]] = None,
    provider_metadata: Optional[Mapping[str, Any]] = None,
) -> Attestation:
    """Compare configured and observed evidence while detecting global overrides."""

    if not isinstance(configured_model, str) or not configured_model.strip():
        raise RuntimeRecordError("configured_model must be a non-empty string")
    invocation = {} if invocation_metadata is None else invocation_metadata
    if not isinstance(invocation, Mapping):
        raise RuntimeRecordError("invocation_metadata must be a mapping")
    env = os.environ if environment is None else environment
    if not isinstance(env, Mapping):
        raise RuntimeRecordError("environment must be a mapping")

    invocation_override = next((key for key in _MODEL_KEYS if key in invocation), None)
    environment_override = OVERRIDE_ENV in env
    if invocation_override is not None or environment_override:
        values = []
        methods = []
        if invocation_override is not None:
            values.append(_override_evidence(invocation.get(invocation_override)))
            methods.append("invocation-override")
        if environment_override:
            values.append(_override_evidence(env.get(OVERRIDE_ENV)))
            methods.append("environment-override")
        observed = values[0] if len(set(values)) == 1 else "<multiple-overrides>"
        return Attestation(
            configured_model=configured_model,
            observed_model=observed,
            evidence_method="+".join(methods),
            status="MISMATCHED",
            override_present=True,
        )

    transcript_model = _metadata_model(transcript_metadata, "transcript_metadata")
    provider_model = _metadata_model(provider_metadata, "provider_metadata")
    observed = {item for item in (transcript_model, provider_model) if item is not None}
    if not observed:
        return Attestation.unknown(configured_model)
    if len(observed) > 1:
        return Attestation.unknown(configured_model, "conflicting-observation")
    observed_model = observed.pop()
    if transcript_model is not None and provider_model is not None:
        method = "transcript+provider"
    elif transcript_model is not None:
        method = "transcript"
    else:
        method = "provider"
    return Attestation(
        configured_model=configured_model,
        observed_model=observed_model,
        evidence_method=method,
        status="MATCHED" if observed_model == configured_model else "MISMATCHED",
    )
