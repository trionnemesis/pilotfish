from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from router import CONFLICT, NO_MATCH, preclassify, route


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "routing.yaml").read_text(encoding="utf-8"))["roles"]
TASK_ENVELOPE_SCHEMA = json.loads(
    (ROOT / "schemas/task-envelope.schema.json").read_text(encoding="utf-8")
)
LEAF_ROLES = tuple(
    name for name, definition in REGISTRY.items() if definition["role_type"] == "leaf"
)


def _frontmatter(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    _, raw_frontmatter, _ = content.split("---", 2)
    return {
        key.strip(): value.strip()
        for line in raw_frontmatter.strip().splitlines()
        for key, value in (line.split(":", 1),)
    }


def _section(policy: str, heading: str) -> str:
    section = policy.split(f"### {heading}", 1)[1]
    return section.split("\n### ", 1)[0]


def _table_keys(section: str) -> set[str]:
    return set(re.findall(r"(?m)^\| `([^`]+)` \|", section))


def _envelope(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "0.1",
        "task_id": "policy-contract-task",
        "parent_task_id": None,
        "task_type": "mechanical",
        "spec_completeness": "fully_specified",
        "risk_level": "low",
        "risk_tags": [],
        "failure_count": 0,
        "classification_source": "manual",
        "classification_evidence": "policy contract test",
    }
    value.update(overrides)
    return value


class PolicyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = (ROOT / "templates/claude-md.orchestration.md").read_text(
            encoding="utf-8"
        )

    def test_policy_version_stamp_matches_version_file(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertIn(f"<!-- pilotfish v{version} -->", self.policy)

    def test_canonical_registry_defines_exactly_seven_leaf_templates(self) -> None:
        control_plane_roles = {
            name
            for name, definition in REGISTRY.items()
            if definition["role_type"] == "control_plane"
        }
        template_roles = {
            path.stem for path in (ROOT / "templates" / "agents").glob("*.md")
        }

        self.assertEqual(len(LEAF_ROLES), 7)
        self.assertEqual(control_plane_roles, {"orchestrator"})
        self.assertEqual(template_roles, set(LEAF_ROLES))
        self.assertFalse((ROOT / "templates" / "agents" / "orchestrator.md").exists())
        self.assertEqual(
            _table_keys(_section(self.policy, "Named leaf roles")), set(LEAF_ROLES)
        )

        for role in LEAF_ROLES:
            definition = REGISTRY[role]
            frontmatter = _frontmatter(
                ROOT / "templates" / "agents" / f"{role}.md"
            )
            self.assertFalse(definition["can_spawn"])
            self.assertEqual(definition["model_binding_source"], "role_registry")
            self.assertEqual(frontmatter["name"], role)
            self.assertEqual(frontmatter["model"], definition["model_alias"])
            self.assertEqual(frontmatter["effort"], definition["effort"])

    def test_named_roles_own_model_binding(self) -> None:
        self.assertIn("omit the `model` argument entirely", self.policy)
        self.assertIn("invocation-level model override", self.policy)
        self.assertIn("owned by each named role definition", self.policy)
        self.assertIn("Never use an ad-hoc role to bypass", self.policy)
        leaf_aliases = {
            REGISTRY[role]["model_alias"].lower() for role in LEAF_ROLES
        }
        for alias in leaf_aliases:
            self.assertNotRegex(self.policy.lower(), rf"\b{re.escape(alias)}\b")

    def test_policy_requires_valid_envelope_and_canonical_router(self) -> None:
        contract = _section(self.policy, "Canonical dispatch contract")
        envelope_fields = re.search(
            r"Form a canonical Task Envelope with (.*?)\. Run schema",
            contract,
            re.DOTALL,
        )
        self.assertIsNotNone(envelope_fields)
        self.assertEqual(
            set(re.findall(r"`([^`]+)`", envelope_fields.group(1))),
            set(TASK_ENVELOPE_SCHEMA["required"]),
        )
        self.assertIn("schema and invariant validation", contract)
        self.assertIn("contradictory fields require `REFINE`", contract)
        self.assertIn("do not delegate or guess replacement values", contract)
        self.assertIn("canonical deterministic `route(envelope, history)`", contract)
        self.assertIn("validated history and the canonical role registry", contract)
        self.assertIn("only to the exact returned role", contract)

    def test_policy_has_phase_aware_approval_gate_without_new_roles(self) -> None:
        self.assertIn("use this lifecycle", self.policy)
        for phase in ("Discovery", "Plan", "Approval", "Execution", "Verification"):
            self.assertIn(f"| {phase} |", self.policy)
        self.assertIn("wait for explicit user approval", self.policy)
        self.assertIn("No source edit or implementation dispatch", self.policy)
        self.assertNotIn("plan-verifier", self.policy)
        self.assertNotIn("security-reviewer", self.policy)

    def test_policy_brakes_tightly_coupled_dispatch(self) -> None:
        self.assertIn("Apply a dispatch brake before every Agent call", self.policy)
        self.assertIn("establishes eligibility, not a requirement to spawn", self.policy)
        self.assertIn("main session's evolving evidence", self.policy)
        self.assertIn("root-cause discovery", self.policy)
        self.assertIn("trace-driven debugging", self.policy)
        self.assertIn("tightly coupled state propagation", self.policy)
        self.assertIn("sequential `scout` → `executor` pipeline", self.policy)
        self.assertIn("does not own or block the main diagnosis", self.policy)
        self.assertIn("choose by net benefit", self.policy)
        self.assertIn("Direct execution being slightly faster is not a veto", self.policy)

    def test_security_preapproval_preserves_write_boundary(self) -> None:
        self.assertIn("explicitly labeled `ANALYSIS ONLY`", self.policy)
        self.assertIn("forbid file edits", self.policy)
        self.assertIn("after approval, route only the stable implementation contract", self.policy)
        self.assertIn("security-executor", self.policy)

    def test_policy_preclassification_contract_matches_live_core(self) -> None:
        contract = _section(self.policy, "Canonical dispatch contract")
        self.assertIn("`preclassify(context)` for every task", contract)
        self.assertIn("only structured signals the rules can prove", contract)
        self.assertIn("`NO_MATCH`, `CONFLICT`", contract)
        self.assertIn("`classify(prompt, partial_envelope?)`", contract)

        self.assertEqual(preclassify({}), NO_MATCH)
        self.assertEqual(
            preclassify(
                {"read_only_lookup": True, "security_sensitive": True}
            ),
            CONFLICT,
        )
        classified = preclassify({"operation": "verification_only"})
        self.assertEqual(classified["task_type"], "verification")
        self.assertTrue(classified["classification_evidence"].startswith("rule:"))

    def test_control_plane_actions_do_not_invent_roles(self) -> None:
        contract = _section(self.policy, "Canonical dispatch contract")
        self.assertEqual(
            _table_keys(contract), {"DELEGATE", "REFINE", "TAKEOVER", "BLOCK"}
        )

        self.assertIn("There is no refine agent", self.policy)
        self.assertIn("not a named role", self.policy)
        self.assertIn("There is no block agent", self.policy)
        self.assertIn("new `task_id`", self.policy)
        self.assertIn("never reset the original task's failure count", self.policy)
        self.assertNotIn("plan-verifier", self.policy)
        self.assertNotIn("security-reviewer", self.policy)

    def test_failure_accounting_is_a_closed_mapping(self) -> None:
        section = _section(self.policy, "Failure accounting and re-routing")
        mapping = dict(
            re.findall(
                r"(?m)^\| `([^`]+)` \| (Increment|Do not increment)[^|]*\|$",
                section,
            )
        )
        self.assertEqual(
            mapping,
            {
                "execution:FAILED": "Increment",
                "execution:BLOCKED_MISROUTE_OR_SPEC_CONTRADICTION": "Increment",
                "verifier:REFUTED": "Increment",
                "attestation:MISMATCHED": "Do not increment",
                "user:CANCELLED": "Do not increment",
                "verifier:RUNTIME_FAILURE": "Do not increment",
            },
        )
        self.assertIn("monotonically update the parent Task Envelope", section)
        self.assertIn("append a new ledger/history record", section)
        self.assertIn("call `route(envelope, history)` again", section)
        self.assertIn("never the verifier task", section)
        self.assertIn("Two consecutive verifier runtime/tool failures", section)
        self.assertIn("without increasing the parent count", section)

    def test_live_router_matches_policy_safety_decisions(self) -> None:
        invalid = _envelope()
        del invalid["task_type"]
        invalid_decision = route(invalid, registry=REGISTRY)
        self.assertEqual(
            (invalid_decision.action, invalid_decision.role), ("REFINE", None)
        )

        for failure_count in (0, 1):
            security_decision = route(
                _envelope(
                    task_type="security",
                    spec_completeness="ambiguous",
                    risk_level="low",
                    failure_count=failure_count,
                ),
                registry=REGISTRY,
            )
            self.assertEqual(
                (security_decision.action, security_decision.role),
                ("DELEGATE", "security-executor"),
            )

        exhausted_security = route(
            _envelope(task_type="security", failure_count=2),
            registry=REGISTRY,
        )
        self.assertEqual(
            (exhausted_security.action, exhausted_security.role),
            ("TAKEOVER", None),
        )

        escalated_decision = route(_envelope(failure_count=4), registry=REGISTRY)
        self.assertEqual(
            (escalated_decision.action, escalated_decision.role),
            ("DELEGATE", "senior-executor"),
        )

        exhausted_decision = route(_envelope(failure_count=6), registry=REGISTRY)
        self.assertEqual(
            (exhausted_decision.action, exhausted_decision.role),
            ("TAKEOVER", None),
        )

    def test_security_pre_route_and_verifier_isolation_are_explicit(self) -> None:
        security_pre_route = "deterministic security pre-route before ordinary classification"
        self.assertIn(security_pre_route, self.policy)
        self.assertLess(
            self.policy.index(security_pre_route),
            self.policy.index("Form a canonical Task Envelope"),
        )
        self.assertIn(
            "never perform its modification work in the main session",
            self.policy.lower(),
        )
        self.assertIn(
            "validated security work delegates to `security-executor`", self.policy
        )
        self.assertIn(
            "at the canonical failure boundary it returns `TAKEOVER`", self.policy
        )
        self.assertIn(
            "later child execution must still return to the security lane", self.policy
        )
        self.assertIn("must return `BLOCKED` rather than guess", self.policy)
        self.assertIn("create a verification Task Envelope", self.policy)
        self.assertIn("fresh context", self.policy)
        self.assertIn("only `CONFIRMED` or `REFUTED`", self.policy)
        self.assertIn("never edit or fix what it finds", self.policy)
        self.assertIn("exhaustive verification profile", self.policy)

    def test_all_named_roles_remain_leaf_agents(self) -> None:
        self.assertIn("Every named subagent role", self.policy)
        self.assertIn("is a leaf", self.policy)
        self.assertIn("never call Agent or Workflow or spawn another subagent", self.policy)
        self.assertIn("Every named role remains a non-spawning leaf", self.policy)
        self.assertTrue(all(not REGISTRY[role]["can_spawn"] for role in LEAF_ROLES))

    def test_policy_preserves_safe_parallel_and_long_running_guidance(self) -> None:
        self.assertIn(
            "Parallelize only independent, already-routed child tasks", self.policy
        )
        self.assertIn("isolated worktree", self.policy)
        self.assertIn("collect every background result", self.policy)
        self.assertIn("Leaf agents must never detach work with `nohup`", self.policy)
        self.assertIn("exact command, absolute working directory", self.policy)
        self.assertIn("runs it in that exact context with background Bash", self.policy)
        self.assertIn("Do not infer agent liveness from host CPU", self.policy)

    def test_writing_dispatch_requires_authorization_without_bypassing_routing(self) -> None:
        self.assertIn(
            "requires an authorized and stable execution contract", self.policy
        )
        self.assertIn("wait for explicit user approval", self.policy)
        self.assertIn(
            "Approval never bypasses envelope validation, security routing, or the deterministic router",
            self.policy,
        )


if __name__ == "__main__":
    unittest.main()
