from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from runtime.ledger import LedgerError, append_record, read_records
from runtime.models import build_record


def envelope(task_id: str = "task-1", failure_count: int = 0) -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "task_id": task_id,
        "parent_task_id": None,
        "task_type": "judgment",
        "spec_completeness": "fully_specified",
        "risk_level": "medium",
        "risk_tags": [],
        "failure_count": failure_count,
        "classification_source": "manual",
        "classification_evidence": "spec:sha256:abc",
    }


def ledger_record(
    record_id: str,
    sequence: int,
    *,
    task_id: str = "task-1",
    failure_count: int = 0,
    supersedes_record_id: str | None = None,
) -> dict[str, object]:
    return build_record(
        record_id=record_id,
        sequence=sequence,
        timestamp=f"2026-07-14T08:00:{sequence:02d}Z",
        envelope_snapshot=envelope(task_id, failure_count),
        role_invoked="executor",
        model_claimed="sonnet",
        execution_status="SUCCEEDED",
        supersedes_record_id=supersedes_record_id,
    )


class AppendOnlyLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "run-ledger.jsonl"

    def test_append_never_rewrites_existing_bytes(self) -> None:
        first = ledger_record("record-0", 0)
        second = ledger_record("record-1", 1, failure_count=1)
        append_record(self.path, first)
        prefix = self.path.read_bytes()
        append_record(self.path, second)
        final = self.path.read_bytes()
        self.assertTrue(final.startswith(prefix))
        self.assertGreater(len(final), len(prefix))
        self.assertEqual(
            [item["record_id"] for item in read_records(self.path)],
            ["record-0", "record-1"],
        )

    def test_reads_are_copies_and_no_mutation_api_exists(self) -> None:
        append_record(self.path, ledger_record("record-0", 0))
        records = read_records(self.path)
        records[0]["envelope_snapshot"]["failure_count"] = 99
        self.assertEqual(
            read_records(self.path)[0]["envelope_snapshot"]["failure_count"], 0
        )
        import runtime.ledger as ledger

        self.assertFalse(hasattr(ledger, "update_record"))
        self.assertFalse(hasattr(ledger, "delete_record"))

    def test_duplicate_id_changed_record_is_rejected_without_write(self) -> None:
        original = ledger_record("record-0", 0)
        append_record(self.path, original)
        before = self.path.read_bytes()
        changed = copy.deepcopy(original)
        changed["sequence"] = 1
        changed["execution_status"] = "FAILED"
        with self.assertRaisesRegex(LedgerError, "duplicate record_id"):
            append_record(self.path, changed)
        self.assertEqual(self.path.read_bytes(), before)

    def test_sequence_and_failure_count_are_monotonic(self) -> None:
        append_record(self.path, ledger_record("record-0", 2, failure_count=2))
        for candidate, message in (
            (ledger_record("record-1", 2, failure_count=2), "sequence"),
            (ledger_record("record-2", 3, failure_count=1), "failure_count"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(LedgerError, message):
                    append_record(self.path, candidate)
        self.assertEqual(len(read_records(self.path)), 1)

    def test_corrections_only_append_and_reference_prior_same_task_record(self) -> None:
        append_record(self.path, ledger_record("record-0", 0))
        prefix = self.path.read_bytes()
        correction = ledger_record(
            "record-1", 1, supersedes_record_id="record-0"
        )
        append_record(self.path, correction)
        self.assertTrue(self.path.read_bytes().startswith(prefix))
        self.assertEqual(read_records(self.path)[1]["supersedes_record_id"], "record-0")

        with self.assertRaisesRegex(LedgerError, "earlier record"):
            append_record(
                self.path,
                ledger_record("record-2", 2, supersedes_record_id="missing"),
            )
        with self.assertRaisesRegex(LedgerError, "same task"):
            append_record(
                self.path,
                ledger_record(
                    "record-3",
                    3,
                    task_id="task-2",
                    supersedes_record_id="record-0",
                ),
            )

    def test_malformed_or_partial_history_is_never_repaired(self) -> None:
        self.path.write_bytes(b'{"record_id":"partial"}')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(LedgerError, "partial record"):
            append_record(self.path, ledger_record("record-1", 1))
        self.assertEqual(self.path.read_bytes(), before)

    def test_raw_prompts_secrets_and_code_are_outside_record_shape(self) -> None:
        for field in ("raw_prompt", "secret", "source_code"):
            candidate = ledger_record("record-0", 0)
            candidate[field] = "must not persist"
            with self.subTest(field=field):
                with self.assertRaisesRegex(LedgerError, "invalid ledger record"):
                    append_record(self.path, candidate)
                self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
