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
