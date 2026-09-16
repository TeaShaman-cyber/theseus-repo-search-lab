import io
import json
import tempfile
from hashlib import sha256
import subprocess
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from theseus_repo_search.errors import RepoSearchError
from scripts import producer_guard
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

    def test_bound_extraction_replaces_stale_graph_and_writes_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw.json"
            receipt = root / "receipt.json"
            raw.write_text("stale", encoding="utf-8")
            fresh = b'{"nodes":[],"edges":[]}\n'

            def fake_run(argv, cwd):
                self.assertFalse(raw.exists())
                raw.write_bytes(fresh)

            with patch.object(producer_guard, "verify_checked_out_commit", return_value="a" * 40) as verify,                  patch.object(producer_guard, "run_exact_command", side_effect=fake_run):
                producer_guard.run_bound_extraction(
                    ["lake", "env", "lean"],
                    cwd=root,
                    repo_dir=root,
                    expected_commit="a" * 40,
                    raw_depgraph=raw,
                    receipt_path=receipt,
                    source_repo="example/repo",
                    source_subdir="zeta23",
                    root_modules=("Zeta23",),
                    producer_kind="lean-dep-viz",
                    producer_tool_repo="cameronfreer/LeanDepViz",
                    producer_tool_commit="b" * 40,
                    producer_tool_hash="c" * 64,
                )
            self.assertEqual(verify.call_count, 2)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"]["commit"], "a" * 40)
            self.assertEqual(payload["raw_depgraph"]["sha256"], sha256(fresh).hexdigest())

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
