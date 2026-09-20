import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "property-test.yml"
ENDPOINT = ROOT / "tools" / "ci" / "property-test"
LOCK = ROOT / "requirements" / "ci-property.txt"
PROPERTY_TEST = ROOT / "tests" / "property" / "test_normalize_properties.py"
PROFILE_SHA = "d18f37208c73df2c5b17d6286caa0fa341db4f6e"


class PropertyCiContractTest(unittest.TestCase):
    def test_caller_pins_exact_profile_and_is_read_only(self):
        text = WORKFLOW.read_text()
        expected = (
            "uses: TeaShaman-cyber/marcopolo-cookbook/.github/workflows/"
            f"reusable-property-test.yml@{PROFILE_SHA}"
        )
        self.assertIn(expected, text)
        self.assertEqual(len(PROFILE_SHA), 40)
        self.assertNotIn("@main", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn("continue-on-error", text)

    def test_lock_is_exact_and_hash_pinned(self):
        text = LOCK.read_text()
        self.assertIn("hypothesis==6.168.0", text)
        self.assertIn("sortedcontainers==2.4.0", text)
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", text)
        self.assertEqual(len(hashes), 2)
        self.assertEqual(len(set(hashes)), 2)

    def test_endpoint_is_valid_posix_shell_and_scoped_to_property_suite(self):
        result = subprocess.run(
            ["sh", "-n", str(ENDPOINT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = ENDPOINT.read_text()
        self.assertIn("tests/property", text)
        self.assertIn("PROPERTY_TEST_PASS", text)
        self.assertIn("HYPOTHESIS_STORAGE_DIRECTORY", text)

    def test_property_settings_are_bounded_and_database_free(self):
        text = PROPERTY_TEST.read_text()
        self.assertIn("max_examples=200", text)
        self.assertIn("deadline=None", text)
        self.assertIn("derandomize=True", text)
        self.assertIn("database=None", text)
        self.assertEqual(text.count("@PROPERTY_SETTINGS"), 3)


if __name__ == "__main__":
    unittest.main()
