import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_retrieval

from theseus_repo_search import cli
from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus


class AgentCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db, _ = test_retrieval.RetrievalTests().build_db(Path(self.tmp.name))
        self.identity = "a" * 64
        self.item = AcceptedCorpus("zeta", "tag", "asset", "receipt", "descriptor", self.identity, "b"*64)
        self.catalog = AcceptedCatalog("theseus.repo-search.accepted-catalog.v1", "example/repo", (self.item,))
        self.local = sqlite3.connect(self.db)
        self.addCleanup(self.local.close)
        self.local.execute("INSERT OR REPLACE INTO meta VALUES ('artifact_identity', ?)", (self.identity,))
        self.local.commit()
        self.client = Mock()
        self.client.get_database.return_value = {"hostname": "db.example"}
        self.client.query_rows.side_effect = lambda _h, _t, sql, args=(): self.local.execute(sql,args).fetchall()

    def invoke(self, *args):
        with patch.dict("os.environ", {"REPO_SEARCH_CATALOG": "/example/catalog.json",
                        "REPO_SEARCH_TURSO_ORGANIZATION": "example",
                        "REPO_SEARCH_TURSO_PLATFORM_URL": "https://turso.int.exe.xyz"}, clear=True), patch.object(cli, "load_accepted_catalog", return_value=self.catalog), patch.object(cli, "_turso_client", return_value=self.client), patch.object(cli, "_emit") as emit:
            rc = cli.main(["query", *args])
        return rc, emit.call_args.args[0]

    def test_corpora_lists_catalog_not_live_availability(self):
        rc, result = self.invoke("corpora")
        self.assertEqual(rc, 0)
        self.assertEqual(result["corpora"][0]["artifact_identity"], self.identity)
        self.assertEqual(result["availability"], "NOT_CHECKED")
        self.client.get_database.assert_not_called()

    def test_search_carries_artifact_identity(self):
        rc, result = self.invoke("search", "moment", "--limit", "2")
        self.assertEqual(rc, 0)
        self.assertEqual(result["searched_corpora"], 1)
        self.assertTrue(result["hits"])
        self.assertEqual(result["hits"][0]["artifact_identity"], self.identity)

    def test_graph_and_context_use_same_profile(self):
        for op in ("deps", "rdeps", "context"):
            rc, result = self.invoke(op, "zeta", "Zeta23.Tiny.lemmaR_tight_two")
            self.assertEqual(rc, 0)
            self.assertEqual(result["artifact_identity"], self.identity)
            self.assertEqual(result["source_id"], "zeta")

    def test_path(self):
        rc, result = self.invoke("path", "zeta", "Zeta23.Tiny.lemmaR_tight_two", "Zeta23.Tiny.N0star_lower_moment")
        self.assertEqual(rc, 0)
        self.assertTrue(result["found"])

    def test_scoped_search_rejects_unknown_corpus(self):
        rc, result = self.invoke("search", "moment", "--source", "missing")
        self.assertEqual(rc, 2)
        self.assertEqual(result["status"], "BLOCKED")
        self.client.get_database.assert_not_called()

    def test_partial_search_reports_unavailable(self):
        self.client.get_database.return_value = None
        rc, result = self.invoke("search", "moment")
        self.assertEqual(rc, 0)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["searched_corpora"], 0)

    def test_profile_missing_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(cli, "_emit") as emit:
            self.assertEqual(cli.main(["query", "search", "moment"]), 2)
        self.assertEqual(emit.call_args.args[0]["code"], "BLOCKED_QUERY_PROFILE")

    def test_profile_explicit_flags(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(cli, "load_accepted_catalog", return_value=self.catalog), patch.object(cli, "_emit"):
            self.assertEqual(cli.main(["query", "--catalog", "catalog.json", "corpora"]), 0)

    def test_graph_search_preserves_lexical_when_graph_unavailable(self):
        from theseus_repo_search.errors import RepoSearchError

        with patch.object(cli, "_agent_kusto_client", side_effect=RepoSearchError("UNAVAILABLE_KUSTO_AUTH", "unavailable")):
            rc, result = self.invoke("graph-search", "moment", "--limit", "2")
        self.assertEqual(rc, 0)
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["graph_status"], "UNAVAILABLE")
        self.assertTrue(result["hits"])
        self.assertEqual(result["searched_corpora"], 1)

    def test_graph_search_passes_search_seeds_and_bounds(self):
        with patch.object(cli, "_agent_kusto_client"), patch.object(cli, "expand_search_hits", return_value={
                "graph_status": "FOUND", "graphs": [], "graph_seeds": 1}) as expand:
            rc, result = self.invoke("graph-search", "moment", "--limit", "2", "--depth", "1", "--graph-limit", "3", "--reverse")
        self.assertEqual(rc, 0)
        self.assertEqual(result["backend"], "turso+kusto")
        self.assertTrue(expand.call_args.args[2])
        self.assertEqual(expand.call_args.kwargs, {"depth": 1, "limit": 3, "reverse": True})

    def test_graph_search_empty_does_not_need_kusto(self):
        with patch.object(cli, "_agent_kusto_client") as client:
            rc, result = self.invoke("graph-search", "zz_missing_probe_zz")
        self.assertEqual(rc, 0)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["graph_status"], "NOT_RUN")
        client.assert_not_called()

    def test_reach_all_uses_entire_catalog_without_turso(self):
        with patch.object(cli, "_agent_kusto_client"), patch.object(cli, "federated_reach", return_value={
                "status": "FOUND", "searched_corpora": 1}) as reach_all:
            rc, result = self.invoke("reach-all", "A", "--reverse")
        self.assertEqual(rc, 0)
        self.assertEqual(result["searched_corpora"], 1)
        self.assertEqual(reach_all.call_args.args[0], self.catalog)
        self.client.get_database.assert_not_called()

    def test_overlap_runs_read_only_scan_with_profile(self):
        rc, result = self.invoke("overlap", "--limit", "1")
        self.assertEqual(rc, 0)
        self.assertEqual(result["searched_corpora"], 1)
        self.assertEqual(result["shared_names"], 0)
        self.assertEqual(result["scanned_nodes"], self.local.execute("SELECT count(*) FROM nodes").fetchone()[0])
        self.assertEqual(result["filter"], "theorem_candidates_v1")

    def test_overlap_unavailable_is_not_empty_success(self):
        self.client.get_database.return_value = None
        rc, result = self.invoke("overlap")
        self.assertEqual(rc, 0)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["unavailable_corpora"], 1)
