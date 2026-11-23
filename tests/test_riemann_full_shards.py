import unittest
from pathlib import Path

from theseus_repo_search.producer_config import load_lean_git_source

DESCRIPTOR = Path("producer/pilots/riemann-hypothesis-full.json")
WORKFLOW = Path(".github/workflows/riemann-full-raw-shards.yml")


class RiemannFullDescriptorTests(unittest.TestCase):
    def test_descriptor_binds_exact_full_source_scope(self):
        source = load_lean_git_source(DESCRIPTOR)
        self.assertEqual(
            source.source_commit,
            "1b894f7511841e6607bc0398cf2657526bbb29d9",
        )
        self.assertEqual(
            source.root_modules,
            (
                "Challenge",
                "CompanionTheory",
                "Development",
                "Formalism",
                "GroundTheory",
                "Lemmata",
                "Main",
                "PositivityTheory",
                "ReciprocityTheory",
                "Statements",
                "Supplements",
            ),
        )
        self.assertEqual(source.exclude_source_prefixes, ())


class RiemannFullShardWorkflowTests(unittest.TestCase):
    def test_workflow_parameterizes_shard_count_and_matrix(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("shard_count:", text)
        self.assertIn('default: "64"', text)
        self.assertIn("options:", text)
        self.assertIn('"32"', text)
        self.assertIn('"64"', text)
        self.assertIn('"128"', text)
        self.assertIn("plan_matrix:", text)
        self.assertIn("shard_matrix=", text)
        self.assertIn("fromJSON(needs.plan_matrix.outputs.shard_matrix)", text)
        self.assertIn("SHARD_COUNT: " + "$" + "{{ needs.plan_matrix.outputs.shard_count }}", text)
        self.assertIn('--shard-count "$SHARD_COUNT"', text)
        self.assertIn("max-parallel: 16", text)

    def test_single_shard_dispatch_is_validated_by_plan_job(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ONLY_SHARD:", text)
        self.assertIn("only_shard must be -1", text)
        self.assertNotIn("matrix.shard_index ==", text)

    def test_each_shard_uploads_raw_evidence_and_small_metrics_separately(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("riemann-rh-raw-shard-", text)
        self.assertIn("riemann-rh-metrics-shard-", text)
        self.assertIn("raw-depgraph-receipt.json", text)
        self.assertIn("shard-metrics.json", text)
        self.assertIn('"raw_bytes"', text)
        self.assertIn('"nodes"', text)
        self.assertIn('"edges"', text)
        self.assertIn('"source_bytes"', text)
        self.assertIn('"shard_count": int(os.environ["SHARD_COUNT"])', text)

    def test_shards_clean_source_and_outputs_after_upload(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("rm -rf _target _out", text)
        self.assertIn("test ! -e _target", text)
        self.assertIn("test ! -e _out", text)


if __name__ == "__main__":
    unittest.main()
