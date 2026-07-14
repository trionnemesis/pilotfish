"""Best-effort Codex model attestation from explicit structured evidence."""

from __future__ import annotations

from typing import Any, Mapping

from runtime.models import Attestation, RuntimeRecordError


def attest_codex(
    configured_model: str,
    *,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> Attestation:
    """Return UNKNOWN unless a caller supplies one structured model observation."""

    if not isinstance(configured_model, str) or not configured_model.strip():
        raise RuntimeRecordError("configured_model must be a non-empty string")
    if runtime_metadata is None:
        return Attestation.unknown(configured_model)
    if not isinstance(runtime_metadata, Mapping):
        raise RuntimeRecordError("runtime_metadata must be a mapping")
    unknown = set(runtime_metadata) - {"model", "source"}
    if unknown:
        raise RuntimeRecordError(
            "runtime_metadata contains unsupported fields: " + ", ".join(sorted(unknown))
        )
    source = runtime_metadata.get("source")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise RuntimeRecordError("runtime_metadata.source must be a non-empty string")
    observed = runtime_metadata.get("model")
    if observed is None:
        return Attestation.unknown(configured_model, source)
    if not isinstance(observed, str) or not observed.strip():
        raise RuntimeRecordError("runtime_metadata.model must be a non-empty string")
    if not isinstance(source, str) or not source.strip():
        raise RuntimeRecordError("observed model requires an evidence source")
    return Attestation(
        configured_model=configured_model,
        observed_model=observed,
        evidence_method=source,
        status="MATCHED" if observed == configured_model else "MISMATCHED",
    )
