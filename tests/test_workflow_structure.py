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
    "scripts/replay_decreasing_diagrams.py",
)
ACTIVE_MATRIX_REPLAYS = (
    "scripts/replay_zeta23.py",
    "scripts/replay_long_gaps.py",
    "scripts/replay_flt_regular.py",
    "scripts/replay_cdc_lean.py",
    "scripts/replay_con_nf.py",
    "scripts/replay_ten_proofs_multicolor.py",
    "scripts/replay_decreasing_diagrams.py",
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

    def test_source_exclusions_are_descriptor_driven_after_generic_dispatch(self):
        text = GENERIC.read_text(encoding="utf-8")
        extract = text.split("- name: Extract exact declaration graph", 1)[1].split(
            "- name: Install repository lens package", 1
        )[0]
        build = text.split("- name: Build normalized artifact", 1)[1].split(
            "- name: Verify project and replay selected source", 1
        )[0]
        self.assertNotIn("exclude_args", extract)
        self.assertNotIn("exclude_args", build)
        self.assertNotIn("--exclude-source-prefix", build)
        self.assertIn("build-source-artifact", build)
        self.assertIn('"${{ matrix.source_descriptor }}"', build)

    def test_archive_row_and_source_kind_acquisition_dispatch_are_explicit(self):
        text = GENERIC.read_text(encoding="utf-8")
        producer = text.split("  produce-and-replay:\n", 1)[1].split("  consume-artifact:\n", 1)[0]
        consumer = text.split("  consume-artifact:\n", 1)[1]

        archive_row = (
            "producer/sources/decreasing-diagrams-complete.json",
            "scripts/replay_decreasing_diagrams.py",
            "decreasing-diagrams-complete-repo-index-v2",
        )
        pattern = re.compile(
            r"- source_descriptor:\s*(\S+)\n\s*replay_script:\s*(\S+)\n\s*artifact_name:\s*(\S+)"
        )
        producer_rows = pattern.findall(producer)
        consumer_rows = pattern.findall(consumer)
        self.assertIn(archive_row, producer_rows)
        self.assertIn(archive_row, consumer_rows)
        self.assertEqual(producer.count("source_kind: archive"), 1)
        self.assertEqual(consumer.count("source_kind: archive"), 1)

        expected_git_rows = [
            ("producer/sources/zeta23.json", "scripts/replay_zeta23.py", "zeta23-repo-index-v1"),
            ("producer/sources/openai-long-gaps.json", "scripts/replay_long_gaps.py", "openai-long-gaps-repo-index-v1"),
            ("producer/sources/leanprover-community-flt-regular.json", "scripts/replay_flt_regular.py", "leanprover-community-flt-regular-repo-index-v1"),
            ("producer/sources/openai-cdc-lean.json", "scripts/replay_cdc_lean.py", "openai-cdc-lean-repo-index-v1"),
            ("producer/sources/leanprover-community-con-nf.json", "scripts/replay_con_nf.py", "leanprover-community-con-nf-repo-index-v1"),
            ("producer/sources/openai-ten-proofs-multicolor.json", "scripts/replay_ten_proofs_multicolor.py", "openai-ten-proofs-multicolor-repo-index-v1"),
        ]
        self.assertEqual(producer_rows[:6], expected_git_rows)
        self.assertEqual(consumer_rows[:6], expected_git_rows)

        checkout = producer.split("- name: Checkout pinned source", 1)[1].split(
            "- name: Materialize pinned archive source", 1
        )[0]
        materialize = producer.split("- name: Materialize pinned archive source", 1)[1].split(
            "- name: Verify exact source readback", 1
        )[0]
        git_readback = producer.split("- name: Verify exact source readback", 1)[1].split(
            "- name: Verify archive materialization readback", 1
        )[0]
        archive_readback = producer.split(
            "- name: Verify archive materialization readback", 1
        )[1].split("- name: Resolve canonical source root", 1)[0]
        self.assertIn("if: matrix.source_kind != 'archive'", checkout)
        self.assertIn("if: matrix.source_kind == 'archive'", materialize)
        self.assertIn("scripts/materialize_archive_source.py", materialize)
        self.assertIn('"${{ matrix.source_descriptor }}"', materialize)
        self.assertIn("if: matrix.source_kind != 'archive'", git_readback)
        self.assertIn("if: matrix.source_kind == 'archive'", archive_readback)
        self.assertIn("verify_materialized_archive_members", archive_readback)

        shared = producer.split("- name: Resolve canonical source root", 1)[1]
        self.assertNotIn("if: matrix.source_kind", shared)
        self.assertNotIn("if: env.SOURCE_KIND", shared)
        self.assertNotIn('case "$SOURCE_KIND"', shared)
        self.assertNotIn("SOURCE_COMMIT", shared)
        self.assertNotIn("SOURCE_REPO", shared)
        self.assertIn("extract-source", shared)
        self.assertIn("build-source-artifact", shared)
        self.assertNotRegex(materialize, r"SOURCE_COMMIT|--commit|--expected")
        self.assertNotRegex(archive_readback, r"SOURCE_COMMIT|--commit|--expected")
        self.assertNotIn("ARCHIVE_SHA256", checkout)
        self.assertNotIn("ARCHIVE_SHA256", git_readback)

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

    def test_exact_lock_mathlib_archive_cache_is_bounded_and_not_proof_evidence(self):
        text = GENERIC.read_text(encoding="utf-8")
        producer = text.split("  produce-and-replay:\n", 1)[1].split("  consume-artifact:\n", 1)[0]
        key = producer.index("- name: Compute pinned Mathlib archive cache key")
        restore = producer.index("- name: Restore pinned Mathlib download archives")
        get = producer.index("- name: Restore source dependency cache")
        measure = producer.index("- name: Bound Mathlib cache persistence")
        save = producer.index("- name: Save pinned Mathlib download archives")
        build = producer.index("- name: Build source target")
        self.assertLess(key, restore)
        self.assertLess(restore, get)
        self.assertLess(get, measure)
        self.assertLess(measure, save)
        self.assertLess(save, build)
        self.assertIn('"$SOURCE_ROOT/lean-toolchain"', producer)
        self.assertIn('"$SOURCE_ROOT/lake-manifest.json"', producer)
        self.assertIn("actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830", producer)
        self.assertIn("actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830", producer)
        self.assertIn("~/.cache/mathlib", producer)
        self.assertIn("750000", producer)
        self.assertIn("cache-hit != 'true'", producer)
        self.assertNotIn(".lake/packages\n", producer[restore:save])
        self.assertNotIn(".lake/build\n", producer[restore:save])
        self.assertIn("lake exe cache get", producer)
        self.assertIn("  consume-artifact:\n", text)
        self.assertIn("  research-smoke:\n", text)

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
        self.assertIn('--source-descriptor "${{ matrix.source_descriptor }}"', consumer)
        self.assertIn('--source-descriptor-path "${{ matrix.source_descriptor }}"', consumer)
        self.assertIn('--runner-config producer/runner.json', consumer)
        self.assertIn('--runner-config-path producer/runner.json', consumer)
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

    def test_research_smoke_job_is_fresh_source_free_and_bounded(self):
        text = GENERIC.read_text(encoding="utf-8")
        self.assertIn("  research-smoke:\n", text)
        smoke = text.split("  research-smoke:\n", 1)[1]
        self.assertIn("needs: consume-artifact", smoke)
        self.assertIn("qa/research-smoke/zeta23-riemann-panel-v0.json", smoke)
        self.assertIn("qa/research-smoke/flt-bridge-v0.json", smoke)
        self.assertIn("scripts/run_research_smoke.py", smoke)
        self.assertIn("-research-smoke-receipt", smoke)
        self.assertIn("Verify exact research smoke checkout", smoke)
        self.assertIn("git rev-parse HEAD", smoke)
        self.assertIn('expected="${{ github.sha }}"', smoke)
        self.assertIn("RESEARCH_SMOKE_TOOL_COMMIT", smoke)
        self.assertIn('\"$RESEARCH_SMOKE_TOOL_COMMIT\"', smoke)
        self.assertIn("source_id: zeta23", smoke)
        self.assertIn("source_id: leanprover-community-flt-regular", smoke)
        for token in (
            "Checkout pinned source",
            "elan",
            "lake ",
            "--source-root",
            "gh codespace",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, smoke)

class ReleaseConsumerCanaryWorkflowTests(unittest.TestCase):
    def test_release_consumer_canary_is_source_free_and_reruns_fresh_consumers(self):
        path = ROOT / ".github" / "workflows" / "accepted-artifact-consumer-canary.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn("accepted-artifact/flt-regular/41bfa1d236ee59a8", text)
        self.assertIn("gh release verify", text)
        self.assertIn("gh release verify-asset", text)
        self.assertIn('git/ref/tags/$RELEASE_TAG', text)
        self.assertIn('.acceptance.repository_head', text)
        self.assertIn("scripts/consume_accepted_release.py", text)
        self.assertIn("python3 -m theseus_repo_search build-index", text)
        self.assertIn("scripts/replay_flt_regular.py", text)
        self.assertIn("scripts/run_research_smoke.py", text)
        for forbidden in (
            "Checkout pinned source",
            "Build source target",
            "LeanDepViz",
            "elan",
            "lake ",
            "_target/source",
            "producer_guard.py checkout",
        ):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, text)

class ReleaseConsumerNegativeCanaryWorkflowTests(unittest.TestCase):
    def test_negative_canary_requires_rebuild_and_fails_closed(self):
        path = ROOT / ".github" / "workflows" / "accepted-artifact-consumer-negative-canary.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn("REBUILD_REQUIRED", text)
        self.assertIn("RELEASE_EVIDENCE_INVALID", text)
        self.assertIn("producer/sources/leanprover-community-flt-regular.json", text)
        self.assertIn("consume_accepted_release.py", text)
        self.assertIn("missing-package.tar.gz", text)
        self.assertNotIn("build-index", text)
        self.assertNotIn("replay_flt_regular.py", text)
        self.assertNotIn("run_research_smoke.py", text)
        for forbidden in (
            "Checkout pinned source",
            "Build source target",
            "LeanDepViz",
            "elan",
            "lake ",
            "_target/source",
        ):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, text)
