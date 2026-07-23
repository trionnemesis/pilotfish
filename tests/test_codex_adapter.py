from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from adapters.codex import (
    CAPABILITY_ORDER,
    MINIMUM_CODEX_VERSION,
    CodexCompileError,
    attest_codex,
    compile_codex,
    probe_codex,
    probe_from_outputs,
)
from adapters.claude import compile_adapter
from runtime.models import RuntimeRecordError


ROOT = Path(__file__).resolve().parents[1]

RECORDED_ROOT_HELP = """Codex CLI
Commands:
  exec  Run Codex non-interactively
Options:
  --model <MODEL>
  --sandbox <SANDBOX_MODE>
  --ask-for-approval <APPROVAL_POLICY>
  --cd <DIR>
  --add-dir <DIR>
  --dangerously-bypass-approvals-and-sandbox
"""
RECORDED_EXEC_HELP = """Run Codex non-interactively
Options:
  --model <MODEL>
  --sandbox <SANDBOX_MODE>
  --ask-for-approval <APPROVAL_POLICY>
  --cd <DIR>
  --add-dir <DIR>
  --ephemeral
  --ignore-user-config
  --output-schema <FILE>
  --json
  --dangerously-bypass-approvals-and-sandbox
"""
RECORDED_FEATURES = """fast_mode experimental false
multi_agent stable true
"""
RECORDED_CONFIG_LOAD = "generated agent config accepted\n"
CODEX_ROLE_MAP = {
    "scout": ("gpt-5.6-terra", "low", "read-only"),
    "Explore": ("gpt-5.6-terra", "low", "read-only"),
    "mech-executor": ("gpt-5.6-luna", "low", None),
    "executor": ("gpt-5.6-terra", "high", None),
    "senior-executor": ("gpt-5.6-sol", "high", None),
    "verifier": ("gpt-5.6-sol", "medium", "read-only"),
    "security-executor": ("gpt-5.6-sol", "high", None),
}

FAKE_CODEX = r'''
import json
import os
import sys
from pathlib import Path

Path(os.environ["HOME"], "probe-touch").write_text("isolated", encoding="utf-8")
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9")
elif args == ["--help"]:
    print("""Codex CLI
Commands:
  exec  Run Codex non-interactively
Options:
  --model <MODEL>
  --sandbox <MODE>
  --ask-for-approval <POLICY>
  --cd <DIR>
  --add-dir <DIR>
  --dangerously-bypass-approvals-and-sandbox
""")
elif args == ["exec", "--help"]:
    print("""Run Codex non-interactively
Options:
  --model <MODEL>
  --sandbox <MODE>
  --ask-for-approval <POLICY>
  --cd <DIR>
  --add-dir <DIR>
  --ephemeral
  --ignore-user-config
  --output-schema <FILE>
  --json
  --dangerously-bypass-approvals-and-sandbox
""")
elif args == ["features", "list"]:
    print("multi_agent stable true")
elif args == ["app-server", "--stdio"]:
    agent_file = Path(os.environ["CODEX_HOME"], "agents", "pilotfish-probe.toml")
    if not agent_file.is_file():
        raise SystemExit(3)
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("id") == 0:
            print(json.dumps({"id": 0, "result": {"codexHome": os.environ["CODEX_HOME"]}}), flush=True)
        elif message.get("id") == 1:
            print(json.dumps({"id": 1, "result": {"config": {}}}), flush=True)
else:
    raise SystemExit(2)
'''


def recorded_probe():
    return probe_from_outputs(
        version_output="codex-cli 0.144.5\n",
        root_help=RECORDED_ROOT_HELP,
        exec_help=RECORDED_EXEC_HELP,
        features_output=RECORDED_FEATURES,
        config_load_output=RECORDED_CONFIG_LOAD,
    )


