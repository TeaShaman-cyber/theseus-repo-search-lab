from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "dependency-security.yml"
INPUTS = ROOT / "config" / "dependency-security.inputs"
PROFILE_SHA = "a6c1a24b642585a6d4dc1a87d2eef6f61aee965a"


class DependencySecurityConsumerContractTest(unittest.TestCase):
    def test_caller_pins_exact_reusable_profile_and_is_read_only(self):
        text = WORKFLOW.read_text()
        expected = (
            "uses: TeaShaman-cyber/marcopolo-cookbook/.github/workflows/"
            f"reusable-dependency-security.yml@{PROFILE_SHA}"
        )
        self.assertIn(expected, text)
        self.assertEqual(len(PROFILE_SHA), 40)
        self.assertNotIn("@main", text)
        self.assertIn("pull_request:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn("continue-on-error", text)

    def test_caller_owns_only_dependency_input_manifest(self):
        text = WORKFLOW.read_text()
        self.assertIn("inputs_file: config/dependency-security.inputs", text)
        self.assertNotIn("analysis_endpoint", text)
        rows = [
            line.strip()
            for line in INPUTS.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(rows, ["pip-hashed requirements/ci-heavy.txt"])

    def test_consumer_does_not_copy_shared_scanner_runtime(self):
        self.assertFalse((ROOT / "tools" / "ci" / "dependency-security").exists())
        self.assertFalse((ROOT / "requirements" / "ci-dependency-security.txt").exists())
        self.assertFalse((ROOT / ".github" / "actions" / "dependency-security").exists())


if __name__ == "__main__":
    unittest.main()
