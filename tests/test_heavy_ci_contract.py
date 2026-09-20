import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "heavy-python.yml"
ENDPOINT = ROOT / "tools" / "ci" / "heavy-python"
LOCK = ROOT / "requirements" / "ci-heavy.txt"
MYPY = ROOT / "config" / "mypy-heavy.ini"
COOKBOOK_SHA = "5c6a60df781adc0c6426c8cd25dcde857ee2435d"


class HeavyCiContractTest(unittest.TestCase):
    def test_workflow_is_separate_read_only_advisory_surface(self):
        text = WORKFLOW.read_text()
        self.assertIn("pull_request:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("continue-on-error", text)
        self.assertNotIn("push:", text)

    def test_workflow_pins_promoted_reusable_profile(self):
        text = WORKFLOW.read_text()
        expected = (
            "TeaShaman-cyber/marcopolo-cookbook/.github/workflows/"
            f"reusable-heavy-python.yml@{COOKBOOK_SHA}"
        )
        self.assertIn(f"uses: {expected}", text)
        self.assertNotIn("@main", text)
        self.assertEqual(len(COOKBOOK_SHA), 40)

    def test_caller_owns_lock_and_analysis_endpoint(self):
        text = WORKFLOW.read_text()
        self.assertIn("requirements_file: requirements/ci-heavy.txt", text)
        self.assertIn("analysis_endpoint: tools/ci/heavy-python", text)
        self.assertIn('python_version: "3.11"', text)

    def test_lock_is_exact_and_hash_pinned(self):
        text = LOCK.read_text()
        expected = {
            "ruff": "0.16.8",
            "mypy": "2.3.1",
            "mypy_extensions": "1.1.0",
            "typing_extensions": "4.16.0",
            "pathspec": "1.1.1",
            "ast_serialize": "0.11.2",
            "librt": "0.15.0",
        }
        for package, version in expected.items():
            self.assertIn(f"{package}=={version} \\", text)
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", text)
        self.assertEqual(len(hashes), len(expected))
        self.assertEqual(len(set(hashes)), len(expected))

    def test_endpoint_is_valid_posix_shell(self):
        result = subprocess.run(
            ["sh", "-n", str(ENDPOINT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_endpoint_avoids_parent_config_bleed_and_runs_both_analyzers(self):
        text = ENDPOINT.read_text()
        self.assertIn("ruff check --isolated --target-version py311 src scripts tests", text)
        self.assertIn("MYPYPATH=src mypy --config-file config/mypy-heavy.ini src scripts", text)
        self.assertIn("HEAVY_PYTHON_RESULT", text)
        self.assertIn("ruff_code", text)
        self.assertIn("mypy_code", text)

    def test_mypy_policy_is_repository_owned(self):
        text = MYPY.read_text()
        for marker in (
            "python_version = 3.11",
            "check_untyped_defs = True",
            "ignore_missing_imports = True",
            "show_error_codes = True",
        ):
            self.assertIn(marker, text)

    def test_generated_local_projection_patterns_are_ignored(self):
        text = (ROOT / ".gitignore").read_text()
        self.assertIn(".consumer-*/", text)
        self.assertIn(".verify-run-*/", text)


if __name__ == "__main__":
    unittest.main()
