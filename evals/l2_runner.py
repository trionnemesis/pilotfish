"""Run explicit, non-blocking L2 classifier evaluations."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = Path(__file__).with_name("l2-classification.yaml")
MAX_CAPTURE_CHARS = 262_144
ENVELOPE_FIELDS = frozenset(
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
ROUTE_ACTIONS = frozenset({"DELEGATE", "REFINE", "TAKEOVER", "BLOCK"})

if __package__ in {None, ""} and str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from evals.report import aggregate_report  # noqa: E402
from router.invariants import InvariantViolation, validate_envelope  # noqa: E402
from router.models import canonical_json  # noqa: E402
from router.route import route  # noqa: E402


class L2ConfigurationError(ValueError):
    """Raised before any command runs when L2 configuration is unsafe or invalid."""


def load_suite(path: Path) -> dict[str, Any]:
    """Load and validate a JSON-compatible YAML L2 fixture document."""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != "0.1":
        raise L2ConfigurationError("fixture document must use schema_version '0.1'")
    if set(document) != {"schema_version", "fixtures"}:
        raise L2ConfigurationError("fixture document has an invalid field set")
    fixtures = document.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise L2ConfigurationError("fixtures must be a non-empty array")

    seen: set[str] = set()
    required = {
        "id",
        "task_description",
        "accepted_envelopes",
        "expected_route",
        "security_expected",
    }
    for index, fixture in enumerate(fixtures):
        if not isinstance(fixture, dict) or set(fixture) != required:
            raise L2ConfigurationError(f"fixtures[{index}] has an invalid field set")
        fixture_id = fixture["id"]
        if not isinstance(fixture_id, str) or not fixture_id.strip():
            raise L2ConfigurationError(f"fixtures[{index}].id must be non-empty")
        if fixture_id in seen:
            raise L2ConfigurationError(f"duplicate fixture id: {fixture_id}")
        seen.add(fixture_id)
        if not isinstance(fixture["task_description"], str) or not fixture[
            "task_description"
        ].strip():
            raise L2ConfigurationError(f"{fixture_id}.task_description is invalid")
        accepted = fixture["accepted_envelopes"]
        if not isinstance(accepted, dict) or not accepted:
            raise L2ConfigurationError(f"{fixture_id}.accepted_envelopes is invalid")
        for field, values in accepted.items():
            if field not in ENVELOPE_FIELDS:
                raise L2ConfigurationError(
                    f"{fixture_id}.accepted_envelopes has unknown field {field}"
                )
            if not isinstance(values, list) or not values:
                raise L2ConfigurationError(
                    f"{fixture_id}.accepted_envelopes.{field} must be non-empty"
                )
        expected = fixture["expected_route"]
        if not isinstance(expected, dict) or set(expected) != {"action", "role"}:
            raise L2ConfigurationError(f"{fixture_id}.expected_route is invalid")
        if expected["action"] not in ROUTE_ACTIONS:
            raise L2ConfigurationError(f"{fixture_id}.expected_route.action is invalid")
        if expected["role"] is not None and not isinstance(expected["role"], str):
            raise L2ConfigurationError(f"{fixture_id}.expected_route.role is invalid")
        if not isinstance(fixture["security_expected"], bool):
            raise L2ConfigurationError(f"{fixture_id}.security_expected must be boolean")
    return document


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value[:512]


def _normalize_token_usage(value: Any) -> dict[str, int | None]:
    result: dict[str, int | None] = {"input": None, "output": None, "total": None}
    if not isinstance(value, Mapping):
        return result
    for field in result:
        item = value.get(field)
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            result[field] = item
    return result


def _command_source(command: Sequence[str]) -> str:
    return f"command:{Path(command[0]).name}"


def _invoke(
    command: Sequence[str], request: Mapping[str, Any], timeout_seconds: float
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(command),
            input=canonical_json(request) + "\n",
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "returncode": None,
            "stdout": (error.stdout or "")[:MAX_CAPTURE_CHARS]
            if isinstance(error.stdout, str)
            else "",
            "stderr_present": bool(error.stderr),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "process_error": "command timed out",
        }
    except OSError as error:
        return {
            "returncode": None,
            "stdout": "",
            "stderr_present": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "process_error": f"{type(error).__name__}: {error}",
        }
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[:MAX_CAPTURE_CHARS],
        "stderr_present": bool(completed.stderr),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "process_error": None,
    }


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError):
        return type(left) is type(right) and left == right


def _evaluate_invocation(
    fixture: Mapping[str, Any],
    run_index: int,
    command: Sequence[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = {
        "schema_version": "0.1",
        "fixture_id": fixture["id"],
        "run_index": run_index,
        "task_description": fixture["task_description"],
    }
    invocation = _invoke(command, request, timeout_seconds)
    stdout = invocation["stdout"].strip()
    parsed: Any = None
    parse_error: str | None = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as error:
            parse_error = f"JSONDecodeError: {error.msg}"
    else:
        parse_error = "classifier emitted no JSON"

    wrapper = parsed if isinstance(parsed, Mapping) and "envelope" in parsed else {}
    candidate = wrapper.get("envelope") if wrapper else parsed
    metadata = wrapper.get("metadata", {}) if isinstance(wrapper, Mapping) else {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    source = _normalize_optional_text(wrapper.get("classifier_source")) or _command_source(
        command
    )
    validation_error = parse_error
    normalized: dict[str, Any] | None = None
    if invocation["process_error"] is not None:
        validation_error = invocation["process_error"]
    elif invocation["returncode"] != 0:
        validation_error = f"classifier command exited {invocation['returncode']}"
    elif parse_error is None:
        try:
            normalized = validate_envelope(candidate)
        except InvariantViolation as error:
            validation_error = f"InvariantViolation: {error}"

    accepted = fixture["accepted_envelopes"]
    matches = 0
    if normalized is not None:
        for field, allowed_values in accepted.items():
            if any(_json_equal(normalized.get(field), value) for value in allowed_values):
                matches += 1

    decision = route(normalized).to_dict() if normalized is not None else None
    expected_route = fixture["expected_route"]
    route_match = bool(
        decision is not None
        and decision["action"] == expected_route["action"]
        and decision["role"] == expected_route["role"]
    )
    accepted_envelope = normalized is not None and matches == len(accepted)
    raw_output = stdout if parsed is None and stdout else None
    return {
        "schema_version": "0.1",
        "fixture_id": fixture["id"],
        "run_index": run_index,
        "classifier_source": source,
        "emitted_envelope": candidate,
        "raw_output": raw_output,
        "schema_valid": normalized is not None,
        "validation_error": validation_error,
        "accepted_envelope": accepted_envelope,
        "accepted_field_matches": matches,
        "accepted_field_total": len(accepted),
        "routing_decision": decision,
        "route_match": route_match,
        "latency_ms": invocation["latency_ms"],
        "token_usage": _normalize_token_usage(metadata.get("token_usage")),
        "model": _normalize_optional_text(metadata.get("model")),
        "cli_version": _normalize_optional_text(metadata.get("cli_version")),
        "process_returncode": invocation["returncode"],
        "stderr_present": invocation["stderr_present"],
    }


def run_suite(
    suite: Mapping[str, Any],
    *,
    command: Sequence[str],
    runs: int,
    max_invocations: int,
    output_dir: Path,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Execute configured L2 runs and persist JSONL evidence plus one report."""

    if isinstance(command, (str, bytes)) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise L2ConfigurationError("command must be a non-empty argument array")
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
        raise L2ConfigurationError("runs must be a positive integer")
    if (
        not isinstance(max_invocations, int)
        or isinstance(max_invocations, bool)
        or max_invocations < 1
    ):
        raise L2ConfigurationError("max_invocations must be a positive integer")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise L2ConfigurationError("timeout_seconds must be positive")
    fixtures = suite.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise L2ConfigurationError("suite must contain fixtures")
    required_invocations = len(fixtures) * runs
    if required_invocations > max_invocations:
        raise L2ConfigurationError(
            f"requested {required_invocations} invocations exceeds budget {max_invocations}"
        )
    if output_dir.exists():
        raise L2ConfigurationError("output_dir already exists; refusing to overwrite evidence")

    output_dir.mkdir(parents=True)
    evidence_path = output_dir / "runs.jsonl"
    results: list[dict[str, Any]] = []
    with evidence_path.open("x", encoding="utf-8", newline="\n") as evidence_file:
        for fixture in fixtures:
            for run_index in range(1, runs + 1):
                result = _evaluate_invocation(
                    fixture, run_index, command, float(timeout_seconds)
                )
                results.append(result)
                evidence_file.write(canonical_json(result) + "\n")
                evidence_file.flush()

    report = aggregate_report(
        fixtures,
        results,
        runs_per_fixture=runs,
        max_invocations=max_invocations,
        timeout_seconds=float(timeout_seconds),
    )
    (output_dir / "report.json").write_text(
        canonical_json(report) + "\n", encoding="utf-8", newline="\n"
    )
    return report


def _parse_command(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("command must be a JSON array") from error
    if not isinstance(parsed, list) or not parsed or not all(
        isinstance(part, str) and part for part in parsed
    ):
        raise argparse.ArgumentTypeError("command must be a non-empty JSON string array")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--command-json", type=_parse_command, required=True)
    parser.add_argument("--runs", type=int, required=True)
    parser.add_argument("--max-invocations", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)

    try:
        suite = load_suite(args.fixtures)
        report = run_suite(
            suite,
            command=args.command_json,
            runs=args.runs,
            max_invocations=args.max_invocations,
            output_dir=args.output_dir,
            timeout_seconds=args.timeout_seconds,
        )
    except (L2ConfigurationError, json.JSONDecodeError, OSError) as error:
        print(
            canonical_json(
                {"schema_version": "0.1", "error": f"{type(error).__name__}: {error}"}
            ),
            file=sys.stderr,
        )
        return 2
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
