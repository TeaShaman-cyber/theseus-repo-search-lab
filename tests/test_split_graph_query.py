import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.split_turso import split_projection_sha256


class SplitGraphQueryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.receipt = {"profile": "repo-search-split-v1", "artifact_identity": "a"*64,
                        "full_projection_fingerprint": "b"*64, "search_projection_fingerprint": "c"*64,
                        "nodes": 2, "edges": 1, "sources": 3}
        self.pin = split_projection_sha256(self.receipt)
        self.item = AcceptedCorpus("fixture", "tag", "asset", "receipt", "descriptor",
                                   "a"*64, "d"*64, split_projection_sha256=self.pin)
        self.catalog = AcceptedCatalog("theseus.repo-search.accepted-catalog.v1", "example/repo", (self.item,))
        self.prepared = self.root / "prepared"
        self.prepared.mkdir()
        (self.prepared / "graph.duckdb").write_bytes(b"verified graph fixture")

    def test_warm_cache_does_not_recover_full_release_and_rejects_corruption(self):
        from theseus_repo_search.split_graph_query import ensure_serving_graph

        with patch("theseus_repo_search.split_graph_query.prepare_catalog_graph",
                   return_value={"split": self.receipt, "directory": str(self.prepared)}) as prepare:
            first = ensure_serving_graph(self.catalog, self.item, repository_root=self.root, cache_root=self.root)
            second = ensure_serving_graph(self.catalog, self.item, repository_root=self.root, cache_root=self.root)
            self.assertEqual(first, second)
            self.assertEqual(prepare.call_count, 1)
            first.write_bytes(b"corrupt")
            with self.assertRaises(RepoSearchError):
                ensure_serving_graph(self.catalog, self.item, repository_root=self.root, cache_root=self.root)
            self.assertEqual(prepare.call_count, 1)

    def test_frontier_goes_local_and_metadata_stays_in_turso(self):
        from theseus_repo_search.split_graph_query import SplitGraphConnection

        search = Mock()
        conn = SplitGraphConnection(search, self.prepared / "graph.duckdb")
        self.assertIs(conn.execute("SELECT key, value FROM meta"), search.execute.return_value)
        sql = ("SELECT source_id, target_id, relation, evidence_grade, producer "
               "FROM edges WHERE source_id IN (?) ORDER BY relation, target_id, source_id, producer LIMIT ?")
        with patch("theseus_repo_search.split_graph_query._native") as native:
            def write(*args, **kwargs):
                kwargs["output"].write("a,b,value_dependency,ELABORATED_DEPENDENCY,lean\n")
            native.side_effect = write
            self.assertEqual(conn.execute(sql, ("a'b", 5)).fetchall(), [("a","b","value_dependency","ELABORATED_DEPENDENCY","lean")])
            self.assertIn("'a''b'", native.call_args.args[2])
            self.assertTrue(native.call_args.kwargs["read_only"])
        self.assertEqual(search.execute.call_count, 1)

    def test_unexpected_edge_statement_never_falls_back_to_turso(self):
        from theseus_repo_search.split_graph_query import SplitGraphConnection

        search = Mock()
        conn = SplitGraphConnection(search, self.prepared / "graph.duckdb")
        with self.assertRaises(RepoSearchError):
            conn.execute("SELECT * FROM edges")
        search.execute.assert_not_called()


    def test_intake_can_publish_serving_graph_without_recovering_release(self):
        from theseus_repo_search.split_graph_query import publish_serving_graph

        with patch("theseus_repo_search.split_graph_query.prepare_catalog_graph") as prepare:
            db = publish_serving_graph(self.item, {"split": self.receipt, "directory": str(self.prepared)}, cache_root=self.root)
        prepare.assert_not_called()
        self.assertEqual(db.read_bytes(), (self.prepared / "graph.duckdb").read_bytes())
