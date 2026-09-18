import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERIC = ROOT / ".github/workflows/lean-source-producer-smoke.yml"
LEGACY = ROOT / ".github/workflows/zeta23-producer-smoke.yml"
LEGACY_CONFIG = ROOT / "producer/zeta23.json"
REPLAYS = (
    "scripts/replay_zeta23.py",
    "scripts/replay_long_gaps.py",
    "scripts/replay_prime_gaps_186.py",
)
REQUIRED = (
    "src/**", "tests/**", "scripts/producer_guard.py",
    "scripts/load_producer_env.py", *REPLAYS,
    "producer/sources/**", "producer/runner.json", "pyproject.toml",
    ".github/workflows/lean-source-producer-smoke.yml",
)


class WorkflowStructureTests(unittest.TestCase):
    def test_generic_workflow_contract(self):
        candidate = GENERIC if GENERIC.exists() else LEGACY
        self.assertTrue(candidate.is_file())
        text = candidate.read_text(encoding="utf-8")
        for required in REQUIRED:
            with self.subTest(required=required):
                self.assertIn(required, text)
        for replay in REPLAYS:
            with self.subTest(replay=replay):
                self.assertTrue((ROOT / replay).is_file())
                self.assertIn(replay, text)
        self.assertFalse(LEGACY.exists())
        self.assertFalse(LEGACY_CONFIG.exists())
        self.assertNotIn("producer/zeta23.json", text)
        upload = text.split("- name: Upload normalized repository lens artifact", 1)[1]
        self.assertNotIn("${SOURCE_ID}", upload)
        self.assertIn("_out/*-artifact/", upload)
        self.assertIn("_out/*-replay.json", upload)

    def test_generic_workflow_splits_cache_and_build_timing(self):
        text = GENERIC.read_text(encoding="utf-8")
        self.assertIn("- name: Restore source dependency cache", text)
        self.assertIn("- name: Build source target", text)
        self.assertNotIn("- name: Restore source cache and build", text)
        self.assertIn("cache_get_seconds=", text)
        self.assertIn("source_build_seconds=", text)
        self.assertIn("mathlib_cache_files=", text)
        self.assertIn("source_build_kib=", text)
