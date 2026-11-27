import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_projection
from theseus_repo_search.projection import build_projection


class LocalGraphCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        artifact = self.root / "artifact"
        test_projection.ProjectionTests().build_artifact(artifact)
        self.full = self.root / "full.sqlite"
        self.fp = build_projection(artifact, self.full)

    def test_prepare_creates_split_and_graph_then_reuses_verified_graph(self):
        from theseus_repo_search.local_graph_cache import prepare_local_graph

        out = self.root / "candidate"
        def build(root, dest, executable):
            dest.write_bytes(b"fixture native graph")
        with patch("theseus_repo_search.local_graph_cache._build_graph", side_effect=build) as create, patch("theseus_repo_search.local_graph_cache._verify_graph"):
            first = prepare_local_graph(self.full, out, self.fp)
            second = prepare_local_graph(self.full, out, self.fp)
        self.assertEqual(first["graph_status"], "CREATED")
        self.assertEqual(second["graph_status"], "REUSED")
        self.assertEqual(first["split"], second["split"])
        self.assertEqual(create.call_count, 1)
        self.assertTrue((out / "graph.duckdb").is_file())

    def test_graph_verification_failure_does_not_publish_candidate(self):
        from theseus_repo_search.local_graph_cache import prepare_local_graph

        out = self.root / "candidate"
        def build(root, dest, executable):
            dest.write_bytes(b"fixture native graph")
        with (
            patch("theseus_repo_search.local_graph_cache._build_graph", side_effect=build),
            patch("theseus_repo_search.local_graph_cache._verify_graph", side_effect=ValueError("bad graph")),
            self.assertRaisesRegex(ValueError, "bad graph"),
        ):
            prepare_local_graph(self.full, out, self.fp)
        self.assertFalse((out / "graph.duckdb").exists())

    def test_wrong_parent_does_not_reuse_existing_graph(self):
        from theseus_repo_search.local_graph_cache import prepare_local_graph

        out = self.root / "candidate"
        def build(root, dest, executable):
            dest.write_bytes(b"fixture native graph")
        with patch("theseus_repo_search.local_graph_cache._build_graph", side_effect=build), patch("theseus_repo_search.local_graph_cache._verify_graph"):
            prepare_local_graph(self.full, out, self.fp)
            with self.assertRaises(ValueError):
                prepare_local_graph(self.full, out, "0"*64)


    def test_catalog_prepare_reuses_existing_corpus_loader(self):
        from unittest.mock import Mock

        from theseus_repo_search.local_graph_cache import prepare_catalog_graph

        state = Mock(db_path=self.full, projection_fingerprint=self.fp, artifact_identity="a"*64)
        root = self.root / "cache"
        def build(split, dest, executable):
            dest.write_bytes(b"fixture native graph")
        with (
            patch("theseus_repo_search.local_graph_cache.ensure_local_corpus", return_value=state),
            patch("theseus_repo_search.local_graph_cache._build_graph", side_effect=build) as create,
            patch("theseus_repo_search.local_graph_cache._verify_graph"),
        ):
            first = prepare_catalog_graph(Mock(), "fixture", repository_root=self.root, cache_root=root)
            second = prepare_catalog_graph(Mock(), "fixture", repository_root=self.root, cache_root=root)
        self.assertEqual(first["graph_status"], "CREATED")
        self.assertEqual(second["graph_status"], "REUSED")
        self.assertEqual(create.call_count, 1)
        self.assertEqual(Path(first["directory"]).parent, root / "split-v1" / ("a"*64))
