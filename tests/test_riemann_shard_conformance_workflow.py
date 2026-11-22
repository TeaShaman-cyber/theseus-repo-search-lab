import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/riemann-shard-conformance.yml")


class RiemannShardConformanceWorkflowTests(unittest.TestCase):
    def test_workflow_runs_direct_and_two_shards_then_merges(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("mode: direct", text)
        self.assertIn("mode: shard", text)
        self.assertIn("shard_index: 0", text)
        self.assertIn("shard_index: 1", text)
        self.assertIn("scripts/plan_lean_shards.py", text)
        self.assertIn("--module-prefix Lemmata", text)
        self.assertIn("scripts/merge_raw_depgraph_shards.py", text)
        self.assertIn("needs: extract", text)
        self.assertIn("CONFORMANCE_PASS", text)

    def test_full_lemmata_build_is_direct_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        build = text.split("- name: Build Lemmata through lazy project cache", 1)[1].split(
            "- name: Prepare synthetic shard root", 1
        )[0]
        self.assertIn("if: matrix.mode == 'direct'", build)

    def test_shard_roots_are_synthetic_and_untracked(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Lemmata.TheseusShard", text)
        self.assertIn("src/Lemmata/TheseusShard", text)
        self.assertIn("--untracked-files=no", Path("scripts/producer_guard.py").read_text())


if __name__ == "__main__":
    unittest.main()
