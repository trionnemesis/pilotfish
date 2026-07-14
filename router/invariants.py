"""Schema-adjacent invariants used by the side-effect-free router."""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


TASK_TYPES = frozenset(
    {"recon", "mechanical", "judgment", "security", "verification"}
)
SPEC_COMPLETENESS = frozenset({"fully_specified", "partial", "ambiguous"})
RISK_LEVELS = frozenset({"low", "medium", "high"})
CLASSIFICATION_SOURCES = frozenset({"manual", "rule", "llm"})
ROLE_TYPES = frozenset({"control_plane", "leaf"})
MODEL_ALIASES = frozenset({"best", "haiku", "sonnet", "opus"})
EFFORT_LEVELS = frozenset({"low", "medium", "high"})

REQUIRED_ROLES = frozenset(
    {
        "orchestrator",
        "scout",
        "Explore",
        "mech-executor",
        "executor",
        "senior-executor",
        "verifier",
        "security-executor",
    }
)
LEAF_ROLES = REQUIRED_ROLES - {"orchestrator"}
EXECUTION_ROLES = frozenset(
    {"mech-executor", "executor", "senior-executor", "security-executor"}
)
EXECUTION_TIERS = {
    "mech-executor": 1,
    "executor": 2,
    "senior-executor": 3,
}
EXPECTED_ROLE_BINDINGS = {
    "orchestrator": ("control_plane", "best", "high", True),
    "scout": ("leaf", "haiku", "low", False),
    "Explore": ("leaf", "haiku", "low", False),
    "mech-executor": ("leaf", "sonnet", "low", False),
    "executor": ("leaf", "sonnet", "high", False),
    "senior-executor": ("leaf", "opus", "high", False),
    "verifier": ("leaf", "opus", "medium", False),
    "security-executor": ("leaf", "opus", "high", False),
}

_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "parent_task_id",
        "task_type",
        "spec_completeness",
        "risk_level",
        "risk_tags",
        "failure_count",
        "classification_source",
        "classification_evidence",
    }
)
_ROLE_FIELDS = frozenset(
    {
        "role_type",
        "model_alias",
        "effort",
        "allowed_tools",
        "disallowed_tools",
        "can_spawn",
        "model_binding_source",
    }
)


class InvariantViolation(ValueError):
    """Raised when canonical routing state violates an invariant."""


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation(f"{field} must be a non-empty string")
    return value