class CodexProbeTests(unittest.TestCase):
    def test_recorded_probe_covers_supported_degraded_and_unsupported(self) -> None:
        probe = recorded_probe()
        self.assertTrue(probe.available)
        self.assertTrue(probe.binary_available)
        self.assertTrue(probe.compatible)
        self.assertEqual(probe.minimum_version, MINIMUM_CODEX_VERSION)
        self.assertEqual(probe.version, "0.144.5")
        self.assertTrue(probe.config_load)
        self.assertEqual(probe.target_configuration, "unknown")
        self.assertEqual(probe.future_project_overrides, "unknown")
        self.assertEqual(tuple(probe.capability_map()), CAPABILITY_ORDER)
        self.assertEqual(
            set(probe.capability_map().values()),
            {"supported", "degraded", "unsupported"},
        )
        self.assertEqual(
            probe.capability_map()["fresh_context_verifier"], "supported"
        )
        self.assertEqual(
            probe.capability_map()["runtime_model_observation"], "unsupported"
        )
        self.assertEqual(
            probe.capability_map()["child_spawn_control"], "degraded"
        )
        self.assertTrue(
            any("no-spawn" in warning for warning in probe.warnings)
        )

    def test_exact_five_incompatible_classes_fail_closed(self) -> None:
        cases = {
            "missing_binary": dict(
                version_output="",
                root_help="",
                exec_help="",
                features_output="",
                config_load_output="",
                returncodes=(None, None, None, None, None),
                errors=(
                    "executable not found",
                    "executable not found",
                    "executable not found",
                    "executable not found",
                    "executable not found",
                ),
            ),
            "below_minimum": dict(version_output="codex-cli 0.144.4\n"),
            "prerelease": dict(version_output="codex-cli 0.145.0-alpha.1\n"),
            "unparsable_version": dict(version_output="codex-cli latest\n"),
            "required_surface": dict(features_output="multi_agent stable false\n"),
        }
        defaults = {
            "version_output": "codex-cli 0.144.5\n",
            "root_help": RECORDED_ROOT_HELP,
            "exec_help": RECORDED_EXEC_HELP,
            "features_output": RECORDED_FEATURES,
            "config_load_output": RECORDED_CONFIG_LOAD,
        }
        for expected, overrides in cases.items():
            with self.subTest(expected=expected):
                probe = probe_from_outputs(**(defaults | overrides))
                self.assertFalse(probe.compatible)
                self.assertEqual(probe.incompatibility, expected)
                with self.assertRaisesRegex(CodexCompileError, expected):
                    compile_codex(probe=probe, strict=True)

    def test_missing_multi_agent_and_config_rejection_share_required_surface(self) -> None:
        for features, config in (
            ("", RECORDED_CONFIG_LOAD),
            ("multi_agent stable false\n", RECORDED_CONFIG_LOAD),
            (RECORDED_FEATURES, ""),
        ):
            with self.subTest(features=features, config=bool(config)):
                probe = probe_from_outputs(
                    version_output="codex-cli 0.144.5\n",
                    root_help=RECORDED_ROOT_HELP,
                    exec_help=RECORDED_EXEC_HELP,
                    features_output=features,
                    config_load_output=config,
                )
                self.assertEqual(probe.incompatibility, "required_surface")

    def test_partial_and_timeout_evidence_is_normalized_and_bounded(self) -> None:
        probe = probe_from_outputs(
            version_output="x" * 300_000,
            root_help=RECORDED_ROOT_HELP,
            exec_help=RECORDED_EXEC_HELP,
            features_output=RECORDED_FEATURES,
            config_load_output=RECORDED_CONFIG_LOAD,
            returncodes=(None, 0, 0, 0, 0),
            errors=("command timed out", None, None, None, None),
        )
        self.assertEqual(probe.incompatibility, "unparsable_version")
        self.assertEqual(len(probe.commands[0].stdout), 262_144)
        self.assertEqual(probe.commands[0].error, "command timed out")

    def test_target_and_future_override_boundaries_are_explicit(self) -> None:
        arguments = dict(
            version_output="codex-cli 0.144.5\n",
            root_help=RECORDED_ROOT_HELP,
            exec_help=RECORDED_EXEC_HELP,
            features_output=RECORDED_FEATURES,
            config_load_output=RECORDED_CONFIG_LOAD,
            target_configuration="enabled",
        )
        probe = probe_from_outputs(**arguments)
        self.assertEqual(probe.target_configuration, "enabled")
        self.assertEqual(probe.future_project_overrides, "unknown")
        self.assertEqual(probe.warnings, probe_from_outputs(**arguments).warnings)

    def test_json_events_do_not_claim_runtime_model_observation(self) -> None:
        probe = recorded_probe()
        self.assertTrue(probe.surface_map()["structured_events"])
        self.assertEqual(
            probe.capability_map()["runtime_model_observation"], "unsupported"
        )

    def test_missing_executable_returns_explicit_unsupported_probe(self) -> None:
        probe = probe_codex(("pilotfish-codex-does-not-exist",), timeout_seconds=0.1)
        self.assertFalse(probe.available)
        self.assertIsNone(probe.version)
        self.assertEqual(set(probe.capability_map().values()), {"unsupported"})
        self.assertTrue(all(item["error"] for item in probe.to_dict()["commands"]))

    def test_probe_is_bounded_to_an_isolated_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake = root / "fake_codex.py"
            fake.write_text(FAKE_CODEX, encoding="utf-8")
            user_home = root / "user-home"
            user_home.mkdir()
            environment = dict(os.environ)
            environment["HOME"] = str(user_home)
            environment["USERPROFILE"] = str(user_home)
            probe = probe_codex(
                (sys.executable, str(fake)),
                timeout_seconds=2,
                environment=environment,
            )
            self.assertFalse((user_home / "probe-touch").exists())

        self.assertTrue(probe.available)
        self.assertEqual(probe.version, "9.9.9")
        self.assertEqual(
            [item.name for item in probe.commands],
            ["version", "root_help", "exec_help", "features", "config_load"],
        )

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI is not installed")
    def test_live_probe_smoke_does_not_write_user_config(self) -> None:
        config = Path.home() / ".codex" / "config.toml"
        before = (
            hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else None
        )
        probe = probe_codex(timeout_seconds=5)
        after = hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else None

        self.assertTrue(probe.available)
        self.assertIsNotNone(probe.version)
        self.assertEqual(before, after)


class CodexCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = recorded_probe()

    def test_compiler_emits_stable_probe_bounded_artifacts(self) -> None:
        first = compile_codex(probe=self.probe)
        second = compile_codex(probe=self.probe)
        self.assertEqual(
            [(item.relative_path, item.content) for item in first.emitted_files()],
            [(item.relative_path, item.content) for item in second.emitted_files()],
        )
        self.assertEqual(
            [item.relative_path for item in first.emitted_files()],
            [
                "agents/scout.toml",
                "agents/Explore.toml",
                "agents/mech-executor.toml",
                "agents/executor.toml",
                "agents/senior-executor.toml",
                "agents/verifier.toml",
                "agents/security-executor.toml",
                "AGENTS.orchestration.md",
                "invocation-policy.json",
                "verifier-output.schema.json",
                "capability-report.json",
            ],
        )

    def test_native_agents_parse_and_match_reviewed_role_map(self) -> None:
        compilation = compile_codex(probe=self.probe)
        agents = {
            item.relative_path.removeprefix("agents/").removesuffix(".toml"): tomllib.loads(
                item.text()
            )
            for item in compilation.artifacts
            if item.relative_path.startswith("agents/")
        }
        self.assertEqual(tuple(agents), tuple(CODEX_ROLE_MAP))
        self.assertNotIn("orchestrator", agents)
        for name, (model, effort, sandbox) in CODEX_ROLE_MAP.items():
            with self.subTest(name=name):
                document = agents[name]
                self.assertEqual(document["name"], name)
                self.assertTrue(document["description"].strip())
                self.assertTrue(document["developer_instructions"].strip())
                self.assertIn("Do not spawn", document["developer_instructions"])
                self.assertEqual(document["model"], model)
                self.assertEqual(document["model_reasoning_effort"], effort)
                self.assertEqual(document.get("sandbox_mode"), sandbox)
                self.assertNotIn("agents", document)

    def test_native_agents_and_policy_match_checked_in_golden_bytes(self) -> None:
        compilation = compile_codex(probe=self.probe)
        for item in compilation.artifacts:
            if not (
                item.relative_path.startswith("agents/")
                or item.relative_path == "AGENTS.orchestration.md"
            ):
                continue
            with self.subTest(path=item.relative_path):
                golden = ROOT / "adapters" / "codex" / "templates" / item.relative_path
                self.assertEqual(item.content, golden.read_bytes())

    def test_native_text_artifacts_are_lf_deterministic_and_safe(self) -> None:
        compilation = compile_codex(probe=self.probe)
        forbidden = (
            "dangerously-bypass",
            "bypass-hook-trust",
            "api_key",
            "auth.json",
            "mcp_servers",
            "glpat-",
            "github_pat_",
        )
        for item in compilation.artifacts:
            if item.relative_path.endswith((".toml", ".md")):
                text = item.text()
                self.assertNotIn("\r", text)
                self.assertTrue(text.endswith("\n"))
                for marker in forbidden:
                    self.assertNotIn(marker, text.casefold(), item.relative_path)
        self.assertNotIn("gpt-5.6", (ROOT / "routing.yaml").read_text(encoding="utf-8"))

    def test_native_goldens_are_lf_pinned(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("adapters/codex/templates/** text eol=lf", attributes)

    def test_generic_adapter_dispatches_to_codex(self) -> None:
        with mock.patch(
            "adapters.codex.compiler.probe_codex", return_value=self.probe
        ):
            compilation = compile_adapter(target="codex")
        self.assertEqual(compilation.capability_report.to_dict()["target"], "codex")

    def test_invocation_policy_contains_only_observed_controls(self) -> None:
        compilation = compile_codex(probe=self.probe)
        policy = json.loads(
            next(
                item.content
                for item in compilation.artifacts
                if item.relative_path == "invocation-policy.json"
            )
        )
        self.assertIn("--sandbox", policy["default_arguments"])
        self.assertLess(
            policy["default_arguments"].index("--ask-for-approval"),
            policy["default_arguments"].index("exec"),
        )
        self.assertIn("--output-schema", policy["verifier_arguments"])
        self.assertNotIn("--model", policy["default_arguments"])
        self.assertEqual(
            policy["role_enforcement"]["model_binding"], "native_custom_agent"
        )
        self.assertEqual(
            policy["role_enforcement"]["write_role_sandbox"], "inherited"
        )
        self.assertEqual(
            policy["role_enforcement"]["positive_tool_allowlists"],
            "prompt_guidance",
        )
        self.assertEqual(
            policy["role_enforcement"]["child_spawn_control"],
            "prompt_guidance",
        )
        self.assertEqual(
            policy["attestation"]["runtime_model_observation"], "UNKNOWN"
        )
        self.assertEqual(policy["attestation"]["account_availability"], "UNKNOWN")

        unavailable = probe_from_outputs(
            version_output="",
            root_help="",
            exec_help="",
            features_output="",
            config_load_output="",
            returncodes=(None, None, None, None, None),
            errors=("missing", "missing", "missing", "missing", "missing"),
        )
        unavailable_policy = json.loads(
            next(
                item.content
                for item in compile_codex(probe=unavailable).artifacts
                if item.relative_path == "invocation-policy.json"
            )
        )
        self.assertEqual(unavailable_policy["default_arguments"], [])
        self.assertEqual(unavailable_policy["verified_controls"], {})

    def test_policy_labels_role_controls_as_prompt_only(self) -> None:
        policy = next(
            item.text()
            for item in compile_codex(probe=self.probe).artifacts
            if item.relative_path == "AGENTS.orchestration.md"
        )
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertIn(f"pilotfish v{version}", policy)
        self.assertIn("native controls", policy)
        self.assertIn("prompt guidance", policy)
        self.assertIn("executable canonical router", policy)
        self.assertIn("dispatch eligibility brake", policy)
        self.assertIn("no-downgrade", policy)
        self.assertIn("fresh-context verifier", policy)
        self.assertIn("multi-agent V2", policy)
        self.assertIn("### security-executor", policy)
        self.assertNotIn("gpt-5.6", policy)

    def test_strict_mode_accepts_only_supported_required_capabilities(self) -> None:
        supported = [
            name
            for name, status in self.probe.capabilities
            if status == "supported"
        ]
        for name in supported:
            with self.subTest(supported=name):
                compile_codex(
                    probe=self.probe,
                    strict=True,
                    required_capabilities=(name,),
                )
        for name, status in self.probe.capabilities:
            if status == "supported":
                continue
            with self.subTest(unavailable=name, status=status):
                with self.assertRaisesRegex(CodexCompileError, name):
                    compile_codex(
                        probe=self.probe,
                        strict=True,
                        required_capabilities=(name,),
                    )

    def test_non_strict_mode_preserves_required_degradation_warning(self) -> None:
        compilation = compile_codex(
            probe=self.probe,
            required_capabilities=("per_role_tool_policy",),
        )
        report = compilation.capability_report.to_dict()
        self.assertEqual(report["capabilities"]["per_role_tool_policy"], "degraded")
        self.assertTrue(
            any("not fully supported" in warning for warning in report["warnings"])
        )

    def test_unknown_or_duplicate_required_capability_fails(self) -> None:
        with self.assertRaisesRegex(CodexCompileError, "unknown"):
            compile_codex(probe=self.probe, required_capabilities=("telepathy",))
        with self.assertRaisesRegex(CodexCompileError, "duplicates"):
            compile_codex(
                probe=self.probe,
                required_capabilities=("fresh_context_verifier", "fresh_context_verifier"),
            )

    def test_capability_report_contains_hashes_not_raw_help(self) -> None:
        report = compile_codex(probe=self.probe).capability_report.to_dict()
        serialized = json.dumps(report)
        self.assertNotIn(RECORDED_EXEC_HELP, serialized)
        self.assertEqual(report["cli"]["version"], "0.144.5")
        self.assertEqual(report["cli"]["minimum_supported"], "0.144.5")
        self.assertTrue(report["cli"]["compatible"])
        self.assertTrue(report["probe"]["config_load"])
        self.assertEqual(report["target_configuration"], "unknown")
        self.assertEqual(report["future_project_overrides"], "unknown")
        self.assertEqual(report["runtime"]["model_availability"], "UNKNOWN")
        self.assertEqual(report["runtime"]["account_availability"], "UNKNOWN")
        self.assertEqual(
            report["role_mappings"]["mech-executor"]["model"], "gpt-5.6-luna"
        )
        for command in report["probe"]["commands"]:
            self.assertRegex(command["stdout_sha256"], r"^[0-9a-f]{64}$")

    def test_capability_report_does_not_leak_raw_probe_output(self) -> None:
        marker = "PRIVATE-PROBE-MARKER"
        probe = probe_from_outputs(
            version_output="codex-cli 0.144.5\n",
            root_help=RECORDED_ROOT_HELP + marker,
            exec_help=RECORDED_EXEC_HELP + marker,
            features_output=RECORDED_FEATURES + marker,
            config_load_output=RECORDED_CONFIG_LOAD,
        )
        report = json.dumps(compile_codex(probe=probe).capability_report.to_dict())
        self.assertNotIn(marker, report)


class CodexAttestorTests(unittest.TestCase):
    def test_missing_observation_remains_unknown(self) -> None:
        attestation = attest_codex("gpt-5")
        self.assertEqual(attestation.status, "UNKNOWN")
        self.assertIsNone(attestation.observed_model)
        self.assertFalse(attestation.invalidates_run)

    def test_structured_observation_matches_or_invalidates(self) -> None:
        matched = attest_codex(
            "gpt-5", runtime_metadata={"model": "gpt-5", "source": "provider"}
        )
        mismatched = attest_codex(
            "gpt-5", runtime_metadata={"model": "gpt-4", "source": "provider"}
        )
        self.assertEqual(matched.status, "MATCHED")
        self.assertEqual(mismatched.status, "MISMATCHED")
        self.assertTrue(mismatched.invalidates_run)

    def test_raw_event_streams_and_unsourced_models_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeRecordError, "unsupported fields"):
            attest_codex("gpt-5", runtime_metadata={"events": []})
        with self.assertRaisesRegex(RuntimeRecordError, "evidence source"):
            attest_codex("gpt-5", runtime_metadata={"model": "gpt-5"})


if __name__ == "__main__":
    unittest.main()
