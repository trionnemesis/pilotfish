from __future__ import annotations

import copy
import unittest

from router import route


def envelope(**overrides: object) -> dict[str, object]:
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
        "classification_evidence": "explicit user classification",
    }
    value.update(overrides)
    return value


def history_record(
    *,
    role: str,
    sequence: int,
    failure_count: int = 0,
    task_id: str = "task-1",
    execution_status: str = "SUCCEEDED",
) -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "record_id": f"record-{task_id}-{sequence}",
        "task_id": task_id,
        "sequence": sequence,
        "timestamp": "2026-07-14T00:00:00+08:00",
        "envelope_snapshot": {
            "task_id": task_id,
            "failure_count": failure_count,
        },
        "role_invoked": role,
        "execution_status": execution_status,
    }


class EscalationBoundaryTests(unittest.TestCase):
    def assertRoute(
        self,
        value: dict[str, object],
        expected: tuple[str, object, str],
        history: list[dict[str, object]] | tuple[()] = (),
    ) -> None:
        decision = route(value, history).to_dict()
        self.assertEqual(
            decision,
            {
                "action": expected[0],
                "role": expected[1],
                "reason_code": expected[2],
            },
        )

    def test_recon_boundaries(self) -> None:
        for failures in (0, 1):
            self.assertRoute(
                envelope(task_type="recon", failure_count=failures),
                ("DELEGATE", "scout", "recon_default"),
            )
        self.assertRoute(
            envelope(task_type="recon", failure_count=2),
            ("TAKEOVER", None, "failure_budget_exhausted"),
        )

    def test_mechanical_low_boundaries(self) -> None:
        expectations = {
            0: "mech-executor",
            1: "mech-executor",
            2: "executor",
            3: "executor",
            4: "senior-executor",
            5: "senior-executor",
        }
        for failures, role in expectations.items():
            with self.subTest(failures=failures):
                self.assertRoute(
                    envelope(failure_count=failures),
                    ("DELEGATE", role, "mechanical_low_risk"),
                )
        self.assertRoute(
            envelope(failure_count=6),
            ("TAKEOVER", None, "failure_budget_exhausted"),
        )

    def test_mechanical_medium_boundaries(self) -> None:
        for failures in (0, 1):
            self.assertRoute(
                envelope(risk_level="medium", failure_count=failures),
                ("DELEGATE", "executor", "mechanical_medium_risk"),
            )
        for failures in (2, 3):
            self.assertRoute(
                envelope(risk_level="medium", failure_count=failures),
                ("DELEGATE", "senior-executor", "mechanical_medium_risk"),
            )
        self.assertRoute(
            envelope(risk_level="medium", failure_count=4),
            ("TAKEOVER", None, "failure_budget_exhausted"),
        )

    def test_mechanical_and_judgment_high_boundaries(self) -> None:
        for task_type in ("mechanical", "judgment"):
            reason = (
                "mechanical_high_risk"
                if task_type == "mechanical"
                else "judgment_high_risk"
            )
            for failures in (0, 1):
                self.assertRoute(
                    envelope(
                        task_type=task_type,
                        risk_level="high",
                        failure_count=failures,
                    ),
                    ("DELEGATE", "senior-executor", reason),
                )
            self.assertRoute(
                envelope(
                    task_type=task_type,
                    risk_level="high",
                    failure_count=2,
                ),
                ("TAKEOVER", None, "failure_budget_exhausted"),
            )

    def test_judgment_low_and_medium_boundaries(self) -> None:
        for risk in ("low", "medium"):
            for failures in (0, 1):
                self.assertRoute(
                    envelope(
                        task_type="judgment",
                        risk_level=risk,
                        failure_count=failures,
                    ),
                    ("DELEGATE", "executor", "judgment_standard_risk"),
                )
            for failures in (2, 3):
                self.assertRoute(
                    envelope(
                        task_type="judgment",
                        risk_level=risk,
                        failure_count=failures,
                    ),
                    (
                        "DELEGATE",
                        "senior-executor",
                        "judgment_standard_risk",
                    ),
                )
            self.assertRoute(
                envelope(
                    task_type="judgment", risk_level=risk, failure_count=4
                ),
                ("TAKEOVER", None, "failure_budget_exhausted"),
            )

    def test_security_boundary_never_falls_to_sonnet(self) -> None:
        for failures in (0, 1):
            self.assertRoute(
                envelope(task_type="security", failure_count=failures),
                ("DELEGATE", "security-executor", "security_fixed_lane"),
            )
        self.assertRoute(
            envelope(task_type="security", failure_count=2),
            ("TAKEOVER", None, "failure_budget_exhausted"),
        )

    def test_migration_uses_high_risk_ladder_not_declared_low_ladder(self) -> None:
        self.assertRoute(
            envelope(risk_tags=["migration"], failure_count=1),
            ("DELEGATE", "senior-executor", "mechanical_high_risk"),
        )
        self.assertRoute(
            envelope(risk_tags=["migration"], failure_count=2),
            ("TAKEOVER", None, "failure_budget_exhausted"),
        )