def _string_sequence(value: Any, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise InvariantViolation(f"{field} must be an array")
    result = []
    for index, item in enumerate(value):
        result.append(_non_empty_string(item, f"{field}[{index}]"))
    if len(result) != len(set(result)):
        raise InvariantViolation(f"{field} must not contain duplicates")
    return result


def validate_envelope(
    envelope: Any, history: Sequence[Mapping[str, Any]] = ()
) -> Dict[str, Any]:
    """Validate and copy a complete Task Envelope plus monotonic history."""

    if not isinstance(envelope, Mapping):
        raise InvariantViolation("envelope must be an object")
    fields = frozenset(envelope)
    missing = _ENVELOPE_FIELDS - fields
    extra = fields - _ENVELOPE_FIELDS
    if missing:
        raise InvariantViolation(f"envelope missing fields: {sorted(missing)}")
    if extra:
        raise InvariantViolation(f"envelope has unknown fields: {sorted(extra)}")

    if envelope["schema_version"] != "0.1":
        raise InvariantViolation("schema_version must be '0.1'")
    task_id = _non_empty_string(envelope["task_id"], "task_id")
    parent_task_id = envelope["parent_task_id"]
    if parent_task_id is not None:
        _non_empty_string(parent_task_id, "parent_task_id")
        if parent_task_id == task_id:
            raise InvariantViolation("parent_task_id must differ from task_id")
    if envelope["task_type"] not in TASK_TYPES:
        raise InvariantViolation("task_type is outside the closed enum")
    if envelope["spec_completeness"] not in SPEC_COMPLETENESS:
        raise InvariantViolation("spec_completeness is outside the closed enum")
    if envelope["risk_level"] not in RISK_LEVELS:
        raise InvariantViolation("risk_level is outside the closed enum")
    risk_tags = _string_sequence(envelope["risk_tags"], "risk_tags")
    failure_count = envelope["failure_count"]
    if (
        not isinstance(failure_count, int)
        or isinstance(failure_count, bool)
        or failure_count < 0
    ):
        raise InvariantViolation("failure_count must be a non-negative integer")
    source = envelope["classification_source"]
    if source not in CLASSIFICATION_SOURCES:
        raise InvariantViolation("classification_source is outside the closed enum")
    evidence = _non_empty_string(
        envelope["classification_evidence"], "classification_evidence"
    )
    if source == "rule" and not evidence.startswith("rule:"):
        raise InvariantViolation("rule classification evidence must start with 'rule:'")

    normalized_history = validate_history(history)
    prior_failure_count = -1
    for record in normalized_history:
        if _history_task_id(record) != task_id:
            continue
        snapshot_count = _history_failure_count(record)
        if snapshot_count is None:
            continue
        if snapshot_count < prior_failure_count:
            raise InvariantViolation("same-task history failure_count must be monotonic")
        prior_failure_count = snapshot_count
    if prior_failure_count > failure_count:
        raise InvariantViolation(
            "envelope failure_count must be at least the latest same-task history value"
        )

    result = copy.deepcopy(dict(envelope))
    result["risk_tags"] = risk_tags
    return result


def validate_history(history: Any) -> Tuple[Dict[str, Any], ...]:
    if isinstance(history, (str, bytes, Mapping)) or not isinstance(history, Sequence):
        raise InvariantViolation("history must be an array of records")
    normalized = []
    last_sequence: Dict[str, int] = {}
    for index, raw_record in enumerate(history):
        if not isinstance(raw_record, Mapping):
            raise InvariantViolation(f"history[{index}] must be an object")
        record = copy.deepcopy(dict(raw_record))
        snapshot = record.get("envelope_snapshot")
        if isinstance(snapshot, Mapping):
            snapshot_task_id = snapshot.get("task_id")
            if (
                record.get("task_id") is not None
                and snapshot_task_id is not None
                and record["task_id"] != snapshot_task_id
            ):
                raise InvariantViolation(
                    f"history[{index}] task_id differs from its envelope snapshot"
                )
            if (
                "failure_count" in record
                and "failure_count" in snapshot
                and record["failure_count"] != snapshot["failure_count"]
            ):
                raise InvariantViolation(
                    f"history[{index}] failure_count differs from its envelope snapshot"
                )
        task_id = _history_task_id(record)
        if task_id is None:
            raise InvariantViolation(f"history[{index}] must identify task_id")
        _non_empty_string(task_id, f"history[{index}].task_id")
        role = record.get("role_invoked", record.get("role"))
        if role is not None and role not in LEAF_ROLES:
            raise InvariantViolation(f"history[{index}] has an unknown role")
        execution_status = record.get("execution_status")
        if execution_status is not None and execution_status not in {
            "SUCCEEDED",
            "FAILED",
            "BLOCKED",
            "CANCELLED",
            "INVALIDATED",
        }:
            raise InvariantViolation(
                f"history[{index}] has an invalid execution_status"
            )
        sequence = record.get("sequence")
        if sequence is not None:
            if (
                not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence < 0
            ):
                raise InvariantViolation(
                    f"history[{index}].sequence must be a non-negative integer"
                )
            previous = last_sequence.get(task_id)
            if previous is not None and sequence <= previous:
                raise InvariantViolation(
                    f"history[{index}].sequence must increase for the task"
                )
            last_sequence[task_id] = sequence
        snapshot_count = _history_failure_count(record)
        if snapshot_count is not None and (
            not isinstance(snapshot_count, int)
            or isinstance(snapshot_count, bool)
            or snapshot_count < 0
        ):
            raise InvariantViolation(
                f"history[{index}] failure_count must be a non-negative integer"
            )
        normalized.append(record)
    return tuple(normalized)


def _history_task_id(record: Mapping[str, Any]) -> Optional[str]:
    task_id = record.get("task_id")
    if task_id is None and isinstance(record.get("envelope_snapshot"), Mapping):
        task_id = record["envelope_snapshot"].get("task_id")
    return task_id


def _history_failure_count(record: Mapping[str, Any]) -> Optional[int]:
    if "failure_count" in record:
        return record["failure_count"]
    snapshot = record.get("envelope_snapshot")
    if isinstance(snapshot, Mapping):
        return snapshot.get("failure_count")
    return None


def history_role(record: Mapping[str, Any]) -> Optional[str]:
    return record.get("role_invoked", record.get("role"))


def same_task_history(
    task_id: str, history: Sequence[Mapping[str, Any]]
) -> Tuple[Mapping[str, Any], ...]:
    return tuple(record for record in history if _history_task_id(record) == task_id)


def validate_registry(registry: Any) -> Dict[str, Dict[str, Any]]:
    """Validate either a whole routing config or its ``roles`` mapping."""

    if not isinstance(registry, Mapping):
        raise InvariantViolation("registry must be an object")
    roles_value = registry.get("roles", registry)
    if not isinstance(roles_value, Mapping):
        raise InvariantViolation("registry roles must be an object")
    role_names = frozenset(roles_value)
    if role_names != REQUIRED_ROLES:
        missing = sorted(REQUIRED_ROLES - role_names)
        extra = sorted(role_names - REQUIRED_ROLES)
        raise InvariantViolation(
            f"registry role inventory mismatch; missing={missing}, extra={extra}"
        )

    roles: Dict[str, Dict[str, Any]] = {}
    for name, raw_definition in roles_value.items():
        if not isinstance(raw_definition, Mapping):
            raise InvariantViolation(f"role {name} must be an object")
        fields = frozenset(raw_definition)
        if fields != _ROLE_FIELDS:
            raise InvariantViolation(f"role {name} has an invalid field set")
        definition = copy.deepcopy(dict(raw_definition))
        if definition["role_type"] not in ROLE_TYPES:
            raise InvariantViolation(f"role {name} has invalid role_type")
        if definition["model_alias"] not in MODEL_ALIASES:
            raise InvariantViolation(f"role {name} has invalid model_alias")
        if definition["effort"] not in EFFORT_LEVELS:
            raise InvariantViolation(f"role {name} has invalid effort")
        definition["allowed_tools"] = _string_sequence(
            definition["allowed_tools"], f"roles.{name}.allowed_tools"
        )
        definition["disallowed_tools"] = _string_sequence(
            definition["disallowed_tools"], f"roles.{name}.disallowed_tools"
        )
        if not isinstance(definition["can_spawn"], bool):
            raise InvariantViolation(f"role {name} can_spawn must be boolean")
        if definition["model_binding_source"] != "role_registry":
            raise InvariantViolation(f"role {name} model binding has another owner")
        roles[name] = definition

    if roles["orchestrator"]["role_type"] != "control_plane":
        raise InvariantViolation("orchestrator must be the control plane")
    if not roles["orchestrator"]["can_spawn"]:
        raise InvariantViolation("orchestrator must be able to delegate")
    for name in LEAF_ROLES:
        if roles[name]["role_type"] != "leaf" or roles[name]["can_spawn"]:
            raise InvariantViolation(f"leaf role {name} must not spawn")
    for name in ("scout", "Explore"):
        allowed = set(roles[name]["allowed_tools"])
        if not allowed or not allowed <= {"Read", "Glob", "Grep"}:
            raise InvariantViolation(f"role {name} needs a read-only positive allowlist")
    for name in EXECUTION_ROLES:
        if not {"Agent", "Workflow"} <= set(roles[name]["disallowed_tools"]):
            raise InvariantViolation(f"role {name} must forbid child delegation tools")
    if not {"Write", "Edit", "NotebookEdit", "Agent", "Workflow"} <= set(
        roles["verifier"]["disallowed_tools"]
    ):
        raise InvariantViolation("verifier must forbid write and delegation tools")
    if roles["security-executor"]["model_alias"] != "opus":
        raise InvariantViolation("security-executor must remain on the Opus tier")
    for name, expected in EXPECTED_ROLE_BINDINGS.items():
        actual = (
            roles[name]["role_type"],
            roles[name]["model_alias"],
            roles[name]["effort"],
            roles[name]["can_spawn"],
        )
        if actual != expected:
            raise InvariantViolation(f"role {name} binding differs from v0.1")
    return roles
