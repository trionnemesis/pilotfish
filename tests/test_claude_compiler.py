from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters.claude import (
    ClaudeCompileError,
    compile_adapter,
    compile_claude,
    parse_agent_definition,
)
from adapters.claude.compiler import CAPABILITY_ORDER, LEAF_ROLE_ORDER
from router import load_canonical_config


ROOT = Path(__file__).resolve().parents[1]
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
SPAWN_TOOLS = {"Agent", "Workflow"}


class ClaudeCompilerGoldenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_canonical_config()
        self.compilation = compile_claude(self.config)

    def test_emits_exact_artifact_inventory_in_stable_order(self) -> None:
        paths = tuple(
            artifact.relative_path for artifact in self.compilation.emitted_files()
        )
        self.assertEqual(
            paths,
            (
                "settings.patch.json",
                "agents/scout.md",
                "agents/Explore.md",
                "agents/mech-executor.md",
                "agents/executor.md",
                "agents/senior-executor.md",
                "agents/verifier.md",
                "agents/security-executor.md",
                "claude-md.orchestration.md",
                "capability-report.json",
            ),
        )
        self.assertNotIn("agents/orchestrator.md", paths)

    def test_generated_agents_match_checked_in_golden_bytes(self) -> None:
        for artifact in self.compilation.artifacts.role_definitions:
            with self.subTest(path=artifact.relative_path):
                golden = ROOT / "templates" / artifact.relative_path
                self.assertEqual(artifact.content, golden.read_bytes())

    def test_policy_and_machine_patch_match_checked_in_golden_bytes(self) -> None:
        settings = self.compilation.artifacts.machine_settings_patch
        policy = self.compilation.artifacts.orchestration_policy
        self.assertEqual(
            settings.content,
            (ROOT / "templates" / "settings.snippet.json").read_bytes(),
        )
        self.assertEqual(
            policy.content,
            (ROOT / "templates" / "claude-md.orchestration.md").read_bytes(),
        )
        self.assertEqual(
            json.loads(settings.content),
            {"model": "best", "fallbackModel": ["opus", "sonnet"]},
        )

    def test_byte_sensitive_sources_and_goldens_are_lf_pinned(self) -> None:
        paths = sorted(
            path.relative_to(ROOT).as_posix()
            for root in (
                ROOT / "adapters" / "claude" / "templates",
                ROOT / "templates" / "agents",
            )
            for path in root.iterdir()
            if path.is_file()
        )
        paths.extend(
            (
                "templates/claude-md.orchestration.md",
                "templates/settings.snippet.json",
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            git_environment = dict(os.environ)
            git_environment.pop("GIT_DIR", None)
            git_environment.pop("GIT_WORK_TREE", None)
            initialized = subprocess.run(
                ["git", "init", "--quiet"],
                cwd=repository,
                env=git_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            shutil.copyfile(ROOT / ".gitattributes", repository / ".gitattributes")
            completed = subprocess.run(
                ["git", "check-attr", "eol", "--", *paths],
                cwd=repository,
                env=git_environment,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        attributes = {
            line.split(": ", 2)[0]: line.split(": ", 2)[2]
            for line in completed.stdout.splitlines()
        }
        self.assertEqual(attributes, {path: "lf" for path in paths})

    def test_repeated_compiles_are_byte_stable(self) -> None:
        first = tuple(
            (artifact.relative_path, artifact.content)
            for artifact in self.compilation.emitted_files()
        )
        second = tuple(
            (artifact.relative_path, artifact.content)
            for artifact in compile_claude(self.config).emitted_files()
        )
        self.assertEqual(first, second)

    def test_mapping_insertion_order_does_not_change_output(self) -> None:
        reordered = copy.deepcopy(self.config)
        reordered["roles"] = dict(reversed(tuple(reordered["roles"].items())))
        original = tuple(
            (item.relative_path, item.content)
            for item in self.compilation.emitted_files()
        )
        actual = tuple(
            (item.relative_path, item.content)
            for item in compile_claude(reordered).emitted_files()
        )
        self.assertEqual(original, actual)

    def test_path_input_and_generic_entrypoint_match(self) -> None:
        from_path = compile_adapter(ROOT / "routing.yaml", target="claude")
        self.assertEqual(
            tuple(item.content for item in from_path.emitted_files()),
            tuple(item.content for item in self.compilation.emitted_files()),
        )

    def test_output_is_independent_of_cwd_timezone_locale_and_hash_seed(self) -> None:
        program = """
import json
from adapters.claude import compile_claude
result = compile_claude()
print(json.dumps({item.relative_path: item.sha256 for item in result.emitted_files()}, sort_keys=True))
"""
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            first_cwd = root / "first"
            second_cwd = root / "second"
            first_cwd.mkdir()
            second_cwd.mkdir()

            base = os.environ.copy()
            existing_path = base.get("PYTHONPATH")
            base["PYTHONPATH"] = str(ROOT) + (
                os.pathsep + existing_path if existing_path else ""
            )

            first_env = base | {
                "PYTHONHASHSEED": "1",
                "TZ": "UTC",
                "LC_ALL": "C",
                "LANG": "C",
            }
            second_env = base | {
                "PYTHONHASHSEED": "8675309",
                "TZ": "Asia/Taipei",
                "LC_ALL": "C.UTF-8",
                "LANG": "C.UTF-8",
            }
            first = subprocess.run(
                [sys.executable, "-c", program],
                cwd=first_cwd,
                env=first_env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            second = subprocess.run(
                [sys.executable, "-c", program],
                cwd=second_cwd,
                env=second_env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        self.assertEqual(first, second)


class ClaudeAgentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.roles = load_canonical_config()["roles"]
        self.compilation = compile_claude()
        self.artifacts = {
            Path(item.relative_path).stem: item
            for item in self.compilation.artifacts.role_definitions
        }

    def test_semantic_frontmatter_matches_every_registry_binding(self) -> None:
        self.assertEqual(tuple(self.artifacts), LEAF_ROLE_ORDER)
        for name in LEAF_ROLE_ORDER:
            with self.subTest(role=name):
                expected = self.roles[name]
                parsed = parse_agent_definition(self.artifacts[name].content)
                self.assertEqual(parsed.name, name)
                self.assertEqual(parsed.model, expected["model_alias"])
                self.assertEqual(parsed.effort, expected["effort"])
                self.assertEqual(parsed.tools, tuple(expected["allowed_tools"]))
                self.assertEqual(
                    parsed.disallowed_tools,
                    tuple(expected["disallowed_tools"]),
                )
                self.assertIn("You are a leaf agent", parsed.body)
                self.assertIn("Never delegate", parsed.body)

    def test_all_leaf_roles_enforce_no_child_spawn(self) -> None:
        for name, artifact in self.artifacts.items():
            with self.subTest(role=name):
                parsed = parse_agent_definition(artifact.content)
                self.assertFalse(self.roles[name]["can_spawn"])
                self.assertLessEqual(SPAWN_TOOLS, set(parsed.disallowed_tools))
                self.assertIn("Agent and Workflow tools are unavailable", parsed.body)

    def test_recon_roles_have_positive_read_only_allowlists(self) -> None:
        for name in ("scout", "Explore"):
            with self.subTest(role=name):
                parsed = parse_agent_definition(self.artifacts[name].content)
                self.assertEqual(parsed.tools, ("Read", "Glob", "Grep"))
                self.assertLessEqual(
                    WRITE_TOOLS | SPAWN_TOOLS,
                    set(parsed.disallowed_tools),
                )

    def test_verifier_is_read_and_run_only_with_closed_verdict(self) -> None:
        verifier = parse_agent_definition(self.artifacts["verifier"].content)
        self.assertEqual(verifier.tools, ("Read", "Glob", "Grep", "Bash"))
        self.assertLessEqual(
            WRITE_TOOLS | SPAWN_TOOLS,
            set(verifier.disallowed_tools),
        )
        self.assertIn("first line must be `CONFIRMED`", verifier.body)
        self.assertIn("or `REFUTED`", verifier.body)
        self.assertIn("Never invent a third verdict", verifier.body)
        self.assertIn("do not force either verdict", verifier.body)
        self.assertIn("records it on the verifier run", verifier.body)
        self.assertIn("never as a parent-task failure", verifier.body)
        self.assertIn("Never plan, edit, or fix anything", verifier.body)

    def test_bash_capable_roles_never_detach_long_work(self) -> None:
        for name in (
            "mech-executor",
            "executor",
            "senior-executor",
            "verifier",
            "security-executor",
        ):
            with self.subTest(role=name):
                body = parse_agent_definition(self.artifacts[name].content).body
                self.assertIn("run commands in the foreground", body)
                self.assertIn("Never detach", body)
                self.assertIn("absolute working directory or isolated worktree", body)
                self.assertIn("every required environment variable", body)
                self.assertIn("orchestrator runs it in that exact context", body)
                self.assertNotIn("launch it detached", body)

    def test_named_role_has_one_model_binding_source(self) -> None:
        policy = self.compilation.artifacts.orchestration_policy.text()
        for name, artifact in self.artifacts.items():
            with self.subTest(role=name):
                text = artifact.text()
                frontmatter, body = text.split("---", 2)[1:]
                self.assertEqual(frontmatter.count("\nmodel:"), 1)
                self.assertNotIn("model:", body)
                self.assertEqual(
                    self.roles[name]["model_binding_source"],
                    "role_registry",
                )
        for alias in ("haiku", "sonnet", "opus"):
            self.assertNotIn(alias, policy.casefold())
        self.assertIn("omit the `model` argument entirely", policy)

    def test_orchestrator_is_virtual_and_not_an_agent_artifact(self) -> None:
        policy = self.compilation.artifacts.orchestration_policy.text()
        self.assertIn("virtual `orchestrator`", policy)
        self.assertNotIn("orchestrator", self.artifacts)
        self.assertTrue(self.roles["orchestrator"]["can_spawn"])


class ClaudeCompilerFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_canonical_config()

    def _assert_registry_rejected_before_render(self, mutated: object) -> None:
        with mock.patch("adapters.claude.compiler._render_agent") as renderer:
            with self.assertRaises(ClaudeCompileError):
                compile_claude(mutated)  # type: ignore[arg-type]
        renderer.assert_not_called()

    def test_missing_role_fails_before_output(self) -> None:
        mutated = copy.deepcopy(self.config)
        del mutated["roles"]["senior-executor"]
        self._assert_registry_rejected_before_render(mutated)

    def test_extra_role_fails_before_output(self) -> None:
        mutated = copy.deepcopy(self.config)
        mutated["roles"]["invented-role"] = copy.deepcopy(
            mutated["roles"]["executor"]
        )
        self._assert_registry_rejected_before_render(mutated)

    def test_wrong_binding_fails_before_output(self) -> None:
        mutated = copy.deepcopy(self.config)
        mutated["roles"]["executor"]["model_alias"] = "opus"
        self._assert_registry_rejected_before_render(mutated)

    def test_spawn_capable_leaf_fails_before_output(self) -> None:
        mutated = copy.deepcopy(self.config)
        mutated["roles"]["executor"]["can_spawn"] = True
        self._assert_registry_rejected_before_render(mutated)

    def test_missing_tool_control_fails_before_output(self) -> None:
        mutated = copy.deepcopy(self.config)
        mutated["roles"]["verifier"]["disallowed_tools"].remove("Write")
        self._assert_registry_rejected_before_render(mutated)

    def test_second_model_owner_fails_before_output(self) -> None:
        mutated = copy.deepcopy(self.config)
        mutated["roles"]["executor"]["model_binding_source"] = "policy"
        self._assert_registry_rejected_before_render(mutated)

    def test_malformed_frontmatter_is_rejected_semantically(self) -> None:
        malformed = """---
name: executor
description: not-quoted
model: sonnet
effort: high
---
prompt
"""
        with self.assertRaisesRegex(ClaudeCompileError, "quoted YAML scalar"):
            parse_agent_definition(malformed)

    def test_unsupported_target_is_explicit(self) -> None:
        with self.assertRaisesRegex(ClaudeCompileError, "unsupported adapter target"):
            compile_adapter(self.config, target="codex")


class ClaudeCapabilityReportTests(unittest.TestCase):
    def test_report_uses_complete_canonical_vocabulary(self) -> None:
        report = compile_claude().capability_report
        document = json.loads(report.to_bytes())
        self.assertEqual(tuple(report.to_dict()["capabilities"]), CAPABILITY_ORDER)
        self.assertEqual(set(document["capabilities"]), set(CAPABILITY_ORDER))
        self.assertEqual(
            document["capabilities"]["runtime_model_observation"],
            "degraded",
        )
        for name in set(CAPABILITY_ORDER) - {"runtime_model_observation"}:
            self.assertEqual(document["capabilities"][name], "supported")

    def test_report_declares_conditional_runtime_baseline_and_evidence(self) -> None:
        document = compile_claude().capability_report.to_dict()
        self.assertEqual(
            document["runtime_requirements"],
            {"claude_code": ">=2.1.207"},
        )
        self.assertEqual(
            document["evidence"]["tool_enforcement_baseline"],
            "upstream-declared verified baseline; not live-probed by this compiler",
        )
        self.assertTrue(
            any("does not live-probe" in warning for warning in document["warnings"])
        )

    def test_strict_compile_accepts_supported_required_capability(self) -> None:
        result = compile_claude(
            strict=True,
            required_capabilities=("child_spawn_control",),
        )
        self.assertEqual(
            result.capability_report.required_capabilities,
            ("child_spawn_control",),
        )

    def test_strict_compile_rejects_degraded_required_capability(self) -> None:
        with self.assertRaisesRegex(
            ClaudeCompileError,
            "runtime_model_observation",
        ):
            compile_claude(
                strict=True,
                required_capabilities=("runtime_model_observation",),
            )

    def test_non_strict_compile_reports_required_degradation(self) -> None:
        result = compile_claude(
            required_capabilities=("runtime_model_observation",),
        )
        self.assertTrue(
            any(
                "required capabilities are not fully supported" in warning
                for warning in result.capability_report.warnings
            )
        )

    def test_unknown_required_capability_fails(self) -> None:
        with self.assertRaisesRegex(ClaudeCompileError, "unknown required"):
            compile_claude(required_capabilities=("telepathy",))

    def test_report_json_is_byte_stable_and_has_no_timestamp(self) -> None:
        first = compile_claude().capability_report.to_bytes()
        second = compile_claude().capability_report.to_bytes()
        self.assertEqual(first, second)
        self.assertNotIn(b"timestamp", first)
        self.assertTrue(first.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
