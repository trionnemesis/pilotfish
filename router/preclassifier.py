"""Deterministic, structured-signal-only preclassification."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Set


NO_MATCH = "NO_MATCH"
CONFLICT = "CONFLICT"

_RULE_ORDER = (
    "read_only_lookup",
    "verification_only",
    "security_sensitive",
    "structured_migration",
)
_TASK_TYPE_RULES = {
    "read_only_lookup": "recon",
    "verification_only": "verification",
    "security_sensitive": "security",
}


def preclassify(context: Any) -> Any:
    """Return a provable partial envelope, ``NO_MATCH``, or ``CONFLICT``.

    Free-form prompt text is intentionally ignored. Callers must provide exact
    structured facts or a list of already-proven rule identifiers.
    """

    if not isinstance(context, Mapping):
        return NO_MATCH

    matched: Set[str] = set()
    provided = context.get("proven_rule_ids", ())
    if isinstance(provided, (list, tuple)):
        matched.update(rule for rule in provided if rule in _RULE_ORDER)

    operation = context.get("operation")
    if operation in _TASK_TYPE_RULES:
        matched.add(operation)
    for rule_id in _TASK_TYPE_RULES:
        if context.get(rule_id) is True:
            matched.add(rule_id)

    risk_tags = context.get("risk_tags")
    if isinstance(risk_tags, (list, tuple)) and "migration" in risk_tags:
        matched.add("structured_migration")

    ordered = [rule for rule in _RULE_ORDER if rule in matched]
    if not ordered:
        return NO_MATCH
    task_types = {
        _TASK_TYPE_RULES[rule]
        for rule in ordered
        if rule in _TASK_TYPE_RULES
    }
    if len(task_types) > 1:
        return CONFLICT

    result: Dict[str, Any] = {
        "classification_source": "rule",
        "classification_evidence": "rule:" + "+".join(ordered),
    }
    if task_types:
        result["task_type"] = next(iter(task_types))
    if "structured_migration" in matched:
        result["risk_tags"] = ["migration"]
    return result
