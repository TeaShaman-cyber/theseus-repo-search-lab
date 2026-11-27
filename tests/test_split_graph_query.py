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


    def test_multiple_frontiers_use_one_native_session(self):
        from theseus_repo_search.split_graph_query import SplitGraphConnection

        sql = ("SELECT source_id, target_id, relation, evidence_grade, producer "
               "FROM edges WHERE source_id IN (?) ORDER BY relation, target_id, source_id, producer LIMIT ?")
        with patch("theseus_repo_search.split_graph_query.NativeGraphSession") as session:
            session.return_value.query.return_value = [("a","b","r","g","p")]
            with SplitGraphConnection(Mock(), self.prepared/"graph.duckdb") as conn:
                self.assertEqual(conn.execute(sql, ("a",5)).fetchall(), [("a","b","r","g","p")])
                self.assertEqual(conn.execute(sql, ("b",5)).fetchall(), [("a","b","r","g","p")])
            session.assert_called_once()
            self.assertEqual(session.return_value.query.call_count,2)
            session.return_value.close.assert_called_once()

    def test_native_empty_result_is_valid_and_session_can_continue(self):
        import time

        from theseus_repo_search.split_graph_query import NativeGraphSession

        session = NativeGraphSession.__new__(NativeGraphSession)
        session.process = Mock()
        session.buffer = b""
        session.deadline = time.monotonic() + 10
        with (
            patch("theseus_repo_search.split_graph_query.select.select", return_value=([session.process.stdout],[],[])),
            patch("theseus_repo_search.split_graph_query.os.read", side_effect=[
                b'\n{"repo_search_end":1}\n',
                b'{"source_id":"a","target_id":"b","relation":"r","evidence_grade":"g","producer":"p"}\n{"repo_search_end":1}\n',
            ]),
        ):
            self.assertEqual(session.query("SELECT fixture"), [])
            self.assertEqual(session.query("SELECT fixture"), [("a","b","r","g","p")])

    def test_missing_executable_is_structured_unavailable(self):
        from theseus_repo_search.split_graph_query import NativeGraphSession

        with self.assertRaises(RepoSearchError) as error:
            NativeGraphSession(str(self.root/"missing-duckdb"), self.prepared/"graph.duckdb")
        self.assertEqual(error.exception.code, "UNAVAILABLE_LOCAL_GRAPH")

    def test_cold_recovery_precedes_read_token_mint(self):
        from theseus_repo_search import cli

        args = cli.build_parser().parse_args([
            "turso-graph", "--catalog", "catalog.json", "--source-id", self.item.source_id,
            "--organization", "example", "--operation", "deps", "--name", "a",
        ])
        events = []
        def recover(*args, **kwargs):
            events.append("recover")
            return self.prepared/"graph.duckdb"
        def connect(*args, **kwargs):
            events.append("token")
            return Mock()
        with (
            patch.object(cli, "ensure_serving_graph", side_effect=recover),
            patch.object(cli, "connect_corpus", side_effect=connect),
            patch.object(cli, "_turso_client"),
            patch.object(cli, "query_graph", side_effect=RepoSearchError("FIXTURE","stop after connection")),
            self.assertRaises(RepoSearchError),
        ):
            cli._cmd_turso_graph(args, catalog=self.catalog)
        self.assertEqual(events, ["recover","token"])
