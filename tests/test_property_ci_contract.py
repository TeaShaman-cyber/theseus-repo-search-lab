import re
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

    def test_endpoint_is_scoped_to_property_suite(self):
        text = ENDPOINT.read_text()
        self.assertIn("tests/property", text)
        self.assertIn("PROPERTY_TEST_PASS", text)
        self.assertIn("HYPOTHESIS_STORAGE_DIRECTORY", text)

    def test_native_lexical_oracle_is_pinned_and_scope_guarded(self):
        text = WORKFLOW.read_text()
        self.assertIn("lean-native-lexical:", text)
        self.assertIn("lean +leanprover/lean4:v4.34.1", text)
        self.assertIn("df0b2b3a439961ffcbb3985214365ffe40f49bc871df04dff268c7d8e21ca8b2", text)
        self.assertIn("git diff --name-only", text)
        self.assertIn("src/theseus_repo_search", text)
        self.assertIn("python3 scripts/check_lean_lexical_oracle.py", text)
        self.assertIn("tests/fixtures/lean_lexical_oracle.lean", text)
        self.assertNotIn("lake build", text)

    def test_property_settings_are_bounded_and_database_free(self):
        text = PROPERTY_TEST.read_text()
        self.assertIn("max_examples=200", text)
        self.assertIn("deadline=None", text)
        self.assertIn("derandomize=True", text)
        self.assertIn("database=None", text)
        self.assertEqual(text.count("@PROPERTY_SETTINGS"), 3)


if __name__ == "__main__":
    unittest.main()
