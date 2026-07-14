from __future__ import annotations

import copy
import hashlib
import unittest

from router import (
    InvariantViolation,
    canonical_json,
    delegate,
    load_canonical_config,
    route,
    validate_delegation_spec,
)


def delegation_spec(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "objective": "Implement the bounded routing change",
        "constraints": ["Preserve the public interface"],
        "done_criteria": ["Focused tests pass"],
        "allowed_paths": ["router/**", "tests/test_delegation.py"],
        "forbidden_paths": ["migrations/**"],
        "context_refs": ["spec:phase-1"],
    }
    value.update(overrides)
    return value


def task_envelope(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "0.1",
        "task_id": "task-1",
        "parent_task_id": None,
        "task_type": "mechanical",
        "spec_completeness": "fully_specified",
        "risk_level": "low",
        "risk_tags": [],
        "failure_count": 0,
        "classification_source": "manual",
        "classification_evidence": "test fixture",
    }
    value.update(overrides)
    return value


class DelegationValidationTests(unittest.TestCase):
    def test_normalizes_optional_lists_without_mutating_input(self) -> None:
        value = {
            "objective": "Inspect the repository",
            "constraints": [],
            "done_criteria": [],
            "allowed_paths": ["src/**"],
        }
        before = copy.deepcopy(value)
        normalized = validate_delegation_spec(value)
        self.assertEqual(value, before)
        self.assertEqual(normalized["forbidden_paths"], [])
        self.assertEqual(normalized["context_refs"], [])

    def test_scope_may_be_allowed_or_forbidden_but_not_empty(self) -> None:
        allowed_only = delegation_spec(forbidden_paths=[])
        forbidden_only = delegation_spec(allowed_paths=[])
        self.assertTrue(validate_delegation_spec(allowed_only)["allowed_paths"])
        self.assertTrue(
            validate_delegation_spec(forbidden_only)["forbidden_paths"]
        )
        with self.assertRaisesRegex(InvariantViolation, "scope"):
            validate_delegation_spec(
                delegation_spec(allowed_paths=[], forbidden_paths=[])
            )

    def test_mechanical_work_requires_constraints_and_done_criteria(self) -> None:
        for changes in (
            {"constraints": []},
            {"done_criteria": []},
            {"objective": ""},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(InvariantViolation):
                    validate_delegation_spec(
                        delegation_spec(**changes), task_type="mechanical"
                    )

    def test_missing_required_fields_and_unknown_persistence_fields_fail(self) -> None:
        for field in ("objective", "constraints", "done_criteria"):
            value = delegation_spec()
            del value[field]
            with self.subTest(field=field):
                with self.assertRaises(InvariantViolation):
                    validate_delegation_spec(value)

        for field in ("raw_prompt", "secret", "source_content", "model"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(InvariantViolation, "unknown fields"):
                    validate_delegation_spec(
                        {**delegation_spec(), field: "must not persist"}
                    )

    def test_duplicate_and_overlapping_scope_is_rejected(self) -> None:
        with self.assertRaisesRegex(InvariantViolation, "duplicates"):
            validate_delegation_spec(
                delegation_spec(allowed_paths=["src/**", "src/**"])
            )
        with self.assertRaisesRegex(InvariantViolation, "both allowed and forbidden"):
            validate_delegation_spec(
                delegation_spec(
                    allowed_paths=["src/**"], forbidden_paths=["src/**"]
                )
            )


class DelegationHandleTests(unittest.TestCase):
    def test_valid_delegation_produces_stable_hash_only_handle(self) -> None:
        value = delegation_spec()
        reordered = {key: value[key] for key in reversed(value)}
        envelope = task_envelope()
        first = delegate("mech-executor", value, envelope)
        second = delegate("mech-executor", reordered, envelope)
        self.assertEqual(first, second)

        normalized = validate_delegation_spec(value, task_type="mechanical")
        expected = hashlib.sha256(
            canonical_json(normalized).encode("utf-8")
        ).hexdigest()
        self.assertEqual(first.spec_ref, f"sha256:{expected}")
        self.assertEqual(
            set(first.to_dict()),
            {"task_id", "role", "spec_ref", "model_alias", "effort"},
        )
        serialized = first.to_json()
        for sensitive_fragment in (
            value["objective"],
            value["constraints"][0],
            value["done_criteria"][0],
            value["allowed_paths"][0],
        ):
            self.assertNotIn(sensitive_fragment, serialized)

    def test_hash_changes_with_spec_but_not_task_identity(self) -> None:
        envelope = task_envelope(task_type="judgment")
        first = delegate("executor", delegation_spec(), envelope)
        changed = delegate(
            "executor",
            delegation_spec(done_criteria=["Full suite passes"]),
            envelope,
        )
        another_task = delegate(
            "executor",
            delegation_spec(),
            task_envelope(task_id="task-2", task_type="judgment"),
        )
        self.assertNotEqual(first.spec_ref, changed.spec_ref)
        self.assertEqual(first.spec_ref, another_task.spec_ref)
        self.assertNotEqual(first.task_id, another_task.task_id)

    def test_named_role_binding_drift_is_rejected(self) -> None:
        config = load_canonical_config()
        config["roles"]["executor"]["model_alias"] = "opus"
        with self.assertRaisesRegex(InvariantViolation, "differs from v0.1"):
            delegate(
                "executor",
                delegation_spec(),
                task_envelope(task_type="judgment"),
                registry=config,
            )

    def test_invalid_or_control_plane_role_is_rejected(self) -> None:
        for role in ("orchestrator", "missing-role"):
            with self.subTest(role=role):
                with self.assertRaisesRegex(InvariantViolation, "leaf role"):
                    delegate(role, delegation_spec(), task_envelope())

    def test_mechanical_delegate_enforces_strict_contract(self) -> None:
        with self.assertRaisesRegex(InvariantViolation, "mechanical"):
            delegate(
                "mech-executor",
                delegation_spec(done_criteria=[]),
                task_envelope(),
            )

    def test_task_id_and_registry_are_validated(self) -> None:
        with self.assertRaisesRegex(InvariantViolation, "task_id"):
            delegate(
                "executor",
                delegation_spec(),
                task_envelope(task_id="", task_type="judgment"),
            )
        config = load_canonical_config()
        config["roles"]["executor"]["model_binding_source"] = "invocation"
        with self.assertRaisesRegex(InvariantViolation, "another owner"):
            delegate(
                "executor",
                delegation_spec(),
                task_envelope(task_type="judgment"),
                registry=config,
            )

    def test_unicode_content_uses_canonical_utf8_hashing(self) -> None:
        value = delegation_spec(objective="完成可驗證的路由核心")
        envelope = task_envelope(task_type="judgment")
        first = delegate("executor", value, envelope)
        second = delegate(
            "executor", copy.deepcopy(value), copy.deepcopy(envelope)
        )
        self.assertEqual(first.spec_ref, second.spec_ref)

    def test_mechanical_escalation_keeps_mechanical_spec_contract(self) -> None:
        envelope = task_envelope(
            task_id="mechanical-escalated", failure_count=2
        )
        decision = route(envelope)
        self.assertEqual((decision.action, decision.role), ("DELEGATE", "executor"))
        handle = delegate(decision.role, delegation_spec(), envelope)
        self.assertEqual(
            (handle.task_id, handle.role),
            ("mechanical-escalated", "executor"),
        )
        with self.assertRaisesRegex(InvariantViolation, "mechanical"):
            delegate(
                decision.role,
                delegation_spec(constraints=[], done_criteria=[]),
                envelope,
            )

    def test_envelope_binds_task_identity_and_compatible_lane(self) -> None:
        with self.assertRaisesRegex(InvariantViolation, "envelope must be an object"):
            delegate("executor", delegation_spec(), "task-1")
        with self.assertRaisesRegex(InvariantViolation, "incompatible"):
            delegate(
                "executor",
                delegation_spec(),
                task_envelope(task_type="security"),
            )

    def test_delegation_must_match_full_canonical_route(self) -> None:
        cases = (
            (
                "judgment-high",
                "executor",
                task_envelope(task_type="judgment", risk_level="high"),
                (),
            ),
            (
                "migration-floor",
                "mech-executor",
                task_envelope(risk_tags=["migration"]),
                (),
            ),
            (
                "failure-escalation",
                "mech-executor",
                task_envelope(failure_count=4),
                (),
            ),
            (
                "partial-refine",
                "mech-executor",
                task_envelope(spec_completeness="partial"),
                (),
            ),
            (
                "exhausted-takeover",
                "mech-executor",
                task_envelope(failure_count=6),
                (),
            ),
            (
                "history-floor",
                "mech-executor",
                task_envelope(),
                (
                    {
                        "task_id": "task-1",
                        "sequence": 1,
                        "failure_count": 0,
                        "role": "senior-executor",
                    },
                ),
            ),
        )
        for name, role, envelope, history in cases:
            with self.subTest(case=name):
                with self.assertRaisesRegex(InvariantViolation, "canonical route"):
                    delegate(
                        role,
                        delegation_spec(),
                        envelope,
                        history=history,
                    )


if __name__ == "__main__":
    unittest.main()
