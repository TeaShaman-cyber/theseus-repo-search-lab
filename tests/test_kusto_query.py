import unittest
from unittest.mock import Mock

from theseus_repo_search.accepted_catalog import AcceptedCorpus
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.kusto_query import reach


class KustoReachTests(unittest.TestCase):
    def setUp(self):
        self.item = AcceptedCorpus("zeta23", "tag", "asset", "receipt", "descriptor", "a"*64, "b"*64)
        self.client = Mock()
        self.ready = [self.item.artifact_identity, "revision", "internal_only", 3, 2, '["Zeta23"]', "git", "null", True, "lean-dep-viz"]

    def test_reach_is_bounded_and_identity_bound(self):
        self.client.query.side_effect = [[self.ready], [[3, 2]], [[1]], [["lean:B", 1], ["lean:C", 2]]]
        r = reach(self.item, self.client, "A", depth=2, limit=1)
        self.assertEqual(r["status"], "DEGRADED")
        self.assertEqual(r["artifact_identity"], self.item.artifact_identity)
        self.assertEqual(r["nodes"], [{"id": "lean:B", "depth": 1}])
        self.assertFalse(r["complete_within_scope"])
        self.assertEqual(r["scope_root_modules"], ["Zeta23"])
        self.assertIn("take 2", self.client.query.call_args.args[0])

    def test_wrong_identity_blocks_before_graph(self):
        self.client.query.return_value = [["wrong", "rev", "internal_only", 3, 2]]
        with self.assertRaises(RepoSearchError):
            reach(self.item, self.client, "A")
        self.assertEqual(self.client.query.call_count, 1)

    def test_missing_root_is_not_empty_reachable_set(self):
        self.client.query.side_effect = [[self.ready], [[3, 2]], [[0]]]
        r = reach(self.item, self.client, "A")
        self.assertEqual(r["status"], "UNKNOWN")
        self.assertFalse(r["found"])

    def test_count_drift_blocks(self):
        self.client.query.side_effect = [[self.ready], [[3, 1]]]
        with self.assertRaises(RepoSearchError):
            reach(self.item, self.client, "A")

    def test_reverse_and_literal_escaping(self):
        self.client.query.side_effect = [[self.ready], [[3, 2]], [[1]], []]
        r = reach(self.item, self.client, 'A"|take 1', reverse=True)
        self.assertEqual(r["status"], "FOUND")
        self.assertIn('a)<-[e*1..3]-(b)', self.client.query.call_args.args[0])
        self.assertIn('lean:A\\"|take 1', self.client.query.call_args.args[0])

    def test_bounds_before_network(self):
        with self.assertRaises(RepoSearchError):
            reach(self.item, self.client, "A", depth=9)
        self.client.query.assert_not_called()


    def test_invalid_scope_rejected(self):
        self.ready[5] = "{}"
        self.client.query.return_value = [self.ready]
        with self.assertRaises(RepoSearchError):
            reach(self.item, self.client, "A")

    def test_zero_edges_are_valid_for_isolated_root(self):
        self.ready[4] = 0
        self.client.query.side_effect = [[self.ready], [[3, 0]], [[1]]]
        r = reach(self.item, self.client, "A")
        self.assertEqual(r["status"], "FOUND")
        self.assertEqual(r["nodes"], [])

    def test_preview_graph_never_claims_complete(self):
        self.ready[9] = "olean-export-deps-preview"
        self.client.query.side_effect = [[self.ready], [[3, 2]], [[1]], [["lean:B", 1]]]
        r = reach(self.item, self.client, "A")
        self.assertEqual(r["status"], "DEGRADED")
        self.assertFalse(r["complete_within_scope"])
        self.assertTrue(r["found"])

    def test_empty_preview_graph_never_claims_complete(self):
        self.ready[9] = "olean-export-deps-preview"
        self.ready[4] = 0
        self.client.query.side_effect = [[self.ready], [[3, 0]], [[1]]]
        r = reach(self.item, self.client, "A")
        self.assertEqual(r["status"], "DEGRADED")
        self.assertFalse(r["complete_within_scope"])

    def test_missing_preview_root_is_degraded(self):
        self.ready[9] = "olean-export-deps-preview"
        self.client.query.side_effect = [[self.ready], [[3, 2]], [[0]]]
        r = reach(self.item, self.client, "A")
        self.assertEqual(r["status"], "DEGRADED")
        self.assertFalse(r["found"])

    def test_static_graph_is_not_elaborated_completeness(self):
        self.ready[9] = "source-static-deps"
        self.client.query.side_effect = [[self.ready], [[3, 2]], [[1]], [["lean:B", 1]]]
        r = reach(self.item, self.client, "A")
        self.assertEqual(r["status"], "DEGRADED")
        self.assertFalse(r["complete_within_scope"])
