from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals.l2_runner import L2ConfigurationError, load_suite, run_suite
from evals.report import aggregate_report
from router.route import route


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "l2-classification.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "l1.yml"


FAKE_CLASSIFIER = r'''
import json
import sys

request = json.load(sys.stdin)
fixture_id = request["fixture_id"]
run_index = request["run_index"]

task_type = "security" if "security" in fixture_id else "judgment"
risk_level = "high" if task_type == "security" else "medium"
if fixture_id == "vary" and run_index == 1:
    task_type = "recon"
    risk_level = "low"
if fixture_id == "denominator-security" and run_index == 2:
    task_type = "judgment"
    risk_level = "medium"
if fixture_id == "invalid-json":
    print("not-json")
    raise SystemExit(0)
if fixture_id == "invalid-envelope":
    task_type = "deployment"

envelope = {
    "schema_version": "0.1",
    "task_id": fixture_id,
    "parent_task_id": None,
    "task_type": task_type,
    "spec_completeness": "fully_specified",
    "risk_level": risk_level,
    "risk_tags": [],
    "failure_count": 0,
    "classification_source": "llm",
    "classification_evidence": "fake classifier",
}
print(json.dumps({
    "classifier_source": "fake-headless",
    "envelope": envelope,
    "metadata": {
        "cli_version": "fake 1.0",
        "model": "fake-model",
        "token_usage": {"input": 2, "output": 3, "total": 5},
    },
}))
'''


def fixture(
    fixture_id: str,
    *,
    accepted_types: list[str],
    action: str,
    role: str | None,
    security_expected: bool = False,
) -> dict[str, object]:
    return {
        "id": fixture_id,
        "task_description": f"classify {fixture_id}",
        "accepted_envelopes": {
            "task_type": accepted_types,
            "failure_count": [0],
            "classification_source": ["llm"],
        },
        "expected_route": {"action": action, "role": role},
        "security_expected": security_expected,
    }


