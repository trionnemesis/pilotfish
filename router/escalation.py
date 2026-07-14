"""Task-specific escalation ladders and monotonic history floors."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .invariants import EXECUTION_TIERS, history_role
from .models import RoutingDecision


def ladder_decision(
    task_type: str,
    effective_risk: str,
    failure_count: int,
    verifier_runtime_failures: int = 0,
) -> RoutingDecision:
    if task_type == "recon":
        if failure_count >= 2:
            return _exhausted()
        return RoutingDecision("DELEGATE", "scout", "recon_default")

    if task_type == "mechanical":
        if effective_risk == "low":
            if failure_count <= 1:
                return RoutingDecision(
                    "DELEGATE", "mech-executor", "mechanical_low_risk"
                )
            if failure_count <= 3:
                return RoutingDecision(
                    "DELEGATE", "executor", "mechanical_low_risk"
                )
            if failure_count <= 5:
                return RoutingDecision(
                    "DELEGATE", "senior-executor", "mechanical_low_risk"
                )
            return _exhausted()
        if effective_risk == "medium":
            if failure_count <= 1:
                return RoutingDecision(
                    "DELEGATE", "executor", "mechanical_medium_risk"
                )
            if failure_count <= 3:
                return RoutingDecision(
                    "DELEGATE", "senior-executor", "mechanical_medium_risk"
                )
            return _exhausted()
        if failure_count <= 1:
            return RoutingDecision(
                "DELEGATE", "senior-executor", "mechanical_high_risk"
            )
        return _exhausted()

    if task_type == "judgment":
        if effective_risk in {"low", "medium"}:
            if failure_count <= 1:
                return RoutingDecision(
                    "DELEGATE", "executor", "judgment_standard_risk"
                )
            if failure_count <= 3:
                return RoutingDecision(
                    "DELEGATE", "senior-executor", "judgment_standard_risk"
                )
            return _exhausted()
        if failure_count <= 1:
            return RoutingDecision(
                "DELEGATE", "senior-executor", "judgment_high_risk"
            )
        return _exhausted()

    if task_type == "security":
        if failure_count <= 1:
            return RoutingDecision(
                "DELEGATE", "security-executor", "security_fixed_lane"
            )
        return _exhausted()

    if verifier_runtime_failures >= 2:
        return _exhausted()
    return RoutingDecision("DELEGATE", "verifier", "verification_isolation")


def consecutive_verifier_runtime_failures(
    history: Sequence[Mapping[str, Any]],
) -> int:
    count = 0
    for record in reversed(history):
        if history_role(record) != "verifier":
            break
        if record.get("execution_status") not in {"FAILED", "BLOCKED"}:
            break
        count += 1
    return count


def apply_no_downgrade(
    decision: RoutingDecision,
    task_type: str,
    history: Sequence[Mapping[str, Any]],
) -> RoutingDecision:
    if decision.action != "DELEGATE" or decision.role is None:
        return decision

    prior_roles = [history_role(record) for record in history]
    if "security-executor" in prior_roles:
        if decision.role == "security-executor":
            return decision
        return RoutingDecision("TAKEOVER", None, "history_no_downgrade")

    highest_tier = max(
        (EXECUTION_TIERS.get(role, 0) for role in prior_roles), default=0
    )
    if highest_tier == 0 or decision.role not in EXECUTION_TIERS:
        if highest_tier and task_type == "recon":
            return RoutingDecision("TAKEOVER", None, "history_no_downgrade")
        return decision
    current_tier = EXECUTION_TIERS[decision.role]
    if current_tier >= highest_tier:
        return decision
    promoted_role = {
        1: "mech-executor",
        2: "executor",
        3: "senior-executor",
    }[highest_tier]
    return RoutingDecision("DELEGATE", promoted_role, "history_no_downgrade")


def _exhausted() -> RoutingDecision:
    return RoutingDecision("TAKEOVER", None, "failure_budget_exhausted")
