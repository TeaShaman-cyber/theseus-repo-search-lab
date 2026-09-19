import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERIC = ROOT / ".github/workflows/lean-source-producer-smoke.yml"
LEGACY = ROOT / ".github/workflows/zeta23-producer-smoke.yml"
LEGACY_CONFIG = ROOT / "producer/zeta23.json"
ALL_REPLAYS = (
    "scripts/replay_zeta23.py",
    "scripts/replay_long_gaps.py",
    "scripts/replay_prime_gaps_186.py",
    "scripts/replay_flt_regular.py",
    "scripts/replay_cdc_lean.py",
    "scripts/replay_con_nf.py",
    "scripts/replay_ten_proofs_multicolor.py",
)
ACTIVE_MATRIX_REPLAYS = (
    "scripts/replay_zeta23.py",
    "scripts/replay_long_gaps.py",
    "scripts/replay_flt_regular.py",
    "scripts/replay_cdc_lean.py",
    "scripts/replay_con_nf.py",
    "scripts/replay_ten_proofs_multicolor.py",
)
REQUIRED = (
    "scripts/producer_guard.py",
    "scripts/load_producer_env.py", *ACTIVE_MATRIX_REPLAYS,
    "producer/sources/", "producer/runner.json",
)


class WorkflowStructureTests(unittest.TestCase):
    def test_heavy_workflow_is_explicit_acceptance_dispatch_only(self):
        text = GENERIC.read_text(encoding="utf-8")
        trigger = text.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("pull_request:", trigger)
        self.assertNotIn("push:", trigger)

    def test_generic_workflow_contract(self):
        candidate = GENERIC if GENERIC.exists() else LEGACY
        self.assertTrue(candidate.is_file())
        text = candidate.read_text(encoding="utf-8")
        for required in REQUIRED:
            with self.subTest(required=required):
                self.assertIn(required, text)
        for replay in ALL_REPLAYS:
            with self.subTest(replay=replay):
                self.assertTrue((ROOT / replay).is_file())
        for replay in ACTIVE_MATRIX_REPLAYS:
            with self.subTest(active_replay=replay):
                self.assertIn(replay, text)
        self.assertFalse(LEGACY.exists())
        self.assertFalse(LEGACY_CONFIG.exists())
        self.assertNotIn("producer/zeta23.json", text)
        upload = text.split("- name: Upload normalized repository lens artifact", 1)[1].split("  consume-artifact:\n", 1)[0]
        self.assertNotIn("${SOURCE_ID}", upload)
        self.assertIn("_out/*-artifact/", upload)
        self.assertIn("_out/*-replay.json", upload)
        self.assertNotIn("_out/raw-depgraph-receipt.json", upload)
        self.assertIn("authority-receipt.json", text)

    def test_source_exclusions_are_wired_only_into_artifact_build(self):
        text = GENERIC.read_text(encoding="utf-8")
        extract = text.split("- name: Extract exact declaration graph", 1)[1].split("- name: Install repository lens package", 1)[0]
        build = text.split("- name: Build normalized artifact", 1)[1].split("- name: Verify project and replay selected source", 1)[0]
        self.assertNotIn("exclude_args", extract)
        self.assertIn("exclude_args=()", build)
        self.assertIn("--exclude-source-prefix", build)
        self.assertIn('"${exclude_args[@]}"', build)

    def test_prime_gaps_is_not_in_default_generic_matrix(self):
        text = GENERIC.read_text(encoding="utf-8")
        matrix = text.split("matrix:", 1)[1].split("steps:", 1)[0]
        self.assertNotIn("producer/sources/openai-prime-gaps-186.json", matrix)

    def test_generic_workflow_splits_cache_and_build_timing(self):
        text = GENERIC.read_text(encoding="utf-8")
        self.assertIn("- name: Restore source dependency cache", text)
        self.assertIn("- name: Build source target", text)
        self.assertNotIn("- name: Restore source cache and build", text)
        self.assertIn("cache_get_seconds=", text)
        self.assertIn("source_build_seconds=", text)
        self.assertIn("mathlib_cache_files=", text)
        self.assertIn("source_build_kib=", text)

    def test_source_build_emits_bounded_live_heartbeat(self):
        text = GENERIC.read_text(encoding="utf-8")
        build = text.split("- name: Build source target", 1)[1].split("- name: Extract exact declaration graph", 1)[0]
        self.assertIn("sleep 60", build)
        self.assertIn("build_heartbeat ts=", build)
        self.assertIn("elapsed_seconds=", build)
        self.assertIn("mem_available_kib=", build)
        self.assertIn("root_free_kib=", build)
        self.assertIn("source_build_kib=", build)
        self.assertIn("tick % 5", build)
        self.assertIn("trap", build)
        self.assertNotIn("GITHUB_TOKEN", build)
        self.assertNotIn("gh api", build)
    def test_fresh_consumer_job_matches_producer_matrix_and_is_source_free(self):
        text = GENERIC.read_text(encoding="utf-8")
        self.assertIn("  consume-artifact:\n", text)
        producer = text.split("  produce-and-replay:\n", 1)[1].split("  consume-artifact:\n", 1)[0]
        consumer = text.split("  consume-artifact:\n", 1)[1]

        def entries(block: str):
            return re.findall(
                r"- source_descriptor:\s*(\S+)\n\s*replay_script:\s*(\S+)\n\s*artifact_name:\s*(\S+)",
                block,
            )

        self.assertEqual(entries(producer), entries(consumer))
        self.assertIn("needs: produce-and-replay", consumer)
        self.assertIn("actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", consumer)
        self.assertIn("scripts/write_consumer_receipt.py", consumer)
        self.assertIn("PRAGMA quick_check", consumer)

        forbidden = (
            "Checkout pinned source",
            "Restore source dependency cache",
            "Build source target",
            "LeanDepViz",
            "elan",
            "lake ",
            "_target/source",
            "--source-root",
            "gh codespace",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, consumer)

    def test_consumer_receipt_is_uploaded_separately(self):
        text = GENERIC.read_text(encoding="utf-8")
        consumer = text.split("  consume-artifact:\n", 1)[1]
        self.assertIn("-consumer-receipt", consumer)
        self.assertIn("_consumer/*-consumer-receipt.json", consumer)
        self.assertIn("if-no-files-found: error", consumer)