class L2RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical_suite = load_suite(FIXTURES)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp_root = Path(self.temporary.name)
        self.classifier = self.temp_root / "fake_classifier.py"
        self.classifier.write_text(FAKE_CLASSIFIER, encoding="utf-8")
        self.command = [sys.executable, str(self.classifier)]

    def _run(
        self, suite: dict[str, object], *, runs: int = 1, name: str = "evidence"
    ) -> tuple[dict[str, object], Path]:
        output = self.temp_root / name
        report = run_suite(
            suite,
            command=self.command,
            runs=runs,
            max_invocations=len(suite["fixtures"]) * runs,
            output_dir=output,
            timeout_seconds=5,
        )
        return report, output

    def test_canonical_fixtures_cover_natural_language_dimensions(self) -> None:
        fixtures = self.canonical_suite["fixtures"]
        self.assertGreaterEqual(len(fixtures), 8)
        accepted_types = {
            task_type
            for item in fixtures
            for task_type in item["accepted_envelopes"]["task_type"]
        }
        self.assertEqual(
            accepted_types,
            {"recon", "mechanical", "judgment", "security", "verification"},
        )
        self.assertTrue(any(item["security_expected"] for item in fixtures))
        self.assertTrue(
            any(
                ["migration"] in item["accepted_envelopes"].get("risk_tags", [])
                for item in fixtures
            )
        )

    def test_each_canonical_fixture_has_an_accepted_route(self) -> None:
        for item in self.canonical_suite["fixtures"]:
            accepted = item["accepted_envelopes"]
            envelope = {
                "schema_version": "0.1",
                "task_id": item["id"],
                "parent_task_id": None,
                "task_type": accepted["task_type"][0],
                "spec_completeness": accepted["spec_completeness"][0],
                "risk_level": accepted["risk_level"][0],
                "risk_tags": accepted["risk_tags"][0],
                "failure_count": accepted["failure_count"][0],
                "classification_source": accepted["classification_source"][0],
                "classification_evidence": "canonical L2 fixture",
            }
            decision = route(envelope).to_dict()
            with self.subTest(fixture=item["id"]):
                self.assertEqual(decision["action"], item["expected_route"]["action"])
                self.assertEqual(decision["role"], item["expected_route"]["role"])

    def test_fake_headless_e2e_persists_evidence_and_report(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                fixture(
                    "security-e2e",
                    accepted_types=["security"],
                    action="DELEGATE",
                    role="security-executor",
                    security_expected=True,
                )
            ],
        }
        report, output = self._run(suite, runs=2)

        evidence = [
            json.loads(line)
            for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        persisted_report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(evidence), 2)
        self.assertTrue(all(item["schema_valid"] for item in evidence))
        self.assertEqual(report, persisted_report)
        self.assertEqual(report["release_gate"], "NON_BLOCKING")
        self.assertEqual(report["summary"]["schema_valid"]["rate"], 1.0)
        self.assertEqual(report["summary"]["accepted_envelope"]["rate"], 1.0)
        self.assertEqual(report["summary"]["route_agreement"]["rate"], 1.0)
        self.assertEqual(report["metadata"]["models"], ["fake-model"])
        self.assertEqual(report["summary"]["token_usage"]["total"]["sum"], 10)

    def test_invalid_outputs_are_retained_and_never_routed(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                fixture(
                    "invalid-json",
                    accepted_types=["judgment"],
                    action="DELEGATE",
                    role="executor",
                ),
                fixture(
                    "invalid-envelope",
                    accepted_types=["judgment"],
                    action="DELEGATE",
                    role="executor",
                ),
            ],
        }
        report, output = self._run(suite)
        evidence = [
            json.loads(line)
            for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        ]

        self.assertEqual(evidence[0]["raw_output"], "not-json")
        self.assertIsNone(evidence[0]["emitted_envelope"])
        self.assertEqual(evidence[1]["emitted_envelope"]["task_type"], "deployment")
        for item in evidence:
            self.assertFalse(item["schema_valid"])
            self.assertFalse(item["accepted_envelope"])
            self.assertIsNone(item["routing_decision"])
            self.assertFalse(item["route_match"])
            self.assertEqual(item["accepted_field_matches"], 0)
        self.assertEqual(report["summary"]["schema_valid"]["rate"], 0.0)

    def test_multiple_semantic_outcomes_produce_variance(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                fixture(
                    "vary",
                    accepted_types=["recon", "judgment"],
                    action="DELEGATE",
                    role="executor",
                )
            ],
        }
        report, _ = self._run(suite, runs=2)
        variance = report["fixtures"][0]["variance"]
        self.assertEqual(variance["unique_outcomes"], 2)
        self.assertEqual(variance["value"], 0.5)

    def test_security_and_route_denominators_include_every_attempt(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [
                fixture(
                    "denominator-security",
                    accepted_types=["security"],
                    action="DELEGATE",
                    role="security-executor",
                    security_expected=True,
                ),
                fixture(
                    "denominator-ordinary",
                    accepted_types=["judgment"],
                    action="DELEGATE",
                    role="executor",
                ),
            ],
        }
        report, _ = self._run(suite, runs=2)
        self.assertEqual(
            report["summary"]["security_recall"],
            {"numerator": 1, "denominator": 2, "rate": 0.5},
        )
        self.assertEqual(report["summary"]["route_agreement"]["denominator"], 4)

    def test_budget_and_output_overwrite_fail_before_invocation(self) -> None:
        suite = {
            "schema_version": "0.1",
            "fixtures": [fixture("budget", accepted_types=["judgment"], action="DELEGATE", role="executor")],
        }
        output = self.temp_root / "budget-output"
        with self.assertRaisesRegex(L2ConfigurationError, "exceeds budget"):
            run_suite(
                suite,
                command=self.command,
                runs=2,
                max_invocations=1,
                output_dir=output,
            )
        self.assertFalse(output.exists())

        output.mkdir()
        marker = output / "owned.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(L2ConfigurationError, "refusing to overwrite"):
            run_suite(
                suite,
                command=self.command,
                runs=1,
                max_invocations=1,
                output_dir=output,
            )
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_report_aggregation_is_deterministic(self) -> None:
        fixture_data = fixture(
            "aggregate", accepted_types=["judgment"], action="DELEGATE", role="executor"
        )
        result = {
            "fixture_id": "aggregate",
            "schema_valid": True,
            "accepted_envelope": True,
            "emitted_envelope": {
                "task_type": "judgment",
                "spec_completeness": "fully_specified",
                "risk_level": "medium",
                "risk_tags": [],
            },
            "accepted_field_matches": 3,
            "accepted_field_total": 3,
            "route_match": True,
            "latency_ms": 2.0,
            "token_usage": {"input": None, "output": None, "total": None},
            "classifier_source": "fake",
            "cli_version": None,
            "model": None,
        }
        arguments = {
            "runs_per_fixture": 1,
            "max_invocations": 1,
            "timeout_seconds": 5.0,
        }
        first = aggregate_report([fixture_data], [copy.deepcopy(result)], **arguments)
        second = aggregate_report([fixture_data], [copy.deepcopy(result)], **arguments)
        self.assertEqual(first, second)

    def test_default_cli_and_ci_do_not_invoke_a_classifier(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "evals.l2_runner"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--command-json", completed.stderr)

        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("evals.l2_runner", workflow_text)
        self.assertNotIn("l2-classification.yaml", workflow_text)

    def test_loader_rejects_duplicates_and_unknown_fields(self) -> None:
        document = copy.deepcopy(self.canonical_suite)
        document["fixtures"].append(copy.deepcopy(document["fixtures"][0]))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "duplicate.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(L2ConfigurationError, "duplicate fixture id"):
                load_suite(path)

        document = copy.deepcopy(self.canonical_suite)
        document["fixtures"][0]["accepted_envelopes"]["made_up"] = ["value"]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "unknown.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(L2ConfigurationError, "unknown field"):
                load_suite(path)


if __name__ == "__main__":
    unittest.main()
