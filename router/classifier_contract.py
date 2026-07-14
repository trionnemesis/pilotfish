"""Boundary contract for stochastic classifier output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from .invariants import InvariantViolation, validate_envelope
from .models import RoutingDecision


@dataclass(frozen=True)
class ClassifierContractResult:
    envelope: Optional[Dict[str, Any]]
    decision: Optional[RoutingDecision]

    @property
    def accepted(self) -> bool:
        return self.envelope is not None


def classifier_contract(
    candidate: Any, history: Sequence[Mapping[str, Any]] = ()
) -> ClassifierContractResult:
    """Accept a valid complete envelope or require orchestrator refinement."""

    try:
        envelope = validate_envelope(candidate, history)
    except InvariantViolation:
        return ClassifierContractResult(
            envelope=None,
            decision=RoutingDecision("REFINE", None, "invalid_envelope"),
        )
    return ClassifierContractResult(envelope=envelope, decision=None)
