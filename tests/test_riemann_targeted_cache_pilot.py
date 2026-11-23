import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/riemann-targeted-cache-pilot.yml")


class RiemannTargetedCachePilotWorkflowTests(unittest.TestCase):
    def test_pilot_uses_source_closure_and_verified_subset_restore_without_lake_build(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("lean_project_import_closure.lean", text)
        self.assertIn("restore_riemann_ci_cache_subset.py", text)
        self.assertNotIn("lake build", text)
        self.assertNotIn("TheseusShard", text)

    def test_pilot_runs_leandepviz_directly_on_original_shard_modules(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('SHARD_ROOTS_CSV=', text)
        self.assertIn('--roots "$SHARD_ROOTS_CSV"', text)
        self.assertIn("LeanDepVizMain.lean", text)

    def test_pilot_records_comparable_restore_and_extraction_metrics(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for field in (
            '"closure_project_modules"',
            '"restore_seconds"',
            '"extract_seconds"',
            '"raw_bytes"',
            '"nodes"',
            '"edges"',
        ):
            self.assertIn(field, text)


if __name__ == "__main__":
    unittest.main()
