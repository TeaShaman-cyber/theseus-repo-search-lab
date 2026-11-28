import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus
from theseus_repo_search.errors import RepoSearchError


class SplitIntakeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.receipt = {
            "profile": "repo-search-split-v1",
            "artifact_identity": "a"*64,
            "full_projection_fingerprint": "b"*64,
            "search_projection_fingerprint": "c"*64,
            "nodes": 2, "edges": 1, "sources": 3,
        }
        from theseus_repo_search.split_turso import split_projection_sha256
        self.pin = split_projection_sha256(self.receipt)
        self.item = AcceptedCorpus("fixture", "tag", "asset", "receipt", "descriptor",
                                   "a"*64, "d"*64, split_projection_sha256=self.pin)
        self.catalog = AcceptedCatalog("theseus.repo-search.accepted-catalog.v1", "example/repo", (self.item,))

    def test_complete_intake_reuses_preparation_and_materializes_both(self):
        from theseus_repo_search.split_intake import materialize_corpus

        prepared = {"split": self.receipt, "directory": str(self.root), "graph_status": "REUSED"}
        with (
            patch("theseus_repo_search.split_intake.prepare_catalog_graph", return_value=prepared),
            patch("theseus_repo_search.split_intake.publish_serving_graph", return_value=self.root/"graph.duckdb"),
            patch("theseus_repo_search.split_intake.materialize_split_search", return_value={"status": "CREATED"}) as search,
            patch("theseus_repo_search.split_intake.materialize_motherduck", return_value={"status": "REUSED"}) as graph,
        ):
            result = materialize_corpus(self.catalog, "fixture", repository_root=self.root,
                                        cache_root=self.root, client=Mock(), group="repo-search",
                                        launcher=self.root/"launcher.py", database="my_db")
        self.assertEqual(result["search"]["status"], "CREATED")
        self.assertEqual(result["graph"]["status"], "REUSED")
        self.assertEqual(result["split_projection_sha256"], self.pin)
        search.assert_called_once()
        graph.assert_called_once()

    def test_wrong_pin_blocks_both_remote_writes(self):
        from theseus_repo_search.split_intake import materialize_corpus

        prepared = {"split": {**self.receipt, "edges": 99}, "directory": str(self.root)}
        with (
            patch("theseus_repo_search.split_intake.prepare_catalog_graph", return_value=prepared),
            patch("theseus_repo_search.split_intake.materialize_split_search") as search,
            patch("theseus_repo_search.split_intake.materialize_motherduck") as graph,
            self.assertRaises(RepoSearchError),
        ):
            materialize_corpus(self.catalog, "fixture", repository_root=self.root,
                               cache_root=self.root, client=Mock(), group="repo-search",
                               launcher=self.root/"launcher.py", database="my_db")
        search.assert_not_called()
        graph.assert_not_called()

    def test_partial_remote_graph_is_not_overwritten(self):
        from theseus_repo_search.split_intake import materialize_motherduck

        with (
            patch("theseus_repo_search.split_intake._md_rows", return_value=[["1","1"]]) as remote,
            patch("theseus_repo_search.split_intake._export_graph") as export,
            self.assertRaisesRegex(RepoSearchError, "partial"),
        ):
            materialize_motherduck(self.item, self.root, self.receipt,
                                   launcher=self.root/"launcher.py", database="my_db")
        remote.assert_called_once()
        export.assert_not_called()

    def test_graph_existing_rows_must_match_before_reuse(self):
        from theseus_repo_search.split_intake import (
            materialize_motherduck,
            motherduck_targets,
        )
        targets = motherduck_targets(self.item,"my_db")
        schema = []
        for table, columns in zip(targets, (
            (("node_key","BIGINT"),("id","VARCHAR")),
            (("source_key","BIGINT"),("target_key","BIGINT"),("relation","VARCHAR"),("evidence_grade","VARCHAR"),("producer","VARCHAR")),
            (("artifact_identity","VARCHAR"),("split_projection_sha256","VARCHAR")),
        ), strict=True):
            schema.extend([table.rsplit(".",1)[1],name,kind,str(pos)] for pos,(name,kind) in enumerate(columns,1))
        schema.sort(key=lambda row:(row[0],int(row[3])))
        with (
            patch("theseus_repo_search.split_intake._md_rows", side_effect=[[["3","3"]], schema, [["2","1","0","1","0","0","1"]]]),
            patch("theseus_repo_search.split_intake._export_graph"),
            self.assertRaisesRegex(RepoSearchError, "parity"),
        ):
            materialize_motherduck(self.item, self.root, self.receipt,
                                   launcher=self.root/"launcher.py", database="my_db")


    def test_motherduck_failure_preserves_exact_successful_turso_receipt(self):
        import json

        from theseus_repo_search.split_intake import materialize_corpus

        prepared = {"split": self.receipt, "directory": str(self.root)}
        search = {"status": "CREATED", "database": "exact-candidate", "database_id": "provider-id"}
        with (
            patch("theseus_repo_search.split_intake.prepare_catalog_graph", return_value=prepared),
            patch("theseus_repo_search.split_intake.publish_serving_graph", return_value=self.root/"graph.duckdb"),
            patch("theseus_repo_search.split_intake.materialize_split_search", return_value=search),
            patch("theseus_repo_search.split_intake.materialize_motherduck", side_effect=RepoSearchError("UNAVAILABLE_GRAPH", "fixture")),
            self.assertRaises(RepoSearchError),
        ):
            materialize_corpus(self.catalog, "fixture", repository_root=self.root,
                               cache_root=self.root, client=Mock(), group="repo-search",
                               launcher=self.root/"launcher.py", database="my_db")
        receipts = list((self.root/"intake-receipts").rglob("*.json"))
        self.assertEqual(len(receipts), 1)
        r = json.loads(receipts[0].read_text())
        self.assertEqual(r["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(r["search"], search)
        self.assertEqual(r["phase"], "MOTHERDUCK")

    def test_launcher_environment_is_allowlisted(self):
        import os
        import subprocess

        from theseus_repo_search.split_intake import _md_rows

        with (
            patch.dict(os.environ, {"SECRET_TURSO_TEST": "private", "PATH": "/bin"}),
            patch("theseus_repo_search.split_intake.subprocess.run", return_value=subprocess.CompletedProcess([],0,stdout="value\n1\n",stderr="")) as run,
        ):
            self.assertEqual(_md_rows(self.root/"launcher.py","my_db","SELECT 1"), [["1"]])
        self.assertNotIn("SECRET_TURSO_TEST", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["PATH"], "/bin")

    def test_reused_graph_schema_drift_is_rejected(self):
        from theseus_repo_search.split_intake import materialize_motherduck

        with (
            patch("theseus_repo_search.split_intake._md_rows", side_effect=[[["3","3"]],[["wrong","column","INTEGER","1"]]]),
            patch("theseus_repo_search.split_intake._export_graph"),
            self.assertRaisesRegex(RepoSearchError, "schema"),
        ):
            materialize_motherduck(self.item, self.root, self.receipt,
                                   launcher=self.root/"launcher.py", database="my_db")

    def test_existing_views_are_blocked_before_export(self):
        from theseus_repo_search.split_intake import materialize_motherduck

        def query(launcher,database,sql,**kwargs):
            if "information_schema.tables" in sql:
                return [["3","0"]] if "BASE TABLE" in sql else [["3"]]
            return []
        with (
            patch("theseus_repo_search.split_intake._md_rows",side_effect=query),
            patch("theseus_repo_search.split_intake._export_graph") as export,
            self.assertRaisesRegex(RepoSearchError,"partial"),
        ):
            materialize_motherduck(self.item,self.root,self.receipt,launcher=self.root/"launcher.py",database="my_db")
        export.assert_not_called()

    def test_turso_post_create_failure_preserves_id_in_journal(self):
        import json

        from theseus_repo_search.split_intake import materialize_corpus

        def search(*args, **kwargs):
            kwargs["on_observed"]({"database_id":"actual-id","status":"CREATED_UNVERIFIED"})
            raise RepoSearchError("UNAVAILABLE","upload")
        with (
            patch("theseus_repo_search.split_intake.prepare_catalog_graph",return_value={"split":self.receipt,"directory":str(self.root)}),
            patch("theseus_repo_search.split_intake.publish_serving_graph",return_value=self.root/"graph.duckdb"),
            patch("theseus_repo_search.split_intake.materialize_split_search",side_effect=search),
            self.assertRaises(RepoSearchError),
        ):
            materialize_corpus(self.catalog,"fixture",repository_root=self.root,cache_root=self.root,client=Mock(),group="repo-search",launcher=self.root/"launcher.py",database="my_db")
        receipt=json.loads(next((self.root/"intake-receipts").rglob("*.json")).read_text())
        self.assertEqual(receipt["search"]["database_id"],"actual-id")
        self.assertEqual(receipt["phase"],"TURSO")
