from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from router.config import load_canonical_config
from router.schema import SchemaValidationError, validate_schema


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = (
    "task-envelope",
    "delegation-spec",
    "role-registry",
    "ledger-record",
    "eval-fixture",
)


def valid_envelope() -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "task_id": "task-001",
        "parent_task_id": None,
        "task_type": "mechanical",
        "spec_completeness": "fully_specified",
        "risk_level": "low",
        "risk_tags": [],
        "failure_count": 0,
        "classification_source": "rule",
        "classification_evidence": "rule:structured_mechanical_task",
    }


def valid_delegation_spec() -> dict[str, object]:
    return {
        "objective": "Implement the bounded routing change",
        "constraints": ["Preserve public behavior"],
        "done_criteria": ["Targeted tests pass"],
        "allowed_paths": ["router/**"],
        "forbidden_paths": ["templates/**"],
        "context_refs": ["spec:S1b"],
    }


def valid_ledger_record() -> dict[str, object]:
    return {
        "record_id": "record-001",
        "task_id": "task-001",
        "sequence": 0,
        "timestamp": "2026-07-14T00:00:00Z",
        "envelope_snapshot": valid_envelope(),
        "role_invoked": "mech-executor",
        "model_claimed": "sonnet",
        "model_attested": None,
        "attestation_method": None,
        "attestation_status": "UNKNOWN",
        "token_usage": {"input": None, "output": None, "total": None},
        "latency_ms": None,
        "execution_status": "SUCCEEDED",
        "verifier_verdict": None,
        "escalated_from": None,
        "supersedes_record_id": None,
    }


def valid_eval_document() -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "fixtures": [
            {
                "id": "mechanical-low-risk",
                "envelope": valid_envelope(),
                "history": [],
                "expected": {
                    "action": "DELEGATE",
                    "role": "mech-executor",
                    "reason_code": "mechanical_low",
                    "forbidden_roles": [
                        "senior-executor",
                        "security-executor",
                        "verifier",
                    ],
                },
            }
        ],
    }


