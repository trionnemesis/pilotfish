"""Dependency-free canonical routing core."""

from .classifier_contract import ClassifierContractResult, classifier_contract
from .config import load_canonical_config
from .delegation import delegate, validate_delegation_spec
from .invariants import InvariantViolation, validate_envelope, validate_registry
from .models import RoutingDecision, RunHandle, canonical_json
from .preclassifier import CONFLICT, NO_MATCH, preclassify
from .route import effective_risk, route
from .schema import SchemaValidationError, validate_schema

__all__ = [
    "CONFLICT",
    "NO_MATCH",
    "ClassifierContractResult",
    "InvariantViolation",
    "RoutingDecision",
    "RunHandle",
    "SchemaValidationError",
    "canonical_json",
    "classifier_contract",
    "delegate",
    "effective_risk",
    "load_canonical_config",
    "preclassify",
    "route",
    "validate_delegation_spec",
    "validate_envelope",
    "validate_registry",
    "validate_schema",
]
