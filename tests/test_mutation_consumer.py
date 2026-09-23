from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "scripts" / "write_mutation_receipt.py"
LOCK = ROOT / "requirements" / "ci-mutation.txt"
LOCK_INPUT = ROOT / "requirements" / "ci-mutation.in"
WORKFLOW = ROOT / ".github" / "workflows" / "mutation-test.yml"
ENDPOINT = ROOT / "tools" / "ci" / "mutation-test"
PYPROJECT = ROOT / "pyproject.toml"

SPEC = importlib.util.spec_from_file_location("write_mutation_receipt", WRITER)
assert SPEC and SPEC.loader
receipt_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = receipt_module
SPEC.loader.exec_module(receipt_module)


def stats(**overrides):
    value = {
        "killed": 211,
        "survived": 0,
        "total": 211,
        "no_tests": 0,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 0,
        "check_was_interrupted_by_user": 0,
        "segfault": 0,
    }
    value.update(overrides)
    return value


class MutationConsumerTests(unittest.TestCase):
    def test_classification_states(self):
        cases = (
            (stats(), "MUTATION_CLEAN"),
            (stats(killed=193, survived=18), "SURVIVORS_PRESENT"),
            (stats(killed=210, timeout=1), "MUTATION_INCOMPLETE"),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                state, counts, reason = receipt_module.classify(payload)
                self.assertEqual(state, expected)
                self.assertEqual(counts["total"], 211)
                self.assertIsNone(reason)

    def test_invalid_or_unaccounted_stats_are_runtime_failed(self):
        state, _counts, reason = receipt_module.classify(stats(total=212))
        self.assertEqual(state, "MUTATION_RUNTIME_FAILED")
        self.assertEqual(reason, "stats_total_mismatch")
        bad = stats()
        bad["survived"] = True
        state, counts, reason = receipt_module.classify(bad)
        self.assertEqual(state, "MUTATION_RUNTIME_FAILED")
        self.assertEqual(counts, {})
        self.assertEqual(reason, "invalid_stats_field:survived")

    def test_receipt_cli_survivors_is_advisory_red(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stats_path = root / "stats.json"
            receipt = root / "receipt.json"
            stats_path.write_text(json.dumps(stats(killed=193, survived=18)), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(WRITER),
                    "--stats",
                    str(stats_path),
                    "--receipt",
                    str(receipt),
                    "--lock-file",
                    str(LOCK),
                    "--source-commit",
                    "a" * 40,
                    "--mutmut-version",
                    "3.8.0",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "SURVIVORS_PRESENT")
            self.assertEqual(payload["counts"]["killed"], 193)
            self.assertEqual(payload["counts"]["survived"], 18)
            self.assertEqual(payload["counts"]["total"], 211)
            self.assertEqual(len(payload["lock_sha256"]), 64)

    def test_runtime_failure_receipt_is_distinct(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = Path(td) / "receipt.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(WRITER),
                    "--receipt",
                    str(receipt),
                    "--lock-file",
                    str(LOCK),
                    "--source-commit",
                    "b" * 40,
                    "--mutmut-version",
                    "3.8.0",
                    "--runtime-failure",
                    "mutmut_run_exit:2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 2)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "MUTATION_RUNTIME_FAILED")
            self.assertEqual(payload["reason"], "mutmut_run_exit:2")

    def test_workflow_is_immutable_shared_consumer(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        trigger = text.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("pull_request:", trigger)
        self.assertNotIn("push:", trigger)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertRegex(
            text,
            r"reusable-mutation-test\.yml@[0-9a-f]{40}",
        )
        self.assertIn("requirements/ci-mutation.txt", text)
        self.assertIn("tools/ci/mutation-test", text)
        self.assertNotIn("continue-on-error", text)

    def test_mutmut_config_selects_only_deterministic_normalizer_tests(self):
        text = PYPROJECT.read_text(encoding="utf-8")
        self.assertIn("[tool.mutmut]", text)
        self.assertIn('only_mutate = ["src/theseus_repo_search/normalize.py"]', text)
        self.assertIn('pytest_add_cli_args_test_selection = ["tests/test_normalize.py"]', text)
        self.assertNotIn("tests/property", text.split("[tool.mutmut]", 1)[1])
        self.assertNotIn("hypothesis", text.split("[tool.mutmut]", 1)[1].lower())

    def test_lock_and_endpoint_contract(self):
        lock = LOCK.read_text(encoding="utf-8")
        endpoint = ENDPOINT.read_text(encoding="utf-8")
        self.assertEqual(LOCK_INPUT.read_text(encoding="utf-8"), "mutmut==3.8.0\n")
        self.assertIn("mutmut==3.8.0", lock)
        self.assertIn("--hash=sha256:", lock)
        self.assertIn("requirements/ci-mutation.in", lock)
        self.assertIn("mutmut export-cicd-stats", endpoint)
        self.assertIn("write_mutation_receipt.py", endpoint)
        self.assertIn("MUTATION_TEST_RECEIPT", endpoint)
        self.assertIn("git status --porcelain --untracked-files=all", endpoint)
        self.assertIn("worktree_dirty", endpoint)
        self.assertNotIn("hypothesis", endpoint.lower())


if __name__ == "__main__":
    unittest.main()
