from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROLES = (
    "scout",
    "Explore",
    "mech-executor",
    "executor",
    "senior-executor",
    "verifier",
    "security-executor",
)


class Phase2DocumentationTests(unittest.TestCase):
    def test_readmes_publish_the_canonical_seven_role_inventory(self) -> None:
        expected_bindings = {
            "scout": ("haiku", "low"),
            "Explore": ("haiku", "low"),
            "mech-executor": ("sonnet", "low"),
            "executor": ("sonnet", "high"),
            "senior-executor": ("opus", "high"),
            "verifier": ("opus", "medium"),
            "security-executor": ("opus", "high"),
        }
        for filename in ("README.md", "README.zh-TW.md"):
            content = (ROOT / filename).read_text(encoding="utf-8")
            rows = {
                name: (model, effort)
                for name, model, effort in re.findall(
                    r"^\| `([^`]+)` \| (haiku|sonnet|opus) \| "
                    r"(low|medium|high) \|",
                    content,
                    flags=re.MULTILINE,
                )
                if name in ROLES
            }
            self.assertEqual(rows, expected_bindings, filename)
            self.assertIn("2.1.207", content)
            self.assertIn(".claude/pilotfish/", content)
            self.assertIn("fingerprint", content)

    def test_install_documentation_is_local_only(self) -> None:
        documents = [
            (ROOT / path).read_text(encoding="utf-8")
            for path in (
                "README.md",
                "README.zh-TW.md",
                "install/AGENT-INSTALL.md",
            )
        ]
        for content in documents:
            self.assertNotIn("raw.githubusercontent.com", content)
            self.assertNotIn("Read https://", content)
            self.assertIn("local", content.casefold())

        for content in documents[:2]:
            self.assertIn("codex/phase-2-claude-adapter", content)
            self.assertIn("https://github.com/trionnemesis/pilotfish.git", content)
            self.assertNotIn("git clone --branch v1.1.5", content)
            self.assertIn("cross-user", content)

        runbook = documents[-1]
        for operation in ("install", "update", "rollback", "uninstall"):
            self.assertIn(f"python3 -m install.installer {operation}", runbook)
        self.assertIn("--dry-run", runbook)
        self.assertIn("--approve", runbook)
        self.assertIn("never disables WebFetch", runbook)
        self.assertIn("cross-user installs are intentionally unsupported", runbook)

    def test_historical_design_points_to_the_canonical_contract(self) -> None:
        content = (ROOT / "docs" / "design.md").read_text(encoding="utf-8")
        self.assertIn("Historical baseline", content)
        self.assertIn("[SPEC.md](../SPEC.md)", content)
        self.assertIn("[routing.yaml](../routing.yaml)", content)
        self.assertIn("canonical contract wins", content)


if __name__ == "__main__":
    unittest.main()
