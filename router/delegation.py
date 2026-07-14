"""Validated deterministic delegation contract."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Mapping, Optional, Sequence

from .config import canonical_config_snapshot
from .invariants import (
    InvariantViolation,
    LEAF_ROLES,
    validate_envelope,
    validate_history,
    validate_registry,
)
from .models import RunHandle, canonical_json
from .route import route
from .schema import SchemaValidationError, validate_schema


_FIELDS = frozenset(
    {
        "objective",
        "constraints",
        "done_criteria",
        "allowed_paths",
        "forbidden_paths",
        "context_refs",
    }
)
_REQUIRED_FIELDS = frozenset({"objective", "constraints", "done_criteria"})
_TASK_TYPE_ROLES = {
    "recon": frozenset({"scout", "Explore"}),
    "mechanical": frozenset({"mech-executor", "executor", "senior-executor"}),
    "judgment": frozenset({"executor", "senior-executor"}),
    "security": frozenset({"security-executor"}),
    "verification": frozenset({"verifier"}),
}


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise InvariantViolation(f"{field} must be an array")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise InvariantViolation(f"{field}[{index}] must be a non-empty string")
        result.append(item)
    if len(result) != len(set(result)):
        raise InvariantViolation(f"{field} must not contain duplicates")
    return result


def validate_delegation_spec(
    spec: Any, task_type: Optional[str] = None
) -> Dict[str, Any]:
    """Validate and normalize the bounded Delegation Spec."""

    if not isinstance(spec, Mapping):
        raise InvariantViolation("delegation spec must be an object")
    fields = frozenset(spec)
    missing = _REQUIRED_FIELDS - fields
    extra = fields - _FIELDS
    if missing:
        raise InvariantViolation(f"delegation spec missing fields: {sorted(missing)}")
    if extra:
        raise InvariantViolation(f"delegation spec has unknown fields: {sorted(extra)}")

    objective = spec["objective"]
    if not isinstance(objective, str) or not objective.strip():
        raise InvariantViolation("objective must be a non-empty string")
    constraints = _strings(spec["constraints"], "constraints")
    done_criteria = _strings(spec["done_criteria"], "done_criteria")
    allowed_paths = _strings(spec.get("allowed_paths", []), "allowed_paths")
    forbidden_paths = _strings(spec.get("forbidden_paths", []), "forbidden_paths")
    context_refs = _strings(spec.get("context_refs", []), "context_refs")
    if not allowed_paths and not forbidden_paths:
        raise InvariantViolation(
            "delegation scope needs allowed_paths or forbidden_paths"
        )
    if set(allowed_paths) & set(forbidden_paths):
        raise InvariantViolation("the same path cannot be both allowed and forbidden")
    if task_type == "mechanical" and (not constraints or not done_criteria):
        raise InvariantViolation(
            "mechanical delegation needs constraints and done_criteria"
        )
    try:
        validate_schema("delegation-spec", spec)
    except (SchemaValidationError, OSError) as error:
        raise InvariantViolation(f"invalid delegation spec: {error}") from error

    return {
        "objective": objective,
        "constraints": constraints,
        "done_criteria": done_criteria,
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "context_refs": context_refs,
    }


def delegate(
    role: str,
    spec: Any,
    envelope: Any,
    registry: Optional[Mapping[str, Any]] = None,
    *,
    history: Sequence[Mapping[str, Any]] = (),
) -> RunHandle:
    """Bind a canonical envelope to a stable, content-minimizing run handle."""

    roles = validate_registry(
        canonical_config_snapshot() if registry is None else registry
    )
    if role not in LEAF_ROLES or role not in roles:
        raise InvariantViolation("delegation role must be a registered leaf role")
    normalized_history = validate_history(history)
    normalized_envelope = validate_envelope(envelope, normalized_history)
    task_type = normalized_envelope["task_type"]
    if role not in _TASK_TYPE_ROLES[task_type]:
        raise InvariantViolation(
            f"role {role} is incompatible with task_type {task_type}"
        )
    decision = route(
        normalized_envelope,
        history=normalized_history,
        registry=roles,
    )
    if decision.action != "DELEGATE" or decision.role != role:
        raise InvariantViolation(
            "delegation role must match canonical route decision; "
            f"requested={role}, decision={decision.action}/{decision.role}"
        )
    normalized = validate_delegation_spec(spec, task_type=task_type)
    digest = hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()
    definition = roles[role]
    return RunHandle(
        task_id=normalized_envelope["task_id"],
        role=role,
        spec_ref=f"sha256:{digest}",
        model_alias=definition["model_alias"],
        effort=definition["effort"],
    )
