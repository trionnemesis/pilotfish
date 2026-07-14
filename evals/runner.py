"""Run deterministic L1 routing fixtures without network or model access."""

from __future__ import annotations

import argparse
import copy
import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch


DEFAULT_FIXTURES = Path(__file__).with_name("l1-routing.yaml")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RouteFunction = Callable[..., Any]

if __package__ in {None, ""} and str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


class FixtureFormatError(ValueError):
    """Raised when an L1 fixture document has an invalid harness shape."""


class OfflineViolation(RuntimeError):
    """Raised when deterministic routing attempts an external side effect."""


def _deny_network(*_args: Any, **_kwargs: Any) -> None:
    raise OfflineViolation("network access is disabled during L1 routing")


def _deny_subprocess(*_args: Any, **_kwargs: Any) -> None:
    raise OfflineViolation("subprocess access is disabled during L1 routing")


_SOCKET_SIDE_EFFECT_METHODS = (
    "connect",
    "connect_ex",
    "send",
    "sendall",
    "sendto",
    "sendmsg",
    "sendfile",
)
_OS_PROCESS_METHODS = (
    "system",
    "popen",
    "fork",
    "forkpty",
    "startfile",
    "posix_spawn",
    "posix_spawnp",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
)


@contextmanager
def _offline_route_guard() -> Any:
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket, "create_connection", _deny_network))
        for method_name in _SOCKET_SIDE_EFFECT_METHODS:
            if hasattr(socket.socket, method_name):
                stack.enter_context(
                    patch.object(socket.socket, method_name, _deny_network)
                )
        stack.enter_context(patch.object(subprocess, "Popen", _deny_subprocess))
        for method_name in _OS_PROCESS_METHODS:
            if hasattr(os, method_name):
                stack.enter_context(
                    patch.object(os, method_name, _deny_subprocess)
                )
        yield


def load_suite(path: Path) -> dict[str, Any]:
    """Load JSON-compatible YAML and validate the runner-level contract."""

    document = json.loads(path.read_text(encoding="utf-8"))
    from router.schema import SchemaValidationError, validate_schema

    try:
        validate_schema("eval-fixture", document)
    except SchemaValidationError as error:
        raise FixtureFormatError(str(error)) from error
    if not isinstance(document, dict):
        raise FixtureFormatError("fixture document must be an object")
    if document.get("schema_version") != "0.1":
        raise FixtureFormatError("schema_version must be '0.1'")

    fixtures = document.get("fixtures")
    if not isinstance(fixtures, list):
        raise FixtureFormatError("fixtures must be an array")

    seen_ids: set[str] = set()
    for index, fixture in enumerate(fixtures):
        if not isinstance(fixture, dict):
            raise FixtureFormatError(f"fixtures[{index}] must be an object")
        fixture_id = fixture.get("id")
        if not isinstance(fixture_id, str) or not fixture_id:
            raise FixtureFormatError(f"fixtures[{index}].id must be a string")
        if fixture_id in seen_ids:
            raise FixtureFormatError(f"duplicate fixture id: {fixture_id}")
        seen_ids.add(fixture_id)
        if not isinstance(fixture.get("envelope"), dict):
            raise FixtureFormatError(f"{fixture_id}.envelope must be an object")
        if "history" in fixture and not isinstance(fixture["history"], list):
            raise FixtureFormatError(f"{fixture_id}.history must be an array")
        expected = fixture.get("expected")
        if not isinstance(expected, dict):
            raise FixtureFormatError(f"{fixture_id}.expected must be an object")
        if not isinstance(expected.get("action"), str):
            raise FixtureFormatError(f"{fixture_id}.expected.action must be a string")
        role = expected.get("role")
        if role is not None and not isinstance(role, str):
            raise FixtureFormatError(
                f"{fixture_id}.expected.role must be a string or null"
            )
        forbidden = expected.get("forbidden_roles", [])
        if not isinstance(forbidden, list) or not all(
            isinstance(item, str) for item in forbidden
        ):
            raise FixtureFormatError(
                f"{fixture_id}.expected.forbidden_roles must be an array of strings"
            )

    return document


def _canonical_route() -> RouteFunction:
    from router.route import route

    return route


def _decision_mapping(decision: Any) -> dict[str, Any]:
    serialized = decision.to_dict()
    if not isinstance(serialized, Mapping):
        raise TypeError("RoutingDecision.to_dict() must return a mapping")
    return dict(serialized)


def evaluate_suite(
    suite: Mapping[str, Any], route_fn: RouteFunction | None = None
) -> dict[str, Any]:
    """Evaluate a loaded suite and return a stable, concise JSON-ready report."""

    with _offline_route_guard():
        selected_route = route_fn or _canonical_route()
    failures: list[dict[str, Any]] = []
    fixtures: Sequence[Mapping[str, Any]] = suite["fixtures"]

    for fixture in fixtures:
        fixture_id = str(fixture["id"])
        errors: list[dict[str, Any]] = []
        try:
            with _offline_route_guard():
                decision = selected_route(
                    copy.deepcopy(fixture["envelope"]),
                    history=tuple(copy.deepcopy(fixture.get("history", []))),
                    registry=None,
                )
                actual = _decision_mapping(decision)
        except Exception as error:  # Report one fixture failure; keep the suite running.
            errors.append(
                {
                    "field": "route",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            actual = {}

        expected = fixture["expected"]
        compared_fields = ["action", "role"]
        if "reason_code" in expected:
            compared_fields.append("reason_code")
        for field in compared_fields:
            if actual.get(field) != expected.get(field):
                errors.append(
                    {
                        "field": field,
                        "expected": expected.get(field),
                        "actual": actual.get(field),
                    }
                )

        forbidden_roles = expected.get("forbidden_roles", [])
        if actual.get("role") in forbidden_roles:
            errors.append(
                {
                    "field": "forbidden_roles",
                    "actual": actual.get("role"),
                }
            )

        if errors:
            failures.append({"id": fixture_id, "errors": errors})

    total = len(fixtures)
    failed = len(failures)
    return {
        "schema_version": suite["schema_version"],
        "total": total,
        "passed": total - failed,
        "failed": failed,
        "failures": failures,
    }


def run(path: Path, route_fn: RouteFunction | None = None) -> dict[str, Any]:
    """Load and evaluate one fixture document."""

    return evaluate_suite(load_suite(path), route_fn=route_fn)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fixtures",
        nargs="?",
        type=Path,
        default=DEFAULT_FIXTURES,
        help="JSON-compatible L1 fixture document",
    )
    args = parser.parse_args(argv)

    try:
        report = run(args.fixtures)
    except (FixtureFormatError, json.JSONDecodeError, OSError) as error:
        report = {
            "schema_version": "0.1",
            "total": 0,
            "passed": 0,
            "failed": 1,
            "failures": [
                {
                    "id": "fixture_document",
                    "errors": [
                        {
                            "field": "document",
                            "error": f"{type(error).__name__}: {error}",
                        }
                    ],
                }
            ],
        }
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 2

    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
