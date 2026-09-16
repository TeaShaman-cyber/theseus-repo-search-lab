import io
import subprocess
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from theseus_repo_search.errors import RepoSearchError
from scripts.producer_guard import checkout_exact, main, run_exact_command, verify_checked_out_commit


class ProducerGuardTests(unittest.TestCase):
    @patch("scripts.producer_guard.subprocess.run")
    def test_checkout_failure_is_blocked_source_binding(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["git"])
        with self.assertRaises(RepoSearchError) as caught:
            checkout_exact("https://github.com/example/repo.git", "a" * 40, Path("target"))
        self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_BINDING")

    @patch("scripts.producer_guard.subprocess.run")
    def test_readback_mismatch_is_blocked_source_mismatch(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["git"], 0, stdout=("b" * 40) + "\n", stderr=""
        )
        with self.assertRaises(RepoSearchError) as caught:
            verify_checked_out_commit(Path("target"), "a" * 40)
        self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")

    @patch("scripts.producer_guard.subprocess.run")
    def test_exact_command_failure_is_degraded_extraction_unavailable(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["lake", "build"])
        with self.assertRaises(RepoSearchError) as caught:
            run_exact_command(["lake", "build", "Zeta23"], Path("target"))
        self.assertEqual(caught.exception.code, "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE")

    @patch("scripts.producer_guard.verify_checked_out_commit")
    def test_cli_emits_one_machine_error_to_stderr(self, verify):
        verify.side_effect = RepoSearchError("BLOCKED_SOURCE_MISMATCH", "sha mismatch")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["verify", "--repo-dir", "target", "--expected", "a" * 40])
        self.assertNotEqual(code, 0)
        self.assertEqual(
            stderr.getvalue(),
            '{"code":"BLOCKED_SOURCE_MISMATCH","message":"sha mismatch","status":"BLOCKED"}\n',
        )
