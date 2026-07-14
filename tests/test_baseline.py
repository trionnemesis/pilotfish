from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "baseline" / "manifest.json"


class BaselineManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_manifest_uses_immutable_baseline(self) -> None:
        self.assertEqual(
            self.manifest["baseline_commit"],
            "e5b45dd2330b1ba781d9da0f80211dd657d854cf",
        )
        self.assertEqual(self.manifest["baseline_tag"], "v1.1.5")
        self.assertEqual(self.manifest["version"], "1.1.5")
        self.assertEqual(self.manifest["commit_count"], 27)

    def test_baseline_file_hashes_match_git_objects(self) -> None:
        baseline = self.manifest["baseline_commit"]
        for path, expected in self.manifest["sha256"].items():
            with self.subTest(path=path):
                content = subprocess.run(
                    ["git", "show", f"{baseline}:{path}"],
                    cwd=ROOT,
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected)

    def test_spec_hash_matches_committed_source(self) -> None:
        content = (ROOT / "SPEC.md").read_bytes()
        self.assertEqual(
            hashlib.sha256(content).hexdigest(), self.manifest["spec_sha256"]
        )

    def test_baseline_role_inventory_is_explicit(self) -> None:
        self.assertEqual(
            self.manifest["role_names"],
            [
                "Explore",
                "executor",
                "mech-executor",
                "scout",
                "security-executor",
                "verifier",
            ],
        )


if __name__ == "__main__":
    unittest.main()
