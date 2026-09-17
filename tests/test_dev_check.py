from pathlib import Path
import os
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


if __name__ == "__main__":
    unittest.main()
