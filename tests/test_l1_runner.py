from __future__ import annotations

import copy
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evals.runner import FixtureFormatError, evaluate_suite, load_suite, run
from router.models import RoutingDecision
from router.schema import validate_schema


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "l1-routing.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "l1.yml"


class L1RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.suite = load_suite(FIXTURES)

    def test_canonical_fixtures_match_schema_and_router(self) -> None:
        validate_schema("eval-fixture", self.suite)
        report = run(FIXTURES)

        fixture_count = len(self.suite["fixtures"])
        self.assertGreater(fixture_count, 0)
        self.assertEqual(report["total"], fixture_count)
        self.assertEqual(report["passed"], fixture_count)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["failures"], [])

    def test_canonical_fixtures_cover_required_dimensions(self) -> None:
        fixtures = self.suite["fixtures"]
        envelopes = [fixture["envelope"] for fixture in fixtures]

        self.assertEqual(
            {envelope["task_type"] for envelope in envelopes}
            & {"recon", "mechanical", "judgment", "security", "verification"},
            {"recon", "mechanical", "judgment", "security", "verification"},
        )
        self.assertEqual(
            {envelope["spec_completeness"] for envelope in envelopes},
            {"fully_specified", "partial", "ambiguous"},
        )
        self.assertEqual(
            {envelope["risk_level"] for envelope in envelopes}
            & {"low", "medium", "high"},
            {"low", "medium", "high"},
        )
        self.assertTrue(
            any("migration" in envelope.get("risk_tags", []) for envelope in envelopes)
        )

        by_id = {fixture["id"]: fixture for fixture in fixtures}
        required_ids = {
            "security-boundary-f1-partial",
            "verification-isolation",
            "migration-effective-high",
            "partial-mechanical-refine",
            "partial-judgment-refine",
            "partial-verification-refine",
            "ambiguous-non-security-refine",
            "history-no-downgrade-senior",
            "history-no-downgrade-security",
            "invalid-task-type",
            "invalid-risk-level",
            "invalid-negative-failure-count",
            "invalid-non-monotonic-failure-count",
        }
        self.assertLessEqual(required_ids, set(by_id))

        boundary_expectations = {
            "recon-boundary-f1": (1, "DELEGATE", "scout"),
            "recon-boundary-f2": (2, "TAKEOVER", None),
            "mechanical-low-f1": (1, "DELEGATE", "mech-executor"),
            "mechanical-low-f2": (2, "DELEGATE", "executor"),
            "mechanical-low-f3": (3, "DELEGATE", "executor"),
            "mechanical-low-f4": (4, "DELEGATE", "senior-executor"),
            "mechanical-low-f5": (5, "DELEGATE", "senior-executor"),
            "mechanical-low-f6": (6, "TAKEOVER", None),
            "standard-ladder-f1": (1, "DELEGATE", "executor"),
            "standard-ladder-f2": (2, "DELEGATE", "senior-executor"),
            "standard-ladder-f3": (3, "DELEGATE", "senior-executor"),
            "standard-ladder-f4": (4, "TAKEOVER", None),
            "high-ladder-f1": (1, "DELEGATE", "senior-executor"),
            "high-ladder-f2": (2, "TAKEOVER", None),
            "security-boundary-f1-partial": (
                1,
                "DELEGATE",
                "security-executor",
            ),
            "security-boundary-f2": (2, "TAKEOVER", None),
        }
        for fixture_id, expected in boundary_expectations.items():
            with self.subTest(fixture=fixture_id):
                fixture = by_id[fixture_id]
                actual = (
                    fixture["envelope"]["failure_count"],
                    fixture["expected"]["action"],
                    fixture["expected"]["role"],
                )
                self.assertEqual(actual, expected)

        verifier_boundary = {
            fixture_id: (
                len(by_id[fixture_id]["history"]),
                by_id[fixture_id]["expected"]["action"],
            )
            for fixture_id in (
                "verification-runtime-failure-1",
                "verification-runtime-failure-2",
            )
        }
        self.assertEqual(
            verifier_boundary,
            {
                "verification-runtime-failure-1": (1, "DELEGATE"),
                "verification-runtime-failure-2": (2, "TAKEOVER"),
            },
        )

    def test_runner_reports_field_and_forbidden_role_mismatches(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                {
                    "id": "wrong-route",
                    "envelope": {},
                    "expected": {
                        "action": "DELEGATE",
                        "role": "executor",
                        "reason_code": "expected_reason",
                        "forbidden_roles": ["scout"],
                    },
                }
            ],
        }

        def wrong_route(*_args: Any, **_kwargs: Any) -> RoutingDecision:
            return RoutingDecision("DELEGATE", "scout", "actual_reason")

        report = evaluate_suite(suite, route_fn=wrong_route)
        self.assertEqual(report["failed"], 1)
        error_fields = {
            error["field"] for error in report["failures"][0]["errors"]
        }
        self.assertEqual(error_fields, {"role", "reason_code", "forbidden_roles"})

    def test_runner_reports_action_mismatch(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                {
                    "id": "wrong-action",
                    "envelope": {},
                    "expected": {"action": "REFINE", "role": None},
                }
            ],
        }

        def wrong_action(*_args: Any, **_kwargs: Any) -> RoutingDecision:
            return RoutingDecision("TAKEOVER", None, "test")

        report = evaluate_suite(suite, route_fn=wrong_action)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(
            [error["field"] for error in report["failures"][0]["errors"]],
            ["action"],
        )

    def test_runner_blocks_network_and_subprocess(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                {
                    "id": "offline-guard",
                    "envelope": {},
                    "expected": {"action": "REFINE", "role": None},
                }
            ],
        }

        def network_route(*_args: Any, **_kwargs: Any) -> RoutingDecision:
            import socket

            with socket.socket() as connection:
                connection.connect(("127.0.0.1", 9))
            return RoutingDecision("REFINE", None, "unreachable")

        def subprocess_route(*_args: Any, **_kwargs: Any) -> RoutingDecision:
            subprocess.run([sys.executable, "-c", "pass"], check=True)
            return RoutingDecision("REFINE", None, "unreachable")

        def udp_route(*_args: Any, **_kwargs: Any) -> RoutingDecision:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                connection.sendto(b"blocked", ("127.0.0.1", 9))
            return RoutingDecision("REFINE", None, "unreachable")

        def os_system_route(*_args: Any, **_kwargs: Any) -> RoutingDecision:
            os.system("true")
            return RoutingDecision("REFINE", None, "unreachable")

        def os_spawn_route(*_args: Any, **_kwargs: Any) -> RoutingDecision:
            os.spawnv(os.P_WAIT, sys.executable, [sys.executable, "-c", "pass"])
            return RoutingDecision("REFINE", None, "unreachable")

        for route_fn in (
            network_route,
            udp_route,
            subprocess_route,
            os_system_route,
            os_spawn_route,
        ):
            with self.subTest(route=route_fn.__name__):
                report = evaluate_suite(suite, route_fn=route_fn)
                self.assertEqual(report["failed"], 1)
                self.assertIn(
                    "OfflineViolation",
                    report["failures"][0]["errors"][0]["error"],
                )

    def test_runner_guards_decision_serialization(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                {
                    "id": "serialization-guard",
                    "envelope": {},
                    "expected": {"action": "REFINE", "role": None},
                }
            ],
        }

        class SideEffectDecision:
            def to_dict(self) -> dict[str, Any]:
                os.system("true")
                return {"action": "REFINE", "role": None, "reason_code": "test"}

        report = evaluate_suite(suite, route_fn=lambda *_args, **_kwargs: SideEffectDecision())
        self.assertEqual(report["failed"], 1)
        self.assertIn(
            "OfflineViolation",
            report["failures"][0]["errors"][0]["error"],
        )

    def test_runner_records_exception_and_continues(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                {
                    "id": "raises",
                    "envelope": {"raise": True},
                    "expected": {"action": "REFINE", "role": None},
                },
                {
                    "id": "passes",
                    "envelope": {"raise": False},
                    "expected": {"action": "REFINE", "role": None},
                },
            ],
        }

        def selective_route(envelope: dict[str, Any], **_kwargs: Any) -> RoutingDecision:
            if envelope["raise"]:
                raise RuntimeError("offline fixture failure")
            return RoutingDecision("REFINE", None, "test")

        report = evaluate_suite(suite, route_fn=selective_route)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["failures"][0]["id"], "raises")
        self.assertIn("RuntimeError", report["failures"][0]["errors"][0]["error"])

    def test_loader_rejects_duplicate_ids(self) -> None:
        fixture = copy.deepcopy(self.suite["fixtures"][0])
        fixture["id"] = "duplicate"
        duplicate = {
            "schema_version": "0.1",
            "fixtures": [fixture, copy.deepcopy(fixture)],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "duplicate.json"
            path.write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(FixtureFormatError, "duplicate fixture id"):
                load_suite(path)

    def test_loader_rejects_falsely_flagged_valid_envelope(self) -> None:
        fixture = copy.deepcopy(self.suite["fixtures"][0])
        fixture["expect_validation_error"] = True
        document = {"schema_version": "0.1", "fixtures": [fixture]}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "false-invalid-flag.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(FixtureFormatError):
                load_suite(path)

    def test_cli_emits_json_and_exits_nonzero_on_mismatch(self) -> None:
        mismatch = copy.deepcopy(self.suite)
        mismatch["fixtures"] = [mismatch["fixtures"][0]]
        mismatch["fixtures"][0]["expected"]["role"] = "executor"

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mismatch.json"
            path.write_text(json.dumps(mismatch), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "evals" / "runner.py"), str(path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, "")
        report = json.loads(completed.stdout)
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["failed"], 1)

    def test_workflow_pins_actions_and_runs_offline_gate(self) -> None:
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        self.assertIn("pull_request", workflow["on"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})

        job = workflow["jobs"]["offline-l1"]
        self.assertNotIn("if", job)
        self.assertEqual(
            job["strategy"]["matrix"]["os"],
            ["ubuntu-24.04", "windows-2022"],
        )
        self.assertEqual(job["runs-on"], "${{ matrix.os }}")
        steps = job["steps"]
        self.assertEqual(
            [step["name"] for step in steps],
            [
                "Check out repository",
                "Set up Python",
                "Run unit tests",
                "Run deterministic L1 fixtures",
                "Validate canonical JSON documents",
                "Compile Python sources",
                "Check committed diff whitespace",
            ],
        )
        self.assertTrue(all("if" not in step for step in steps))
        uses = [step["uses"] for step in steps if "uses" in step]
        self.assertGreaterEqual(len(uses), 2)
        for action in uses:
            self.assertRegex(action, r"^actions/[\w-]+@[0-9a-f]{40}$")

        checkout = next(step for step in steps if step.get("name") == "Check out repository")
        self.assertIs(checkout["with"]["persist-credentials"], False)
        self.assertEqual(checkout["with"]["fetch-depth"], 2)
        setup = next(step for step in steps if step.get("name") == "Set up Python")
        self.assertEqual(setup["with"]["python-version"], "3.11.14")
        run_commands = "\n".join(step.get("run", "") for step in steps)

        required_commands = {
            "python -m unittest discover -s tests -v",
            "python -m evals.runner evals/l1-routing.yaml",
            "python -m json.tool routing.yaml",
            "python -m compileall -q router evals tests",
            "git diff --check HEAD^1..HEAD",
        }
        for command in required_commands:
            with self.subTest(command=command):
                self.assertIn(command, run_commands)
        self.assertNotIn("/dev/null", run_commands)
        self.assertIsNone(
            re.search(r"\b(?:curl|wget|pip install|npm)\b", run_commands)
        )

        gate = workflow["jobs"]["l1-gate"]
        self.assertEqual(gate["name"], "L1 routing gate")
        self.assertEqual(gate["if"], "${{ always() }}")
        self.assertEqual(gate["needs"], ["offline-l1"])
        self.assertEqual(gate["runs-on"], "ubuntu-24.04")
        self.assertEqual(len(gate["steps"]), 1)
        gate_step = gate["steps"][0]
        self.assertEqual(
            gate_step["env"]["MATRIX_RESULT"],
            "${{ needs.offline-l1.result }}",
        )
        self.assertIn('= "success"', gate_step["run"])


if __name__ == "__main__":
    unittest.main()
