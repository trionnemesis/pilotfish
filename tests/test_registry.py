from __future__ import annotations

import copy
import unittest

from router.config import load_canonical_config
from router.schema import SchemaValidationError, validate_schema


EXPECTED_BINDINGS = {
    "orchestrator": ("control_plane", "best", "high", True),
    "scout": ("leaf", "haiku", "low", False),
    "Explore": ("leaf", "haiku", "low", False),
    "mech-executor": ("leaf", "sonnet", "low", False),
    "executor": ("leaf", "sonnet", "high", False),
    "senior-executor": ("leaf", "opus", "high", False),
    "verifier": ("leaf", "opus", "medium", False),
    "security-executor": ("leaf", "opus", "high", False),
}

RECON_ROLES = ("scout", "Explore")
WRITING_ROLES = (
    "mech-executor",
    "executor",
    "senior-executor",
    "security-executor",
)
SPAWN_TOOLS = {"Agent", "Workflow"}
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}


class RoleRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_canonical_config()
        self.roles = self.config["roles"]

    def test_canonical_registry_validates_and_has_exact_roles(self) -> None:
        self.assertIsNone(validate_schema("role-registry", self.config))
        self.assertEqual(set(self.roles), set(EXPECTED_BINDINGS))

    def test_v01_bindings_use_logical_aliases(self) -> None:
        for role_name, (role_type, alias, effort, can_spawn) in EXPECTED_BINDINGS.items():
            with self.subTest(role=role_name):
                role = self.roles[role_name]
                self.assertEqual(role["role_type"], role_type)
                self.assertEqual(role["model_alias"], alias)
                self.assertEqual(role["effort"], effort)
                self.assertIs(role["can_spawn"], can_spawn)
                self.assertEqual(role["model_binding_source"], "role_registry")
                self.assertNotIn("model", role)

        invalid = copy.deepcopy(self.config)
        invalid["roles"]["executor"]["model_alias"] = "claude-sonnet-4-5"
        with self.assertRaises(SchemaValidationError):
            validate_schema("role-registry", invalid)

    def test_every_role_has_all_required_fields(self) -> None:
        required = (
            "role_type",
            "model_alias",
            "effort",
            "allowed_tools",
            "disallowed_tools",
            "can_spawn",
            "model_binding_source",
        )
        for role_name in EXPECTED_BINDINGS:
            for field in required:
                with self.subTest(role=role_name, missing=field):
                    invalid = copy.deepcopy(self.config)
                    del invalid["roles"][role_name][field]
                    with self.assertRaises(SchemaValidationError):
                        validate_schema("role-registry", invalid)

    def test_orchestrator_is_the_only_spawn_capable_role(self) -> None:
        self.assertEqual(self.roles["orchestrator"]["role_type"], "control_plane")
        self.assertIs(self.roles["orchestrator"]["can_spawn"], True)

        for role_name, role in self.roles.items():
            if role_name == "orchestrator":
                continue
            with self.subTest(role=role_name):
                self.assertEqual(role["role_type"], "leaf")
                self.assertIs(role["can_spawn"], False)

                invalid = copy.deepcopy(self.config)
                invalid["roles"][role_name]["can_spawn"] = True
                with self.assertRaises(SchemaValidationError):
                    validate_schema("role-registry", invalid)

    def test_recon_roles_use_read_only_positive_allowlists(self) -> None:
        read_search_tools = {"Read", "Glob", "Grep"}
        for role_name in RECON_ROLES:
            with self.subTest(role=role_name):
                role = self.roles[role_name]
                allowed = set(role["allowed_tools"])
                disallowed = set(role["disallowed_tools"])
                self.assertTrue(allowed)
                self.assertLessEqual(allowed, read_search_tools)
                self.assertTrue(WRITE_TOOLS | SPAWN_TOOLS <= disallowed)
                self.assertTrue(allowed.isdisjoint(WRITE_TOOLS | SPAWN_TOOLS))

                write_enabled = copy.deepcopy(self.config)
                write_enabled["roles"][role_name]["allowed_tools"].append("Write")
                with self.assertRaises(SchemaValidationError):
                    validate_schema("role-registry", write_enabled)

    def test_writing_roles_cannot_spawn_children(self) -> None:
        for role_name in WRITING_ROLES:
            with self.subTest(role=role_name):
                role = self.roles[role_name]
                self.assertIs(role["can_spawn"], False)
                self.assertTrue(
                    SPAWN_TOOLS <= set(role["disallowed_tools"]),
                    f"{role_name} must disallow Agent and Workflow",
                )
                for tool in SPAWN_TOOLS:
                    invalid = copy.deepcopy(self.config)
                    invalid["roles"][role_name]["disallowed_tools"].remove(tool)
                    with self.assertRaises(SchemaValidationError):
                        validate_schema("role-registry", invalid)

    def test_verifier_is_read_only_and_cannot_spawn(self) -> None:
        verifier = self.roles["verifier"]
        prohibited = WRITE_TOOLS | SPAWN_TOOLS
        self.assertIs(verifier["can_spawn"], False)
        self.assertTrue(prohibited <= set(verifier["disallowed_tools"]))
        self.assertTrue(set(verifier["allowed_tools"]).isdisjoint(prohibited))

        for tool in prohibited:
            with self.subTest(missing_prohibition=tool):
                invalid = copy.deepcopy(self.config)
                invalid["roles"]["verifier"]["disallowed_tools"].remove(tool)
                with self.assertRaises(SchemaValidationError):
                    validate_schema("role-registry", invalid)

    def test_allowed_and_disallowed_tools_do_not_overlap(self) -> None:
        for role_name, role in self.roles.items():
            with self.subTest(role=role_name):
                self.assertTrue(
                    set(role["allowed_tools"]).isdisjoint(role["disallowed_tools"])
                )

    def test_loader_returns_a_fresh_document(self) -> None:
        self.config["roles"]["executor"]["model_alias"] = "mutated"
        fresh = load_canonical_config()
        self.assertEqual(fresh["roles"]["executor"]["model_alias"], "sonnet")


if __name__ == "__main__":
    unittest.main()
