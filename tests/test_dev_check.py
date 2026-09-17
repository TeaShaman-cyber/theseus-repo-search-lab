from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools" / "dev" / "check"


class DevCheckContractTest(unittest.TestCase):
    def test_canonical_dev_check_exists_and_composes_repo_checks(self):
        self.assertTrue(CHECK.is_file(), "missing canonical tools/dev/check")
        self.assertTrue(os.access(CHECK, os.X_OK), "tools/dev/check is not executable")
        text = CHECK.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        for marker in (
            "PYTHONPYCACHEPREFIX",
            "python3 -m unittest discover",
            "python3 -m compileall",
            "python3 -m json.tool",
            "git diff --check",
            "DEV_CHECK_PASS",
        ):
            self.assertIn(marker, text)


    def _run_copied_check_with_plan(self, plan_text: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            for rel in ("src", "scripts", "tests", "docs/superpowers/plans", "tools/dev"):
                (root / rel).mkdir(parents=True, exist_ok=True)
            copied = root / "tools/dev/check"
            shutil.copy2(CHECK, copied)
            (root / "docs/superpowers/plans/example.md").write_text(plan_text, encoding="utf-8")
            return subprocess.run(
                [str(copied)],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def test_dev_check_rejects_module_level_unittest_method_snippet(self):
        result = self._run_copied_check_with_plan(
            """# Bad plan\n\n```python\ndef test_missing_case(self):\n    self.assertEqual(1, 1)\n```\n"""
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("undiscoverable", result.stderr)

    def test_dev_check_accepts_class_scoped_unittest_method_snippet(self):
        result = self._run_copied_check_with_plan(
            """# Good plan\n\n```python\nclass ExampleTests(unittest.TestCase):\n    def test_missing_case(self):\n        self.assertEqual(1, 1)\n```\n"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
