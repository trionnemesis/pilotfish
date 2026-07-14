from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from router.schema import validate_schema
from runtime.attestation import OVERRIDE_ENV, attest
from runtime.ledger import append_record, read_records
from runtime.models import (
    RuntimeRecordError,
    TokenUsage,
    build_record,
    failure_updated_envelope,
)


def envelope(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "0.1",
        "task_id": "task-parent",
        "parent_task_id": None,
        "task_type": "judgment",
        "spec_completeness": "fully_specified",
        "risk_level": "medium",
        "risk_tags": [],
        "failure_count": 0,
        "classification_source": "manual",
        "classification_evidence": "spec:sha256:abc",
    }
    value.update(overrides)
    return value


def record(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "record_id": "record-1",
        "sequence": 0,
        "timestamp": "2026-07-14T08:00:00Z",
        "envelope_snapshot": envelope(),
        "role_invoked": "executor",
        "model_claimed": "sonnet",
        "execution_status": "SUCCEEDED",
    }
    values.update(overrides)
    return build_record(**values)  # type: ignore[arg-type]


class AttestationTests(unittest.TestCase):
    def test_missing_observation_is_unknown_not_matched(self) -> None:
        evidence = attest("sonnet", environment={})
        self.assertEqual(evidence.status, "UNKNOWN")
        self.assertIsNone(evidence.observed_model)
        result = record(attestation=evidence)
        self.assertIsNone(result["model_attested"])
        self.assertEqual(result["execution_status"], "SUCCEEDED")
        validate_schema("ledger-record", result)

    def test_observed_model_matches_or_invalidates(self) -> None:
        matched = attest(
            "sonnet", environment={}, transcript_metadata={"model": "sonnet"}
        )
        self.assertEqual(matched.status, "MATCHED")
        mismatched = attest(
            "sonnet", environment={}, provider_metadata={"model_id": "opus"}
        )
        self.assertEqual(mismatched.status, "MISMATCHED")
        self.assertEqual(
            record(attestation=mismatched)["execution_status"], "INVALIDATED"
        )

    def test_global_and_invocation_overrides_are_detected_by_presence(self) -> None:
        empty_override = attest("sonnet", environment={OVERRIDE_ENV: ""})
        self.assertTrue(empty_override.override_present)
        self.assertEqual(empty_override.evidence_level, "configured")
        self.assertEqual(empty_override.status, "MISMATCHED")
        self.assertEqual(empty_override.observed_model, "<override-present>")
        invocation_override = attest(
            "sonnet", environment={}, invocation_metadata={"model": "sonnet"}
        )
        self.assertEqual(invocation_override.status, "MISMATCHED")
        self.assertEqual(
            record(attestation=invocation_override)["execution_status"],
            "INVALIDATED",
        )

        observed = attest(
            "sonnet", environment={}, transcript_metadata={"model": "sonnet"}
        )
        self.assertEqual(observed.evidence_level, "observed")

    def test_conflicting_observation_stays_unknown(self) -> None:
        evidence = attest(
            "sonnet",
            environment={},
            transcript_metadata={"model": "sonnet"},
            provider_metadata={"model": "opus"},
        )
        self.assertEqual(evidence.status, "UNKNOWN")
        self.assertIsNone(evidence.observed_model)
        self.assertEqual(evidence.evidence_method, "conflicting-observation")

    def test_raw_transcript_and_invalid_metadata_are_not_accepted(self) -> None:
        with self.assertRaisesRegex(RuntimeRecordError, "metadata mapping"):
            attest("sonnet", environment={}, transcript_metadata="raw prompt")  # type: ignore[arg-type]
        with self.assertRaisesRegex(RuntimeRecordError, "non-empty string"):
            attest("sonnet", environment={}, provider_metadata={"model": 7})


class RuntimeRecordTests(unittest.TestCase):
    def test_nullable_cost_fields_and_separate_verifier_verdict(self) -> None:
        result = record(
            role_invoked="verifier",
            verifier_verdict="CONFIRMED",
            token_usage=TokenUsage(),
            latency_ms=None,
        )
        self.assertEqual(
            result["token_usage"], {"input": None, "output": None, "total": None}
        )
        self.assertIsNone(result["latency_ms"])
        self.assertEqual(result["execution_status"], "SUCCEEDED")
        self.assertEqual(result["verifier_verdict"], "CONFIRMED")

    def test_record_builder_rejects_invalid_status_and_cost_shape(self) -> None:
        with self.assertRaisesRegex(RuntimeRecordError, "execution status"):
            record(execution_status="BOGUS")
        with self.assertRaisesRegex(RuntimeRecordError, "exactly"):
            record(
                token_usage={
                    "input": None,
                    "output": None,
                    "total": None,
                    "raw_prompt": "must not persist",
                }
            )

    def test_failure_events_update_only_the_parent_envelope(self) -> None:
        parent = envelope(failure_count=3)
        cases = (
            ({"execution_status": "FAILED"}, 4),
            ({"execution_status": "BLOCKED"}, 3),
            (
                {
                    "execution_status": "BLOCKED",
                    "blocked_is_misroute_or_contradiction": True,
                },
                4,
            ),
            (
                {"execution_status": "SUCCEEDED", "verifier_verdict": "REFUTED"},
                4,
            ),
            ({"execution_status": "CANCELLED"}, 3),
            (
                {"execution_status": "FAILED", "verifier_runtime_failure": True},
                3,
            ),
            (
                {"execution_status": "INVALIDATED", "attestation_status": "MISMATCHED"},
                3,
            ),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                updated = failure_updated_envelope(parent, **arguments)
                self.assertEqual(updated["failure_count"], expected)
                self.assertEqual(parent["failure_count"], 3)

    def test_refuted_verifier_record_is_successful_and_uses_parent_task(self) -> None:
        updated = failure_updated_envelope(
            envelope(), execution_status="SUCCEEDED", verifier_verdict="REFUTED"
        )
        result = record(
            envelope_snapshot=updated,
            role_invoked="verifier",
            verifier_verdict="REFUTED",
        )
        self.assertEqual(result["task_id"], "task-parent")
        self.assertEqual(result["execution_status"], "SUCCEEDED")
        self.assertEqual(result["verifier_verdict"], "REFUTED")
        self.assertEqual(result["envelope_snapshot"]["failure_count"], 1)

    def test_failure_update_is_a_new_append_only_parent_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            append_record(path, record())
            prefix = path.read_bytes()
            updated = failure_updated_envelope(
                envelope(), execution_status="SUCCEEDED", verifier_verdict="REFUTED"
            )
            append_record(
                path,
                record(
                    record_id="record-2",
                    sequence=1,
                    envelope_snapshot=updated,
                    role_invoked="verifier",
                    verifier_verdict="REFUTED",
                ),
            )
            self.assertTrue(path.read_bytes().startswith(prefix))
            self.assertEqual(
                [
                    item["envelope_snapshot"]["failure_count"]
                    for item in read_records(path)
                ],
                [0, 1],
            )


if __name__ == "__main__":
    unittest.main()
