import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/riemann-corpus-shard-pilot.yml")


class RiemannPilotMetricsTests(unittest.TestCase):
    def test_normalized_counts_come_from_built_sqlite(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"normalized_nodes":counts["nodes"]', text)
        self.assertIn('"normalized_edges":counts["edges"]', text)
        self.assertNotIn('manifest["nodes_count"]', text)
        self.assertNotIn('manifest["edges_count"]', text)


if __name__ == "__main__":
    unittest.main()
