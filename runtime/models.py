"""Immutable runtime evidence models and schema-shaped record builders."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from router.invariants import validate_envelope
from router.schema import validate_schema


ATTESTATION_STATUSES = frozenset(
    {"MATCHED", "MISMATCHED", "UNKNOWN", "NOT_APPLICABLE"}
)
EXECUTION_STATUSES = frozenset(
    {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED", "INVALIDATED"}
)
VERIFIER_VERDICTS = frozenset({"CONFIRMED", "REFUTED"})


class RuntimeRecordError(ValueError):
    """Raised when runtime evidence is internally contradictory."""


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeRecordError(f"{field} must be a non-empty string")
    return value


def _nullable_count(value: Any, field: str) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeRecordError(f"{field} must be null or a non-negative integer")
    return value


@dataclass(frozen=True)
class Attestation:
    """Configured claim plus best-effort runtime observation."""

    configured_model: str
    observed_model: Optional[str]
    evidence_method: Optional[str]
    status: str
    override_present: bool = False

    def __post_init__(self) -> None:
        _non_empty(self.configured_model, "configured_model")
        if self.status not in ATTESTATION_STATUSES:
            raise RuntimeRecordError("attestation status is outside the closed enum")
        if not isinstance(self.override_present, bool):
            raise RuntimeRecordError("override_present must be boolean")
        if self.evidence_method is not None:
            _non_empty(self.evidence_method, "evidence_method")
        if self.status in {"MATCHED", "MISMATCHED"}:
            _non_empty(self.observed_model, "observed_model")
            _non_empty(self.evidence_method, "evidence_method")
        elif self.observed_model is not None:
            raise RuntimeRecordError(
                "UNKNOWN and NOT_APPLICABLE must not claim an observed model"
            )
        if self.status == "MATCHED" and self.observed_model != self.configured_model:
            raise RuntimeRecordError("MATCHED requires equal configured and observed models")
        if (
            self.status == "MISMATCHED"
            and not self.override_present
            and self.observed_model == self.configured_model
        ):
            raise RuntimeRecordError("MISMATCHED requires conflicting evidence")

    @classmethod
    def unknown(cls, configured_model: str, method: Optional[str] = None) -> "Attestation":
        return cls(configured_model, None, method, "UNKNOWN")

    @property
    def invalidates_run(self) -> bool:
        return self.status == "MISMATCHED"

    @property
    def evidence_level(self) -> str:
        if self.override_present:
            return "configured"
        if self.status in {"UNKNOWN", "NOT_APPLICABLE"}:
            return "unknown"
        return "observed"

    def to_record_fields(self) -> dict[str, Any]:
        return {
            "model_claimed": self.configured_model,
            "model_attested": self.observed_model,
            "attestation_method": self.evidence_method,
            "attestation_status": self.status,
        }


@dataclass(frozen=True)
class TokenUsage:
    input: Optional[int] = None
    output: Optional[int] = None
    total: Optional[int] = None

    def __post_init__(self) -> None:
        _nullable_count(self.input, "token_usage.input")
        _nullable_count(self.output, "token_usage.output")
        _nullable_count(self.total, "token_usage.total")

    def to_dict(self) -> dict[str, Optional[int]]:
        return {"input": self.input, "output": self.output, "total": self.total}


def failure_updated_envelope(
    envelope: Mapping[str, Any],
    *,
    execution_status: str,
    verifier_verdict: Optional[str] = None,
    blocked_is_misroute_or_contradiction: bool = False,
    verifier_runtime_failure: bool = False,
    attestation_status: str = "UNKNOWN",
) -> dict[str, Any]:
    """Return a copied envelope with the canonical failure event applied once."""

    if execution_status not in EXECUTION_STATUSES:
        raise RuntimeRecordError("execution status is outside the closed enum")
    if verifier_verdict is not None and verifier_verdict not in VERIFIER_VERDICTS:
        raise RuntimeRecordError("verifier verdict is outside the closed enum")
    if attestation_status not in ATTESTATION_STATUSES:
        raise RuntimeRecordError("attestation status is outside the closed enum")
    if verifier_runtime_failure and verifier_verdict is not None:
        raise RuntimeRecordError("a verifier runtime failure cannot carry a verdict")

    updated = validate_envelope(envelope)
    increment = False
    if attestation_status != "MISMATCHED":
        increment = verifier_verdict == "REFUTED" or (
            not verifier_runtime_failure
            and (
                execution_status == "FAILED"
                or (
                    execution_status == "BLOCKED"
                    and blocked_is_misroute_or_contradiction
                )
            )
        )
    if increment:
        updated["failure_count"] += 1
    return updated


def build_record(
    *,
    record_id: str,
    sequence: int,
    timestamp: str,
    envelope_snapshot: Mapping[str, Any],
    role_invoked: str,
    model_claimed: str,
    execution_status: str,
    attestation: Optional[Attestation] = None,
    token_usage: Optional[TokenUsage | Mapping[str, Any]] = None,
    latency_ms: Optional[int] = None,
    verifier_verdict: Optional[str] = None,
    escalated_from: Optional[str] = None,
    supersedes_record_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build and validate one canonical ledger record without writing it."""

    evidence = attestation or Attestation.unknown(model_claimed)
    if evidence.configured_model != model_claimed:
        raise RuntimeRecordError("attestation claim differs from model_claimed")
    if execution_status not in EXECUTION_STATUSES:
        raise RuntimeRecordError("execution status is outside the closed enum")
    if isinstance(token_usage, Mapping):
        if set(token_usage) != {"input", "output", "total"}:
            raise RuntimeRecordError("token_usage must contain exactly input/output/total")
        usage = TokenUsage(
            input=token_usage.get("input"),
            output=token_usage.get("output"),
            total=token_usage.get("total"),
        )
    elif token_usage is None:
        usage = TokenUsage()
    elif isinstance(token_usage, TokenUsage):
        usage = token_usage
    else:
        raise RuntimeRecordError("token_usage must be a TokenUsage or mapping")

    status = "INVALIDATED" if evidence.invalidates_run else execution_status
    document: dict[str, Any] = {
        "record_id": record_id,
        "task_id": envelope_snapshot.get("task_id"),
        "sequence": sequence,
        "timestamp": timestamp,
        "envelope_snapshot": copy.deepcopy(dict(envelope_snapshot)),
        "role_invoked": role_invoked,
        **evidence.to_record_fields(),
        "token_usage": usage.to_dict(),
        "latency_ms": _nullable_count(latency_ms, "latency_ms"),
        "execution_status": status,
        "verifier_verdict": verifier_verdict,
        "escalated_from": escalated_from,
        "supersedes_record_id": supersedes_record_id,
    }
    validate_schema("ledger-record", document)
    return copy.deepcopy(document)
