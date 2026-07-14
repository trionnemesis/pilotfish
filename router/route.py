"""Pure canonical routing decision function."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .config import canonical_config_snapshot
from .escalation import (
    apply_no_downgrade,
    consecutive_verifier_runtime_failures,
    ladder_decision,
)
from .invariants import (
    InvariantViolation,
    same_task_history,
    validate_envelope,
    validate_history,
    validate_registry,
)
from .models import RoutingDecision


def effective_risk(envelope: Mapping[str, Any]) -> str:
    """Return declared risk with the built-in migration floor applied."""

    normalized = validate_envelope(envelope)
    if "migration" in normalized["risk_tags"]:
        return "high"
    return normalized["risk_level"]


def route(
    envelope: Any,
    history: Sequence[Mapping[str, Any]] = (),
    registry: Optional[Mapping[str, Any]] = None,
) -> RoutingDecision:
    """Return a deterministic decision without I/O or input mutation."""

    try:
        normalized_history = validate_history(history)
        normalized = validate_envelope(envelope, normalized_history)
    except InvariantViolation:
        return RoutingDecision("REFINE", None, "invalid_envelope")

    try:
        roles = validate_registry(
            canonical_config_snapshot() if registry is None else registry
        )
    except InvariantViolation:
        return RoutingDecision("REFINE", None, "invalid_registry")

    task_type = normalized["task_type"]
    completeness = normalized["spec_completeness"]
    task_history = same_task_history(normalized["task_id"], normalized_history)

    if task_type != "security" and completeness == "ambiguous":
        return RoutingDecision("REFINE", None, "ambiguous_spec")
    if completeness == "partial" and task_type in {
        "mechanical",
        "judgment",
        "verification",
    }:
        return RoutingDecision("REFINE", None, "partial_spec")

    risk = (
        "high"
        if "migration" in normalized["risk_tags"]
        else normalized["risk_level"]
    )
    verifier_failures = (
        consecutive_verifier_runtime_failures(task_history)
        if task_type == "verification"
        else 0
    )
    decision = ladder_decision(
        task_type,
        risk,
        normalized["failure_count"],
        verifier_failures,
    )
    decision = apply_no_downgrade(decision, task_type, task_history)
    if decision.role is not None and decision.role not in roles:
        return RoutingDecision("REFINE", None, "invalid_registry")
    return decision
