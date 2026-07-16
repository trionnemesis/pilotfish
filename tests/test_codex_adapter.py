from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
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
                "codex-policy.md",
                "invocation-policy.json",
                "verifier-output.schema.json",
                "capability-report.json",
            ],
        )

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
        self.assertIsNone(policy["role_enforcement"]["model_alias_mapping"])
        self.assertEqual(
            policy["attestation"]["runtime_model_observation"], "UNKNOWN"
        )

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
        policy = compile_codex(probe=self.probe).artifacts[0].text()
        self.assertIn("prompt-level", policy)
        self.assertIn("not a Codex model ID", policy)
        self.assertIn("### security-executor", policy)
        self.assertNotIn("--model opus", policy)

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
