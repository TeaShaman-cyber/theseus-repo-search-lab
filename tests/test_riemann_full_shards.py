import json
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
    def test_workflow_runs_exactly_32_balanced_raw_shards(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("max-parallel: 16", text)
        self.assertIn("--shard-count 32", text)
        self.assertIn("--source-prefix src", text)
        self.assertIn("Development.TheseusShard", text)
        self.assertIn('lake build "$shard_root"', text)
        self.assertNotIn("lake build Lemmata", text)

        full_matrix = json.dumps(list(range(32)), separators=(",", ":"))
        self.assertIn(f"fromJSON('{full_matrix}')", text)

    def test_single_shard_dispatch_uses_dynamic_matrix_not_job_if(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = text.split("jobs:", 1)[1]
        header = jobs.split("strategy:", 1)[0]
        self.assertNotIn("matrix.shard_index", header)
        self.assertIn("fromJSON", text)
        self.assertIn("inputs.only_shard == '-1'", text)
        self.assertIn("format('[{0}]', inputs.only_shard)", text)

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

    def test_shards_clean_source_and_outputs_after_upload(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("rm -rf _target _out", text)
        self.assertIn("test ! -e _target", text)
        self.assertIn("test ! -e _out", text)


if __name__ == "__main__":
    unittest.main()
