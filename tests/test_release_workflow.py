"""
Tests for GitHub Actions release workflow policy.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


class TestReleaseWorkflow(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_release_is_tag_or_manual_triggered(self):
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertRegex(self.workflow, r"push:\s*\n\s+tags:\s*\n\s+- \"v\*\.\*\.\*\"")
        push_block = re.search(
            r"on:\s*\n(?P<body>.*?)(?=\npermissions:)",
            self.workflow,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(push_block)
        self.assertNotIn("branches:", push_block.group("body"))

    def test_release_can_write_github_release_assets(self):
        self.assertRegex(self.workflow, r"permissions:\s*\n\s+contents: write")

    def test_release_job_runs_publish_script(self):
        self.assertIn("tools/build/build.py", self.workflow)
        self.assertIn("--publish-release", self.workflow)
        self.assertIn("dotnet tool install -g vpk", self.workflow)


if __name__ == "__main__":
    unittest.main()
