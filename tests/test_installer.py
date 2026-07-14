from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from adapters.claude import compile_claude
from install.installer import (
    ApprovalRequired,
    Installer,
    InstallerError,
    PlanBlocked,
    _validate_windows_target_home,
    _windows_process_is_elevated,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
GOOD_VERSION = lambda: "2.1.207 (Claude Code)"
NON_ELEVATED = lambda: False
TEMP_ROOT = Path.home() if os.name == "nt" else None


def strict_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def try_symlink(link: Path, target: Path, *, directory: bool = False) -> bool:
    try:
        link.symlink_to(target, target_is_directory=directory)
        return True
    except (OSError, NotImplementedError):
        return False


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.home = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def installer(self, **kwargs) -> Installer:
        if os.name == "nt":
            kwargs.setdefault("windows_elevation_probe", NON_ELEVATED)
        return Installer(
            target_home=self.home,
            version_probe=kwargs.pop("version_probe", GOOD_VERSION),
            env=kwargs.pop("env", {}),
            **kwargs,
        )

    def apply_install(self, installer: Installer | None = None):
        installer = installer or self.installer()
        plan = installer.plan_install()
        self.assertFalse(plan.blockers)
        return installer, installer.install(approval=plan.fingerprint)

    def test_target_home_must_be_supplied_existing_non_symlink_directory(self) -> None:
        with self.assertRaises(ValueError):
            Installer(target_home="")
        with self.assertRaises(ValueError):
            Installer(target_home=self.home / "missing")
        target_file = self.home / "file"
        target_file.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            Installer(target_home=target_file)
        real = self.home / "real"
        real.mkdir()
        link = self.home / "link"
        if try_symlink(link, real, directory=True):
            with self.assertRaises(ValueError):
                Installer(target_home=link)

    @unittest.skipUnless(os.name == "nt", "Windows current-profile boundary")
    def test_windows_rejects_target_outside_current_operator_profile(self) -> None:
        filesystem_root = Path(Path.home().anchor)
        with self.assertRaisesRegex(ValueError, "cross-user installs are unsupported"):
            Installer(
                target_home=filesystem_root,
                version_probe=GOOD_VERSION,
                env={},
                windows_elevation_probe=NON_ELEVATED,
            )

    @unittest.skipUnless(os.name == "nt", "Windows TokenElevation integration")
    def test_windows_default_probe_rejects_elevated_process(self) -> None:
        if not _windows_process_is_elevated():
            self.skipTest("current Windows process is not elevated")
        with self.assertRaisesRegex(ValueError, "elevated Windows installs"):
            Installer(target_home=self.home, version_probe=GOOD_VERSION, env={})

    def test_windows_boundary_fails_closed_on_elevation_and_probe_error(self) -> None:
        profile = self.home / "profile"
        target = profile / "target"
        target.mkdir(parents=True)
        _validate_windows_target_home(
            target,
            profile_home=profile,
            elevation_probe=lambda: False,
        )

        with self.assertRaisesRegex(ValueError, "elevated Windows installs"):
            _validate_windows_target_home(
                target,
                profile_home=profile,
                elevation_probe=lambda: True,
            )

        def failed_probe() -> bool:
            raise OSError("token query failed")

        with self.assertRaisesRegex(ValueError, "could not be verified"):
            _validate_windows_target_home(
                target,
                profile_home=profile,
                elevation_probe=failed_probe,
            )

        outside = self.home / "outside-profile"
        outside.mkdir()
        with self.assertRaisesRegex(ValueError, "cross-user installs are unsupported"):
            _validate_windows_target_home(
                outside,
                profile_home=profile,
                elevation_probe=lambda: False,
            )

    def test_dry_run_is_read_only_and_reports_exact_fingerprint(self) -> None:
        before = list(self.home.rglob("*"))
        result = self.installer().install(dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertIsNotNone(result.plan)
        self.assertRegex(result.plan["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(before, list(self.home.rglob("*")))
        paths = {change["path"] for change in result.plan["changes"]}
        self.assertIn(".claude/settings.json", paths)
        self.assertIn(".claude/agents/senior-executor.md", paths)

    def test_runtime_gate_blocks_unknown_unparseable_and_old_versions(self) -> None:
        for probe in (lambda: None, lambda: "unknown", lambda: "2.1.206"):
            with self.subTest(probe=probe()):
                installer = self.installer(version_probe=probe)
                plan = installer.plan_install()
                self.assertTrue(any("2.1.207" in item for item in plan.blockers))
                with self.assertRaises(PlanBlocked):
                    installer.install(approval=plan.fingerprint)
        self.assertFalse((self.home / ".claude").exists())

    def test_override_env_key_warns_without_leaking_even_when_empty(self) -> None:
        plan = self.installer(env={"CLAUDE_CODE_SUBAGENT_MODEL": ""}).plan_install()
        joined = " ".join(plan.warnings)
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL", joined)
        secret = "do-not-print-this-model"
        plan = self.installer(env={"CLAUDE_CODE_SUBAGENT_MODEL": secret}).plan_install()
        self.assertNotIn(secret, " ".join(plan.warnings))

    def test_approval_is_bound_to_plan_and_prewrite_cas(self) -> None:
        installer = self.installer()
        plan = installer.plan_install()
        with self.assertRaises(ApprovalRequired):
            installer.install()
        with self.assertRaises(ApprovalRequired):
            installer.install(approval="0" * 64)
        self.assertFalse((self.home / ".claude").exists())

        prepared = installer._prepare_install()
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "settings.json").write_text('{"external": true}\n', encoding="utf-8")
        with self.assertRaisesRegex(InstallerError, "precondition changed"):
            installer._apply_prepared(prepared, approval=prepared.plan.fingerprint)
        self.assertEqual(strict_json(claude / "settings.json"), {"external": True})

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-local CAS regression")
    def test_descriptor_precondition_rejects_post_plan_write_and_delete_changes(self) -> None:
        installer = self.installer()
        prepared = installer._prepare_install()
        original = installer._write_or_delete
        injected = b'{"external":"post-plan"}\n'
        changed = False

        def change_after_outer_check(path: str, content: bytes | None, **kwargs) -> None:
            nonlocal changed
            if path == ".claude/settings.json" and not changed:
                target = self.home / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(injected)
                changed = True
            original(path, content, **kwargs)

        with mock.patch.object(
            installer, "_write_or_delete", side_effect=change_after_outer_check
        ):
            with self.assertRaisesRegex(InstallerError, "descriptor precondition changed"):
                installer._apply_prepared(
                    prepared, approval=prepared.plan.fingerprint
                )
        target = self.home / ".claude/settings.json"
        self.assertEqual(target.read_bytes(), injected)

        with self.assertRaisesRegex(InstallerError, "descriptor precondition changed"):
            installer._write_or_delete(
                ".claude/settings.json",
                None,
                expected_sha256=hashlib.sha256(b"approved old bytes").hexdigest(),
                expected_mode=stat.S_IMODE(target.stat().st_mode),
            )
        self.assertEqual(target.read_bytes(), injected)

        expected_mode = stat.S_IMODE(target.stat().st_mode)
        shutil.rmtree(target.parent)
        with self.assertRaisesRegex(InstallerError, "descriptor precondition changed"):
            installer._write_or_delete(
                ".claude/settings.json",
                None,
                expected_sha256=hashlib.sha256(injected).hexdigest(),
                expected_mode=expected_mode,
            )

    def test_install_merges_json_emits_seven_roles_and_private_metadata(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings = claude / "settings.json"
        settings.write_text(
            json.dumps({"theme": "dark", "credential": "never-in-manifest"}) + "\n",
            encoding="utf-8",
        )
        settings.chmod(0o640)
        settings_mode = stat.S_IMODE(settings.stat().st_mode)
        claude_md = claude / "CLAUDE.md"
        claude_md.write_text("# User policy\n", encoding="utf-8")
        claude_md.chmod(0o644)
        policy_mode = stat.S_IMODE(claude_md.stat().st_mode)

        installer, result = self.apply_install()
        installed = strict_json(settings)
        self.assertEqual(installed["theme"], "dark")
        self.assertEqual(installed["credential"], "never-in-manifest")
        self.assertEqual(installed["model"], "best")
        self.assertEqual(installed["fallbackModel"], ["opus", "sonnet"])
        self.assertEqual(stat.S_IMODE(settings.stat().st_mode), settings_mode)

        agents = sorted(path.name for path in (claude / "agents").glob("*.md"))
        self.assertEqual(
            agents,
            sorted(
                [
                    "Explore.md",
                    "executor.md",
                    "mech-executor.md",
                    "scout.md",
                    "security-executor.md",
                    "senior-executor.md",
                    "verifier.md",
                ]
            ),
        )
        manifest_path = self.home / result.manifest
        self.assertNotIn("\\", result.manifest)
        manifest_text = manifest_path.read_text(encoding="utf-8")
        state_text = (claude / "pilotfish/state.json").read_text(encoding="utf-8")
        self.assertNotIn("never-in-manifest", manifest_text)
        self.assertNotIn("never-in-manifest", state_text)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)
        manifest = json.loads(manifest_text)
        settings_record = next(
            record for record in manifest["records"] if record["kind"] == "settings"
        )
        self.assertEqual(settings_record["before_mode"], settings_mode)
        self.assertEqual(settings_record["after_mode"], settings_mode)
        backup = self.home / settings_record["backup"]
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertIn("never-in-manifest", backup.read_text(encoding="utf-8"))
        self.assertEqual(
            (claude / "CLAUDE.md").read_text(encoding="utf-8").count("pilotfish:begin"),
            1,
        )
        self.assertEqual(stat.S_IMODE(claude_md.stat().st_mode), policy_mode)

        no_op = installer.plan_install()
        self.assertFalse(no_op.will_write)
        second = installer.install(approval=no_op.fingerprint)
        self.assertIsNone(second.manifest)

    def test_existing_conflicting_setting_is_preserved(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "settings.json").write_text(
            '{"model":"custom","other":1}\n', encoding="utf-8"
        )
        installer = self.installer()
        plan = installer.plan_install()
        self.assertTrue(any("unowned setting 'model'" in warning for warning in plan.warnings))
        installer.install(approval=plan.fingerprint)
        installed = strict_json(claude / "settings.json")
        self.assertEqual(installed["model"], "custom")
        self.assertEqual(installed["other"], 1)

    def test_available_models_missing_aliases_blocks_without_mutation(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings = claude / "settings.json"
        original = '{"availableModels":["sonnet"]}\n'
        settings.write_text(original, encoding="utf-8")
        installer = self.installer()
        plan = installer.plan_install()
        self.assertTrue(any("availableModels blocks" in item for item in plan.blockers))
        with self.assertRaises(PlanBlocked):
            installer.install(approval=plan.fingerprint)
        self.assertEqual(settings.read_text(encoding="utf-8"), original)

    def test_strict_json_rejects_duplicate_non_object_and_nonfinite(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings = claude / "settings.json"
        for content in ('{"x":1,"x":2}', "[]", '{"x":NaN}'):
            with self.subTest(content=content):
                settings.write_text(content, encoding="utf-8")
                with self.assertRaises(InstallerError):
                    self.installer().plan_install()

    def test_collision_scan_uses_frontmatter_name_across_all_filenames(self) -> None:
        agents = self.home / ".claude/agents"
        agents.mkdir(parents=True)
        collision = agents / "my-custom-agent.txt"
        collision.write_text("---\nname: scout\n---\ncustom\n", encoding="utf-8")
        installer = self.installer()
        plan = installer.plan_install()
        self.assertTrue(any("my-custom-agent.txt" in item for item in plan.blockers))
        with self.assertRaises(PlanBlocked):
            installer.install(approval=plan.fingerprint)

    def test_collision_scan_blocks_ambiguous_agent_files(self) -> None:
        agents = self.home / ".claude/agents"
        agents.mkdir(parents=True)
        plain = agents / "notes.txt"
        plain.write_text("ordinary notes without frontmatter\n", encoding="utf-8")
        self.assertFalse(self.installer().plan_install().blockers)
        cases = {
            "bom.md": "\ufeff---\nname: harmless\n---\n",
            "crlf.md": "---\r\nname: harmless\r\n---\r\n",
            "duplicate.md": "---\nname: one\nname: two\n---\n",
            "unclosed.md": "---\nname: one\n",
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                path = agents / filename
                path.write_text(content, encoding="utf-8", newline="")
                with self.assertRaises(InstallerError):
                    self.installer().plan_install()
                path.unlink()
        (agents / "target.md").write_text("plain\n", encoding="utf-8")
        if try_symlink(agents / "linked.md", agents / "target.md"):
            with self.assertRaises(InstallerError):
                self.installer().plan_install()

    def test_marker_errors_and_symlink_escape_fail_closed(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "CLAUDE.md").write_text(
            "<!-- pilotfish:begin -->\nx\n<!-- pilotfish:begin -->\n",
            encoding="utf-8",
        )
        with self.assertRaises(InstallerError):
            self.installer().plan_install()

        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        sentinel = outside / "settings.json"
        sentinel.write_text('{"outside":true}\n', encoding="utf-8")
        shutil.rmtree(claude)
        if try_symlink(claude, outside, directory=True):
            with self.assertRaises(InstallerError):
                self.installer().plan_install()
            self.assertEqual(strict_json(sentinel), {"outside": True})

    def test_target_file_and_directory_symlinks_never_touch_outside_sentinels(self) -> None:
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        for target_rel, directory in (
            ("settings.json", False),
            ("CLAUDE.md", False),
            ("agents", True),
        ):
            with self.subTest(target=target_rel):
                claude = self.home / ".claude"
                if claude.exists():
                    shutil.rmtree(claude)
                claude.mkdir()
                outside_target = outside / target_rel
                if directory:
                    outside_target.mkdir(exist_ok=True)
                else:
                    outside_target.write_text("outside sentinel\n", encoding="utf-8")
                link = claude / target_rel
                if not try_symlink(link, outside_target, directory=directory):
                    continue
                with self.assertRaises(InstallerError):
                    self.installer().plan_install()
                if directory:
                    self.assertEqual(list(outside_target.iterdir()), [])
                else:
                    self.assertEqual(
                        outside_target.read_text(encoding="utf-8"), "outside sentinel\n"
                    )

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-relative race regression")
    def test_descriptor_relative_write_and_delete_resist_parent_swap(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        target = claude / "settings.json"
        target.write_text("inside before\n", encoding="utf-8")
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        outside_target = outside / "settings.json"
        outside_target.write_text("outside sentinel\n", encoding="utf-8")
        installer = self.installer()

        original_rename = os.rename
        displaced = self.home / ".claude-displaced-write"
        swapped = False

        def swap_parent_before_rename(src, dst, *args, **kwargs):
            nonlocal swapped
            if not swapped and dst == "settings.json" and kwargs.get("dst_dir_fd"):
                original_rename(claude, displaced)
                claude.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_rename(src, dst, *args, **kwargs)

        with mock.patch("install.installer.os.rename", side_effect=swap_parent_before_rename):
            with self.assertRaisesRegex(InstallerError, "target parent changed"):
                installer._atomic_write(
                    ".claude/settings.json", b"inside after\n", mode=0o600
                )
        self.assertEqual(outside_target.read_text(encoding="utf-8"), "outside sentinel\n")
        self.assertEqual(
            (displaced / "settings.json").read_text(encoding="utf-8"),
            "inside before\n",
        )

        claude.unlink()
        original_rename(displaced, claude)
        displaced_delete = self.home / ".claude-displaced-delete"
        original_unlink = os.unlink
        swapped = False

        def swap_parent_before_unlink(path, *args, **kwargs):
            nonlocal swapped
            if not swapped and path == "settings.json" and kwargs.get("dir_fd"):
                original_rename(claude, displaced_delete)
                claude.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_unlink(path, *args, **kwargs)

        with mock.patch("install.installer.os.unlink", side_effect=swap_parent_before_unlink):
            with self.assertRaises(InstallerError):
                installer._write_or_delete(".claude/settings.json", None)
        self.assertEqual(outside_target.read_text(encoding="utf-8"), "outside sentinel\n")
        self.assertEqual(
            (displaced_delete / "settings.json").read_text(encoding="utf-8"),
            "inside before\n",
        )

    @unittest.skipIf(os.name == "nt", "POSIX descriptor compensation regression")
    def test_full_install_parent_swap_compensates_displaced_original(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        original = b'{"theme":"dark"}\n'
        (claude / "settings.json").write_bytes(original)
        original_parent = (claude.stat().st_dev, claude.stat().st_ino)
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        outside_target = outside / "settings.json"
        outside_sentinel = b'{"outside":true}\n'
        outside_target.write_bytes(outside_sentinel)
        displaced = self.home / ".claude-displaced-full-operation"
        installer = self.installer()
        plan = installer.plan_install()
        original_rename = os.rename
        swapped = False

        def swap_approved_parent_before_rename(src, dst, *args, **kwargs):
            nonlocal swapped
            parent_fd = kwargs.get("dst_dir_fd")
            if (
                not swapped
                and dst == "settings.json"
                and parent_fd is not None
                and (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino)
                == original_parent
            ):
                original_rename(claude, displaced)
                claude.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_rename(src, dst, *args, **kwargs)

        with mock.patch(
            "install.installer.os.rename", side_effect=swap_approved_parent_before_rename
        ):
            with self.assertRaises(InstallerError):
                installer.install(approval=plan.fingerprint)

        self.assertTrue(swapped)
        self.assertEqual((displaced / "settings.json").read_bytes(), original)
        self.assertEqual(outside_target.read_bytes(), outside_sentinel)

    def test_rollback_rejects_symlinked_manifest_and_backup(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "settings.json").write_text('{"unowned":true}\n', encoding="utf-8")
        installer, installed = self.apply_install()
        manifest_path = self.home / installed.manifest
        manifest_content = manifest_path.read_bytes()
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        outside_manifest = outside / "manifest.json"
        outside_manifest.write_bytes(manifest_content)
        manifest_path.unlink()
        if try_symlink(manifest_path, outside_manifest):
            with self.assertRaises(InstallerError):
                installer.plan_rollback(installed.manifest)
            self.assertEqual(outside_manifest.read_bytes(), manifest_content)
        else:
            manifest_path.write_bytes(manifest_content)
            return

        manifest_path.unlink()
        manifest_path.write_bytes(manifest_content)
        if os.name != "nt":
            manifest_path.chmod(0o600)
        document = strict_json(manifest_path)
        record = next(item for item in document["records"] if item["backup"])
        backup = self.home / record["backup"]
        backup_content = backup.read_bytes()
        outside_backup = outside / "backup"
        outside_backup.write_bytes(backup_content)
        backup.unlink()
        if try_symlink(backup, outside_backup):
            with self.assertRaises(InstallerError):
                installer.plan_rollback(installed.manifest)
            self.assertEqual(outside_backup.read_bytes(), backup_content)

    def test_stale_golden_blocks_install(self) -> None:
        source = self.home / "source"
        shutil.copytree(ROOT / "templates", source / "templates")
        golden = source / "templates/agents/scout.md"
        golden.write_text(golden.read_text(encoding="utf-8") + "stale\n", encoding="utf-8")
        with self.assertRaisesRegex(InstallerError, "golden artifact is stale"):
            self.installer(source_root=source).plan_install()

    def test_owned_update_requires_current_prior_hash(self) -> None:
        installer, _ = self.apply_install()
        compilation = compile_claude()
        roles = list(compilation.artifacts.role_definitions)
        index = next(i for i, item in enumerate(roles) if item.relative_path == "agents/executor.md")
        roles[index] = replace(roles[index], content=roles[index].content + b"\nupdated\n")
        updated = replace(
            compilation,
            artifacts=replace(compilation.artifacts, role_definitions=tuple(roles)),
        )
        source = self.home / "updated-source"
        shutil.copytree(ROOT / "templates", source / "templates")
        (source / "templates/agents/executor.md").write_bytes(roles[index].content)
        updated_installer = self.installer(source_root=source)
        with mock.patch("adapters.claude.compile_claude", return_value=updated):
            plan = updated_installer.plan_update()
            self.assertEqual(plan.operation, "update")
            self.assertTrue(any(change.action == "update" for change in plan.changes))
            result = updated_installer.update(approval=plan.fingerprint)
            self.assertEqual(result.operation, "update")
            self.assertEqual(strict_json(self.home / result.manifest)["operation"], "update")

        target = self.home / ".claude/agents/executor.md"
        target.write_text(target.read_text(encoding="utf-8") + "user edit\n", encoding="utf-8")
        with mock.patch("adapters.claude.compile_claude", return_value=updated):
            plan = updated_installer.plan_update()
        self.assertTrue(any("modified content" in item for item in plan.blockers))

    def test_cli_update_dry_run_reports_update_operation(self) -> None:
        self.apply_install()
        output = io.StringIO()
        with mock.patch.object(Installer, "_probe_claude_version", return_value="2.1.207"), mock.patch(
            "install.installer._windows_process_is_elevated", return_value=False
        ):
            with redirect_stdout(output):
                code = main(
                    [
                        "update",
                        "--target-home",
                        str(self.home),
                        "--dry-run",
                    ]
                )
        self.assertEqual(code, 0)
        document = json.loads(output.getvalue())
        self.assertEqual(document["operation"], "update")
        self.assertEqual(document["plan"]["operation"], "update")

    def test_uninstall_preserves_modified_and_unowned_content(self) -> None:
        installer, _ = self.apply_install()
        claude = self.home / ".claude"
        settings = strict_json(claude / "settings.json")
        settings["model"] = "user-model"
        settings["unowned"] = True
        (claude / "settings.json").write_text(json.dumps(settings) + "\n", encoding="utf-8")
        executor = claude / "agents/executor.md"
        executor.write_text(executor.read_text(encoding="utf-8") + "user edit\n", encoding="utf-8")
        unowned = claude / "agents/custom.md"
        unowned.write_text("plain custom agent\n", encoding="utf-8")
        claude_md = claude / "CLAUDE.md"
        claude_md.write_text(claude_md.read_text(encoding="utf-8") + "user tail\n", encoding="utf-8")

        plan = installer.plan_uninstall()
        result = installer.uninstall(approval=plan.fingerprint)
        self.assertIsNotNone(result.manifest)
        remaining = strict_json(claude / "settings.json")
        self.assertEqual(remaining, {"model": "user-model", "unowned": True})
        self.assertTrue(executor.exists())
        self.assertTrue(unowned.exists())
        self.assertFalse((claude / "agents/scout.md").exists())
        policy_text = claude_md.read_text(encoding="utf-8")
        self.assertNotIn("pilotfish:begin", policy_text)
        self.assertIn("user tail", policy_text)
        self.assertTrue((claude / "pilotfish/state.json").exists())

    def test_uninstall_cleans_already_missing_ownership_and_created_empty_files(self) -> None:
        installer, _ = self.apply_install()
        settings = self.home / ".claude/settings.json"
        document = strict_json(settings)
        document.pop("model")
        settings.write_text(json.dumps(document) + "\n", encoding="utf-8")
        plan = installer.plan_uninstall()
        installer.uninstall(approval=plan.fingerprint)
        self.assertFalse(settings.exists())
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())
        self.assertFalse((self.home / ".claude/pilotfish/state.json").exists())

    def test_uninstall_preserves_preexisting_empty_files(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings = claude / "settings.json"
        policy = claude / "CLAUDE.md"
        settings.write_text("{}\n", encoding="utf-8")
        policy.write_text("", encoding="utf-8")
        installer, _ = self.apply_install()
        plan = installer.plan_uninstall()
        installer.uninstall(approval=plan.fingerprint)
        self.assertEqual(settings.read_text(encoding="utf-8"), "{}\n")
        self.assertEqual(policy.read_text(encoding="utf-8"), "")

    def test_uninstall_and_rollback_restore_exact_policy_separator_bytes(self) -> None:
        originals = ("", "# User", "# User\n", "# User\n\n")
        for original in originals:
            with self.subTest(operation="uninstall", original=repr(original)):
                with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as target_home:
                    home = Path(target_home)
                    claude = home / ".claude"
                    claude.mkdir()
                    policy = claude / "CLAUDE.md"
                    policy.write_text(original, encoding="utf-8")
                    installer = Installer(
                        target_home=home,
                        version_probe=GOOD_VERSION,
                        env={},
                        windows_elevation_probe=(NON_ELEVATED if os.name == "nt" else None),
                    )
                    install_plan = installer.plan_install()
                    installer.install(approval=install_plan.fingerprint)
                    uninstall_plan = installer.plan_uninstall()
                    installer.uninstall(approval=uninstall_plan.fingerprint)
                    self.assertEqual(policy.read_text(encoding="utf-8"), original)

            with self.subTest(operation="rollback", original=repr(original)):
                with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as target_home:
                    home = Path(target_home)
                    claude = home / ".claude"
                    claude.mkdir()
                    policy = claude / "CLAUDE.md"
                    policy.write_text(original, encoding="utf-8")
                    installer = Installer(
                        target_home=home,
                        version_probe=GOOD_VERSION,
                        env={},
                        windows_elevation_probe=(NON_ELEVATED if os.name == "nt" else None),
                    )
                    install_plan = installer.plan_install()
                    installed = installer.install(approval=install_plan.fingerprint)
                    rollback_plan = installer.plan_rollback(installed.manifest)
                    installer.rollback(
                        installed.manifest, approval=rollback_plan.fingerprint
                    )
                    self.assertEqual(policy.read_text(encoding="utf-8"), original)

    @unittest.skipIf(os.name == "nt", "POSIX special mode bits")
    def test_manifest_accepts_and_restores_full_posix_file_mode(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings = claude / "settings.json"
        original = b'{"theme":"dark"}\n'
        settings.write_bytes(original)
        settings.chmod(0o1644)
        if stat.S_IMODE(settings.stat().st_mode) != 0o1644:
            self.skipTest("filesystem does not preserve sticky bit on regular files")

        installer, installed = self.apply_install()
        self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o1644)
        rollback_plan = installer.plan_rollback(installed.manifest)
        installer.rollback(installed.manifest, approval=rollback_plan.fingerprint)
        self.assertEqual(strict_json(settings), {"theme": "dark"})
        self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o1644)

    def test_rollback_is_key_and_policy_segment_selective(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings = claude / "settings.json"
        settings.write_text('{"theme":"dark"}\n', encoding="utf-8")
        settings.chmod(0o640)
        settings_mode = stat.S_IMODE(settings.stat().st_mode)
        policy = claude / "CLAUDE.md"
        policy.write_text("# User\n", encoding="utf-8")
        policy.chmod(0o644)
        policy_mode = stat.S_IMODE(policy.stat().st_mode)
        installer, installed = self.apply_install()
        document = strict_json(settings)
        document["later"] = 1
        settings.write_text(json.dumps(document) + "\n", encoding="utf-8")
        policy.write_text(policy.read_text(encoding="utf-8") + "later text\n", encoding="utf-8")

        plan = installer.plan_rollback(installed.manifest)
        settings_change = next(change for change in plan.changes if change.path.endswith("settings.json"))
        self.assertIsNotNone(settings_change.before_sha256)
        self.assertIsNotNone(settings_change.after_sha256)
        installer.rollback(installed.manifest, approval=plan.fingerprint)
        self.assertEqual(strict_json(settings), {"theme": "dark", "later": 1})
        self.assertEqual(stat.S_IMODE(settings.stat().st_mode), settings_mode)
        self.assertNotIn("pilotfish:begin", policy.read_text(encoding="utf-8"))
        self.assertIn("later text", policy.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(policy.stat().st_mode), policy_mode)

    def test_rollback_reapproval_required_after_any_post_plan_change(self) -> None:
        installer, installed = self.apply_install()
        settings = self.home / ".claude/settings.json"
        plan = installer.plan_rollback(installed.manifest)
        document = strict_json(settings)
        document["unrelated"] = True
        settings.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with self.assertRaises(ApprovalRequired):
            installer.rollback(installed.manifest, approval=plan.fingerprint)
        self.assertTrue(strict_json(settings)["unrelated"])

    def test_rollback_preserves_modified_owned_content_and_state(self) -> None:
        installer, installed = self.apply_install()
        executor = self.home / ".claude/agents/executor.md"
        executor.write_text(executor.read_text(encoding="utf-8") + "user edit\n", encoding="utf-8")
        plan = installer.plan_rollback(installed.manifest)
        self.assertTrue(any("executor.md" in warning for warning in plan.warnings))
        installer.rollback(installed.manifest, approval=plan.fingerprint)
        self.assertTrue(executor.exists())
        self.assertTrue((self.home / ".claude/pilotfish/state.json").exists())

    def test_manifest_tampering_and_backup_tampering_fail_closed(self) -> None:
        installer, installed = self.apply_install()
        manifest_path = self.home / installed.manifest
        document = strict_json(manifest_path)
        document["records"][0]["path"] = "sentinel.txt"
        unsigned = dict(document)
        unsigned.pop("integrity_sha256", None)
        encoded = (json.dumps(unsigned, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        document["integrity_sha256"] = hashlib.sha256(encoded).hexdigest()
        manifest_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(InstallerError, "outside installer ownership"):
            installer.plan_rollback(installed.manifest)
        with self.assertRaises(InstallerError):
            installer.plan_rollback("../../outside.json")

    def test_backup_tampering_fails_before_rollback_plan(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "settings.json").write_text('{"unowned":true}\n', encoding="utf-8")
        installer, installed = self.apply_install()
        manifest = strict_json(self.home / installed.manifest)
        record = next(item for item in manifest["records"] if item["kind"] == "settings")
        backup = self.home / record["backup"]
        backup.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(InstallerError, "backup hash mismatch"):
            installer.plan_rollback(installed.manifest)

    def test_partial_rollback_failure_restores_bytes_and_mode(self) -> None:
        installer, installed = self.apply_install()
        state = self.home / ".claude/pilotfish/state.json"
        before = state.read_bytes()
        before_mode = stat.S_IMODE(state.stat().st_mode)
        plan = installer.plan_rollback(installed.manifest)
        with mock.patch.object(
            installer, "_rollback_policy", side_effect=OSError("injected rollback failure")
        ):
            with self.assertRaisesRegex(InstallerError, "rollback changes were reverted"):
                installer.rollback(installed.manifest, approval=plan.fingerprint)
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), before_mode)

    def test_rollback_recovery_preserves_concurrent_third_state(self) -> None:
        installer, installed = self.apply_install()
        state = self.home / ".claude/pilotfish/state.json"
        concurrent_state = b"concurrent owner data\n"
        plan = installer.plan_rollback(installed.manifest)

        def fail_after_concurrent_state(*args, **kwargs) -> None:
            state.write_bytes(concurrent_state)
            raise OSError("injected rollback failure after concurrent state change")

        with mock.patch.object(
            installer, "_rollback_policy", side_effect=fail_after_concurrent_state
        ):
            with self.assertRaisesRegex(
                InstallerError,
                "recovery errors:.*concurrent change preserved during rollback recovery",
            ):
                installer.rollback(installed.manifest, approval=plan.fingerprint)

        self.assertEqual(state.read_bytes(), concurrent_state)

    def test_rollback_descriptor_cas_preserves_post_plan_owned_key_change(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings = claude / "settings.json"
        settings.write_text('{"theme":"dark"}\n', encoding="utf-8")
        installer, installed = self.apply_install()
        plan = installer.plan_rollback(installed.manifest)
        original = installer._rollback_settings
        injected = False

        def change_owned_key_after_outer_check(record, keys, **kwargs) -> None:
            nonlocal injected
            if not injected:
                document = strict_json(settings)
                document["model"] = "concurrent-user-model"
                settings.write_text(
                    json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                injected = True
            original(record, keys, **kwargs)

        with mock.patch.object(
            installer,
            "_rollback_settings",
            side_effect=change_owned_key_after_outer_check,
        ):
            with self.assertRaisesRegex(
                InstallerError, "descriptor precondition changed after planning"
            ):
                installer.rollback(installed.manifest, approval=plan.fingerprint)

        self.assertEqual(strict_json(settings)["model"], "concurrent-user-model")

    def test_partial_apply_failure_restores_tree_and_removes_failed_backups(self) -> None:
        installer = self.installer()
        plan = installer.plan_install()
        original = installer._write_or_delete

        def fail_on_scout(path: str, content: bytes | None, **kwargs) -> None:
            if path == ".claude/agents/scout.md":
                raise OSError("injected failure")
            original(path, content, **kwargs)

        with mock.patch.object(installer, "_write_or_delete", side_effect=fail_on_scout):
            with self.assertRaisesRegex(InstallerError, "applied writes were rolled back"):
                installer.install(approval=plan.fingerprint)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_post_commit_failure_registers_inflight_recovery(self) -> None:
        installer = self.installer()
        plan = installer.plan_install()
        original = installer._write_or_delete
        raised = False

        def write_then_raise(path: str, content: bytes | None, **kwargs) -> None:
            nonlocal raised
            original(path, content, **kwargs)
            if path == ".claude/settings.json" and not raised:
                raised = True
                raise OSError("injected post-commit failure")

        with mock.patch.object(
            installer, "_write_or_delete", side_effect=write_then_raise
        ):
            with self.assertRaisesRegex(InstallerError, "applied writes were rolled back"):
                installer.install(approval=plan.fingerprint)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_apply_recovery_preserves_concurrent_user_change_and_backup(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings = claude / "settings.json"
        original_settings = b'{"theme":"dark"}\n'
        concurrent_settings = b'{"theme":"dark","concurrent":true}\n'
        settings.write_bytes(original_settings)
        installer = self.installer()
        plan = installer.plan_install()
        original_write = installer._write_or_delete

        def fail_after_concurrent_change(
            path: str, content: bytes | None, **kwargs
        ) -> None:
            if path == ".claude/agents/scout.md":
                settings.write_bytes(concurrent_settings)
                raise OSError("injected failure after concurrent settings change")
            original_write(path, content, **kwargs)

        with mock.patch.object(
            installer,
            "_write_or_delete",
            side_effect=fail_after_concurrent_change,
        ):
            with self.assertRaisesRegex(
                InstallerError, "automatic rollback errors.*concurrent change preserved"
            ):
                installer.install(approval=plan.fingerprint)

        self.assertEqual(settings.read_bytes(), concurrent_settings)
        backups = list(
            (claude / "pilotfish" / "backups").glob("**/.claude/settings.json")
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original_settings)


if __name__ == "__main__":
    unittest.main()