class SchemaDocumentTests(unittest.TestCase):
    def test_all_schema_documents_are_draft_2020_12_json(self) -> None:
        for name in SCHEMAS:
            with self.subTest(schema=name):
                path = ROOT / "schemas" / f"{name}.schema.json"
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    document["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )

    def test_positive_documents_validate(self) -> None:
        documents = {
            "task-envelope": valid_envelope(),
            "delegation-spec": valid_delegation_spec(),
            "role-registry": load_canonical_config(),
            "ledger-record": valid_ledger_record(),
            "eval-fixture": valid_eval_document(),
        }
        for name, document in documents.items():
            with self.subTest(schema=name):
                self.assertIsNone(validate_schema(name, document))

    def test_task_envelope_uses_closed_enums(self) -> None:
        fields = {
            "task_type": "migration",
            "spec_completeness": "complete",
            "risk_level": "critical",
            "classification_source": "heuristic",
        }
        for field, invalid_value in fields.items():
            with self.subTest(field=field):
                envelope = valid_envelope()
                envelope[field] = invalid_value
                with self.assertRaises(SchemaValidationError):
                    validate_schema("task-envelope", envelope)

    def test_task_envelope_nullability_and_failure_count(self) -> None:
        child = valid_envelope()
        child["task_id"] = "task-002"
        child["parent_task_id"] = "task-001"
        self.assertIsNone(validate_schema("task-envelope", child))

        for field, invalid_value in (
            ("parent_task_id", 42),
            ("failure_count", -1),
            ("failure_count", 1.5),
        ):
            with self.subTest(field=field, value=invalid_value):
                envelope = valid_envelope()
                envelope[field] = invalid_value
                with self.assertRaises(SchemaValidationError):
                    validate_schema("task-envelope", envelope)

    def test_task_envelope_rejects_missing_and_extra_fields(self) -> None:
        missing = valid_envelope()
        del missing["classification_evidence"]
        with self.assertRaises(SchemaValidationError):
            validate_schema("task-envelope", missing)

        extra = valid_envelope()
        extra["raw_prompt"] = "must not enter canonical state"
        with self.assertRaises(SchemaValidationError):
            validate_schema("task-envelope", extra)

    def test_delegation_spec_requires_fields_and_bounded_scope(self) -> None:
        for field in ("objective", "constraints", "done_criteria"):
            with self.subTest(missing=field):
                spec = valid_delegation_spec()
                del spec[field]
                with self.assertRaises(SchemaValidationError):
                    validate_schema("delegation-spec", spec)

        for spec in (
            {
                "objective": "Implement change",
                "constraints": ["Preserve behavior"],
                "done_criteria": ["Tests pass"],
            },
            {
                "objective": "Implement change",
                "constraints": ["Preserve behavior"],
                "done_criteria": ["Tests pass"],
                "allowed_paths": [],
                "forbidden_paths": [],
            },
        ):
            with self.subTest(spec=spec):
                with self.assertRaises(SchemaValidationError):
                    validate_schema("delegation-spec", spec)

        non_mechanical = valid_delegation_spec()
        non_mechanical["constraints"] = []
        non_mechanical["done_criteria"] = []
        self.assertIsNone(validate_schema("delegation-spec", non_mechanical))

    def test_ledger_nullable_evidence_and_closed_enums(self) -> None:
        record = valid_ledger_record()
        self.assertIsNone(validate_schema("ledger-record", record))

        for field, invalid_value in (
            ("execution_status", "DONE"),
            ("attestation_status", "ASSUMED"),
            ("verifier_verdict", "INCONCLUSIVE"),
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(record)
                invalid[field] = invalid_value
                with self.assertRaises(SchemaValidationError):
                    validate_schema("ledger-record", invalid)

        invalid_usage = copy.deepcopy(record)
        invalid_usage["token_usage"]["input"] = "unknown"  # type: ignore[index]
        with self.assertRaises(SchemaValidationError):
            validate_schema("ledger-record", invalid_usage)

    def test_ledger_attestation_status_is_consistent_with_evidence(self) -> None:
        mismatch = valid_ledger_record()
        mismatch.update(
            {
                "model_attested": "opus",
                "attestation_method": "transcript",
                "attestation_status": "MISMATCHED",
                "execution_status": "INVALIDATED",
            }
        )
        self.assertIsNone(validate_schema("ledger-record", mismatch))

        mismatched_success = copy.deepcopy(mismatch)
        mismatched_success["execution_status"] = "SUCCEEDED"
        with self.assertRaises(SchemaValidationError):
            validate_schema("ledger-record", mismatched_success)

        matched_without_observation = valid_ledger_record()
        matched_without_observation["attestation_status"] = "MATCHED"
        with self.assertRaises(SchemaValidationError):
            validate_schema("ledger-record", matched_without_observation)

        unknown_with_guessed_observation = valid_ledger_record()
        unknown_with_guessed_observation["model_attested"] = "sonnet"
        with self.assertRaises(SchemaValidationError):
            validate_schema("ledger-record", unknown_with_guessed_observation)

    def test_ledger_rejects_prompt_or_secret_persistence(self) -> None:
        for field in ("raw_prompt", "secret", "source_content"):
            with self.subTest(field=field):
                record = valid_ledger_record()
                record[field] = "sensitive"
                with self.assertRaises(SchemaValidationError):
                    validate_schema("ledger-record", record)

    def test_eval_fixture_contract_and_external_history_ref(self) -> None:
        document = valid_eval_document()
        document["fixtures"][0]["history"] = [valid_ledger_record()]  # type: ignore[index]
        self.assertIsNone(validate_schema("eval-fixture", document))

        invalid_action = valid_eval_document()
        invalid_action["fixtures"][0]["expected"]["action"] = "RETRY"  # type: ignore[index]
        with self.assertRaises(SchemaValidationError):
            validate_schema("eval-fixture", invalid_action)

        invalid_role = valid_eval_document()
        invalid_role["fixtures"][0]["expected"]["role"] = 42  # type: ignore[index]
        with self.assertRaises(SchemaValidationError):
            validate_schema("eval-fixture", invalid_role)

        inconsistent_role = valid_eval_document()
        inconsistent_role["fixtures"][0]["expected"] = {  # type: ignore[index]
            "action": "REFINE",
            "role": "executor",
        }
        with self.assertRaises(SchemaValidationError):
            validate_schema("eval-fixture", inconsistent_role)

    def test_eval_fixture_can_carry_intentionally_invalid_envelope(self) -> None:
        document = valid_eval_document()
        document["fixtures"][0]["envelope"]["task_type"] = "invalid"  # type: ignore[index]
        document["fixtures"][0]["expect_validation_error"] = True  # type: ignore[index]
        document["fixtures"][0]["expected"] = {  # type: ignore[index]
            "action": "REFINE",
            "role": None,
        }
        self.assertIsNone(validate_schema("eval-fixture", document))

        ordinary_fixture = valid_eval_document()
        ordinary_fixture["fixtures"][0]["envelope"]["task_type"] = "invalid"  # type: ignore[index]
        with self.assertRaises(SchemaValidationError):
            validate_schema("eval-fixture", ordinary_fixture)

        falsely_flagged = valid_eval_document()
        falsely_flagged["fixtures"][0]["expect_validation_error"] = True  # type: ignore[index]
        with self.assertRaises(SchemaValidationError):
            validate_schema("eval-fixture", falsely_flagged)


if __name__ == "__main__":
    unittest.main()