class HistoryEscalationTests(unittest.TestCase):
    def test_full_ledger_history_sets_execution_tier_floor(self) -> None:
        history = [history_record(role="senior-executor", sequence=1)]
        self.assertEqual(
            route(envelope(), history).to_dict(),
            {
                "action": "DELEGATE",
                "role": "senior-executor",
                "reason_code": "history_no_downgrade",
            },
        )

    def test_each_execution_tier_is_monotonic(self) -> None:
        cases = (
            ("mech-executor", "mech-executor", "mechanical_low_risk"),
            ("executor", "executor", "history_no_downgrade"),
            ("senior-executor", "senior-executor", "history_no_downgrade"),
        )
        for prior, expected, reason in cases:
            with self.subTest(prior=prior):
                decision = route(
                    envelope(), [history_record(role=prior, sequence=1)]
                )
                self.assertEqual(decision.role, expected)
                self.assertEqual(decision.reason_code, reason)

    def test_security_history_cannot_downgrade_to_general_executor(self) -> None:
        decision = route(
            envelope(), [history_record(role="security-executor", sequence=1)]
        )
        self.assertEqual(
            decision.to_dict(),
            {
                "action": "TAKEOVER",
                "role": None,
                "reason_code": "history_no_downgrade",
            },
        )

    def test_recon_never_escalates_into_a_writing_role(self) -> None:
        decision = route(
            envelope(task_type="recon"),
            [history_record(role="executor", sequence=1)],
        )
        self.assertEqual(
            decision.to_dict(),
            {
                "action": "TAKEOVER",
                "role": None,
                "reason_code": "history_no_downgrade",
            },
        )

    def test_other_task_history_does_not_set_a_floor(self) -> None:
        decision = route(
            envelope(),
            [
                history_record(
                    role="senior-executor", sequence=1, task_id="other-task"
                )
            ],
        )
        self.assertEqual(decision.role, "mech-executor")

    def test_new_child_task_does_not_reset_the_parent_record(self) -> None:
        decision = route(
            envelope(task_id="child", parent_task_id="task-1"),
            [history_record(role="senior-executor", sequence=1)],
        )
        self.assertEqual(decision.role, "mech-executor")

    def test_envelope_failure_count_is_authoritative_but_cannot_go_backwards(self) -> None:
        history = [
            history_record(
                role="executor", sequence=1, failure_count=3
            )
        ]
        self.assertEqual(
            route(envelope(failure_count=2), history).to_dict(),
            {
                "action": "REFINE",
                "role": None,
                "reason_code": "invalid_envelope",
            },
        )
        self.assertEqual(
            route(envelope(failure_count=3), history).role,
            "executor",
        )

    def test_history_sequence_and_failure_count_must_be_monotonic(self) -> None:
        non_monotonic_sequence = [
            history_record(role="executor", sequence=2, failure_count=1),
            history_record(role="executor", sequence=1, failure_count=1),
        ]
        non_monotonic_failures = [
            history_record(role="executor", sequence=1, failure_count=2),
            history_record(role="executor", sequence=2, failure_count=1),
        ]
        for history in (non_monotonic_sequence, non_monotonic_failures):
            with self.subTest(history=history):
                self.assertEqual(
                    route(envelope(failure_count=2), history).reason_code,
                    "invalid_envelope",
                )

    def test_history_record_identity_must_match_its_snapshot(self) -> None:
        record = history_record(role="executor", sequence=1)
        record["envelope_snapshot"]["task_id"] = "different-task"
        self.assertEqual(
            route(envelope(), [record]).reason_code,
            "invalid_envelope",
        )

    def test_history_rejects_unknown_execution_status(self) -> None:
        record = history_record(role="executor", sequence=1)
        record["execution_status"] = "RETRYING"
        self.assertEqual(
            route(envelope(), [record]).reason_code,
            "invalid_envelope",
        )

    def test_verifier_runtime_failure_ladder_uses_two_consecutive_failures(self) -> None:
        one_failure = [
            history_record(
                role="verifier", sequence=1, execution_status="FAILED"
            )
        ]
        self.assertEqual(
            route(envelope(task_type="verification"), one_failure).to_dict(),
            {
                "action": "DELEGATE",
                "role": "verifier",
                "reason_code": "verification_isolation",
            },
        )
        two_failures = one_failure + [
            history_record(
                role="verifier", sequence=2, execution_status="BLOCKED"
            )
        ]
        self.assertEqual(
            route(envelope(task_type="verification"), two_failures).to_dict(),
            {
                "action": "TAKEOVER",
                "role": None,
                "reason_code": "failure_budget_exhausted",
            },
        )

        reset = two_failures + [
            history_record(
                role="verifier", sequence=3, execution_status="SUCCEEDED"
            ),
            history_record(
                role="verifier", sequence=4, execution_status="FAILED"
            ),
        ]
        self.assertEqual(
            route(envelope(task_type="verification"), reset).role,
            "verifier",
        )

    def test_route_does_not_mutate_history(self) -> None:
        history = [history_record(role="executor", sequence=1)]
        before = copy.deepcopy(history)
        route(envelope(), history)
        self.assertEqual(history, before)


if __name__ == "__main__":
    unittest.main()
