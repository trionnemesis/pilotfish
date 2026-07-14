from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest import mock

from router import (
    CONFLICT,
    NO_MATCH,
    canonical_json,
    classifier_contract,
    effective_risk,
    load_canonical_config,
    preclassify,
    route,
)


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


class RoutingTests(unittest.TestCase):
    def assertDecision(
        self, value: dict[str, object], action: str, role: object, reason: str
    ) -> None:
        self.assertEqual(
            route(value).to_dict(),
            {"action": action, "role": role, "reason_code": reason},
        )

    def test_base_routes_cover_every_task_type(self) -> None:
        cases = (
            ("recon", "low", "scout", "recon_default"),
            ("mechanical", "low", "mech-executor", "mechanical_low_risk"),
            ("mechanical", "medium", "executor", "mechanical_medium_risk"),
            ("mechanical", "high", "senior-executor", "mechanical_high_risk"),
            ("judgment", "low", "executor", "judgment_standard_risk"),
            ("judgment", "medium", "executor", "judgment_standard_risk"),
            ("judgment", "high", "senior-executor", "judgment_high_risk"),
            ("security", "low", "security-executor", "security_fixed_lane"),
            ("verification", "high", "verifier", "verification_isolation"),
        )
        for task_type, risk, role, reason in cases:
            with self.subTest(task_type=task_type, risk=risk):
                decision = route(
                    envelope(task_type=task_type, risk_level=risk)
                ).to_dict()
                self.assertEqual(decision["action"], "DELEGATE")
                self.assertEqual(decision["role"], role)
                self.assertEqual(decision["reason_code"], reason)

    def test_completeness_gate_covers_all_values(self) -> None:
        for task_type in ("recon", "mechanical", "judgment", "verification"):
            with self.subTest(task_type=task_type, completeness="ambiguous"):
                self.assertEqual(
                    route(
                        envelope(
                            task_type=task_type,
                            spec_completeness="ambiguous",
                        )
                    ).to_dict(),
                    {
                        "action": "REFINE",
                        "role": None,
                        "reason_code": "ambiguous_spec",
                    },
                )

        self.assertDecision(
            envelope(task_type="recon", spec_completeness="partial"),
            "DELEGATE",
            "scout",
            "recon_default",
        )
        for task_type in ("mechanical", "judgment", "verification"):
            with self.subTest(task_type=task_type, completeness="partial"):
                self.assertDecision(
                    envelope(task_type=task_type, spec_completeness="partial"),
                    "REFINE",
                    None,
                    "partial_spec",
                )

    def test_security_precedes_every_completeness_and_declared_risk(self) -> None:
        for completeness in ("fully_specified", "partial", "ambiguous"):
            for risk in ("low", "medium", "high"):
                with self.subTest(completeness=completeness, risk=risk):
                    self.assertDecision(
                        envelope(
                            task_type="security",
                            spec_completeness=completeness,
                            risk_level=risk,
                        ),
                        "DELEGATE",
                        "security-executor",
                        "security_fixed_lane",
                    )

    def test_migration_raises_effective_risk_without_mutating_envelope(self) -> None:
        value = envelope(
            task_type="judgment", risk_level="low", risk_tags=["migration"]
        )
        before = copy.deepcopy(value)
        self.assertEqual(effective_risk(value), "high")
        self.assertDecision(
            value,
            "DELEGATE",
            "senior-executor",
            "judgment_high_risk",
        )
        self.assertEqual(value, before)
        self.assertEqual(value["risk_level"], "low")

    def test_route_is_deterministic_and_serializes_only_contract_fields(self) -> None:
        value = envelope(task_type="judgment", risk_level="medium")
        first = route(value)
        second = route(copy.deepcopy(value))
        self.assertEqual(first, second)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(
            canonical_json(first.to_dict()),
            '{"action":"DELEGATE","reason_code":"judgment_standard_risk","role":"executor"}',
        )

    def test_route_performs_no_configuration_io(self) -> None:
        with mock.patch.object(
            Path, "read_text", side_effect=AssertionError("route performed I/O")
        ):
            self.assertEqual(route(envelope()).role, "mech-executor")

    def test_invalid_envelopes_fail_open_to_refinement(self) -> None:
        invalid = (
            None,
            envelope(task_type="unknown"),
            envelope(risk_level="critical"),
            envelope(spec_completeness="complete"),
            envelope(failure_count=-1),
            envelope(failure_count=True),
            envelope(classification_source="rule", classification_evidence="guess"),
            envelope(task_id="same", parent_task_id="same"),
            {**envelope(), "raw_prompt": "must not enter canonical state"},
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(
                    route(value).to_dict(),
                    {
                        "action": "REFINE",
                        "role": None,
                        "reason_code": "invalid_envelope",
                    },
                )

    def test_custom_registry_is_validated_without_mutation(self) -> None:
        config = load_canonical_config()
        before = copy.deepcopy(config)
        self.assertEqual(route(envelope(), registry=config).role, "mech-executor")
        self.assertEqual(config, before)

        config["roles"]["mech-executor"]["can_spawn"] = True
        self.assertEqual(
            route(envelope(), registry=config).to_dict(),
            {
                "action": "REFINE",
                "role": None,
                "reason_code": "invalid_registry",
            },
        )

        config = load_canonical_config()
        config["roles"]["executor"]["model_alias"] = "opus"
        self.assertEqual(
            route(envelope(), registry=config).to_dict(),
            {
                "action": "REFINE",
                "role": None,
                "reason_code": "invalid_registry",
            },
        )

    def test_load_canonical_config_returns_independent_copies(self) -> None:
        first = load_canonical_config()
        second = load_canonical_config()
        first["roles"]["executor"]["effort"] = "low"
        self.assertEqual(second["roles"]["executor"]["effort"], "high")


class PreclassifierTests(unittest.TestCase):
    def test_free_form_keywords_never_force_classification(self) -> None:
        self.assertEqual(
            preclassify({"prompt": "security migration verify password"}), NO_MATCH
        )
        self.assertEqual(preclassify("read the repository"), NO_MATCH)

    def test_structured_provable_rules_emit_only_supported_fields(self) -> None:
        self.assertEqual(
            preclassify({"read_only_lookup": True}),
            {
                "task_type": "recon",
                "classification_source": "rule",
                "classification_evidence": "rule:read_only_lookup",
            },
        )
        self.assertEqual(
            preclassify(
                {
                    "operation": "verification_only",
                    "risk_tags": ["migration"],
                }
            ),
            {
                "task_type": "verification",
                "risk_tags": ["migration"],
                "classification_source": "rule",
                "classification_evidence": "rule:verification_only+structured_migration",
            },
        )

    def test_conflicting_task_type_rules_fail_open(self) -> None:
        self.assertEqual(
            preclassify(
                {"read_only_lookup": True, "security_sensitive": True}
            ),
            CONFLICT,
        )

    def test_preclassification_is_deterministic(self) -> None:
        context = {
            "proven_rule_ids": ["structured_migration", "security_sensitive"]
        }
        self.assertEqual(preclassify(context), preclassify(copy.deepcopy(context)))

    def test_classifier_contract_rejects_guessed_or_invalid_output(self) -> None:
        result = classifier_contract(envelope(task_type="outside-enum"))
        self.assertFalse(result.accepted)
        self.assertEqual(
            result.decision.to_dict(),
            {
                "action": "REFINE",
                "role": None,
                "reason_code": "invalid_envelope",
            },
        )
        accepted = classifier_contract(envelope())
        self.assertTrue(accepted.accepted)
        self.assertIsNone(accepted.decision)


if __name__ == "__main__":
    unittest.main()
