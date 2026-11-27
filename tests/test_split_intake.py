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
            patch("theseus_repo_search.split_intake._md_rows", return_value=[["1"]]) as remote,
            patch("theseus_repo_search.split_intake._export_graph") as export,
            self.assertRaisesRegex(RepoSearchError, "partial"),
        ):
            materialize_motherduck(self.item, self.root, self.receipt,
                                   launcher=self.root/"launcher.py", database="my_db")
        remote.assert_called_once()
        export.assert_not_called()

    def test_graph_existing_rows_must_match_before_reuse(self):
        from theseus_repo_search.split_intake import materialize_motherduck

        with (
            patch("theseus_repo_search.split_intake._md_rows", side_effect=[[["3"]], [["2","1","0","1","0","0","1"]]]),
            patch("theseus_repo_search.split_intake._export_graph"),
            self.assertRaisesRegex(RepoSearchError, "parity"),
        ):
            materialize_motherduck(self.item, self.root, self.receipt,
                                   launcher=self.root/"launcher.py", database="my_db")
