"""Deterministic aggregation for non-blocking L2 classifier evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from router.models import canonical_json


SEMANTIC_FIELDS = (
    "task_type",
    "spec_completeness",
    "risk_level",
    "risk_tags",
)


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
    }


def _outcome_key(result: Mapping[str, Any]) -> str:
    envelope = result.get("emitted_envelope")
    if not result.get("schema_valid") or not isinstance(envelope, Mapping):
        return "INVALID"
    projection = {field: envelope.get(field) for field in SEMANTIC_FIELDS}
    return canonical_json(projection)


def _latency_summary(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [
        float(result["latency_ms"])
        for result in results
        if isinstance(result.get("latency_ms"), (int, float))
        and not isinstance(result.get("latency_ms"), bool)
    ]
    if not values:
        return {"observations": 0, "min": None, "max": None, "mean": None}
    return {
        "observations": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(sum(values) / len(values), 3),
    }


def _token_summary(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for field in ("input", "output", "total"):
        values = []
        for result in results:
            usage = result.get("token_usage")
            if not isinstance(usage, Mapping):
                continue
            value = usage.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                values.append(value)
        summary[field] = {
            "observations": len(values),
            "sum": sum(values) if values else None,
        }
    return summary


def _fixture_summary(
    fixture: Mapping[str, Any], results: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    outcomes = Counter(_outcome_key(result) for result in results)
    dominant = max(outcomes.values(), default=0)
    attempted = len(results)
    field_matches = sum(int(result["accepted_field_matches"]) for result in results)
    field_total = sum(int(result["accepted_field_total"]) for result in results)
    route_matches = sum(bool(result.get("route_match")) for result in results)
    return {
        "id": fixture["id"],
        "attempted_runs": attempted,
        "schema_valid": _ratio(
            sum(bool(result.get("schema_valid")) for result in results), attempted
        ),
        "accepted_envelope": _ratio(
            sum(bool(result.get("accepted_envelope")) for result in results), attempted
        ),
        "field_agreement": _ratio(field_matches, field_total),
        "route_agreement": _ratio(route_matches, attempted),
        "variance": {
            "unique_outcomes": len(outcomes),
            "dominant_outcome_runs": dominant,
            "value": round(1 - (dominant / attempted), 6) if attempted else None,
        },
    }


def aggregate_report(
    fixtures: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    *,
    runs_per_fixture: int,
    max_invocations: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Return a stable JSON-ready report without a pass/fail release gate."""

    by_fixture: dict[str, list[Mapping[str, Any]]] = {
        str(fixture["id"]): [] for fixture in fixtures
    }
    for result in results:
        fixture_id = str(result["fixture_id"])
        if fixture_id not in by_fixture:
            raise ValueError(f"result references unknown fixture: {fixture_id}")
        by_fixture[fixture_id].append(result)

    attempted = len(results)
    field_matches = sum(int(result["accepted_field_matches"]) for result in results)
    field_total = sum(int(result["accepted_field_total"]) for result in results)
    security_results = [
        result
        for fixture in fixtures
        if fixture["security_expected"]
        for result in by_fixture[str(fixture["id"])]
    ]
    security_hits = sum(
        bool(result.get("schema_valid"))
        and isinstance(result.get("emitted_envelope"), Mapping)
        and result["emitted_envelope"].get("task_type") == "security"
        for result in security_results
    )

    def unique_metadata(field: str) -> list[str]:
        return sorted(
            {
                str(result[field])
                for result in results
                if isinstance(result.get(field), str) and result[field]
            }
        )

    return {
        "schema_version": "0.1",
        "report_type": "l2-classification",
        "release_gate": "NON_BLOCKING",
        "configuration": {
            "fixture_count": len(fixtures),
            "runs_per_fixture": runs_per_fixture,
            "max_invocations": max_invocations,
            "timeout_seconds": timeout_seconds,
        },
        "summary": {
            "attempted_runs": attempted,
            "schema_valid": _ratio(
                sum(bool(result.get("schema_valid")) for result in results),
                attempted,
            ),
            "accepted_envelope": _ratio(
                sum(bool(result.get("accepted_envelope")) for result in results),
                attempted,
            ),
            "field_agreement": _ratio(field_matches, field_total),
            "route_agreement": _ratio(
                sum(bool(result.get("route_match")) for result in results),
                attempted,
            ),
            "security_recall": _ratio(security_hits, len(security_results)),
            "latency_ms": _latency_summary(results),
            "token_usage": _token_summary(results),
        },
        "fixtures": [
            _fixture_summary(fixture, by_fixture[str(fixture["id"])])
            for fixture in fixtures
        ],
        "metadata": {
            "classifier_sources": unique_metadata("classifier_source"),
            "cli_versions": unique_metadata("cli_version"),
            "models": unique_metadata("model"),
        },
    }
