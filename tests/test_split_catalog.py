import json
import tempfile
import unittest
from pathlib import Path

from tests.test_accepted_catalog import _entry, _payload
from theseus_repo_search.accepted_catalog import load_accepted_catalog
from theseus_repo_search.turso_materializer import derive_database_name


class SplitCatalogTests(unittest.TestCase):
    def load(self, **fields):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "catalog.json"
            p.write_text(json.dumps(_payload(corpora=[_entry(**fields)])))
            return load_accepted_catalog(p).corpora[0]

    def test_split_pin_is_explicit_and_changes_database_name(self):
        from theseus_repo_search.split_turso import split_database_name

        legacy = self.load()
        split = self.load(split_projection_sha256="a" * 64)
        self.assertEqual(split.split_projection_sha256, "a" * 64)
        self.assertNotEqual(split_database_name(split), derive_database_name(legacy.source_id, legacy.artifact_identity))
        self.assertNotEqual(split_database_name(split), split_database_name(self.load(split_projection_sha256="b" * 64)))
        self.assertLessEqual(len(split_database_name(split)), 52)

    def test_invalid_split_receipt_cannot_silently_select_legacy(self):
        for value in ("a" * 63, "A" * 64, None, "", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(split_projection_sha256=value)

    def test_split_search_never_falls_back_to_legacy_database(self):
        from unittest.mock import Mock

        from theseus_repo_search.errors import RepoSearchError
        from theseus_repo_search.split_turso import split_database_name
        from theseus_repo_search.turso_query import connect_corpus

        item = self.load(split_projection_sha256="a" * 64)
        client = Mock()
        client.get_database.return_value = None
        with self.assertRaises(RepoSearchError):
            connect_corpus(item, client)
        client.get_database.assert_called_once_with(split_database_name(item))

    def test_split_search_requires_exact_profile_and_receipt(self):
        from unittest.mock import Mock

        from theseus_repo_search.errors import RepoSearchError
        from theseus_repo_search.turso_query import connect_corpus

        item = self.load(split_projection_sha256="a" * 64)
        client = Mock()
        client.get_database.return_value = {"hostname": "fixture.invalid"}
        client.query_rows.side_effect = [
            [(item.artifact_identity,)],
            [("projection_profile", "repo-search-split-v1"), ("split_projection_sha256", "b" * 64)],
        ]
        with self.assertRaisesRegex(RepoSearchError, "receipt"):
            connect_corpus(item, client)

    def test_legacy_sync_cannot_recreate_full_graph_for_split_entry(self):
        from unittest.mock import Mock, patch

        from theseus_repo_search import cli
        from theseus_repo_search.accepted_catalog import AcceptedCatalog
        from theseus_repo_search.errors import RepoSearchError

        item = self.load(split_projection_sha256="a" * 64)
        catalog = AcceptedCatalog("theseus.repo-search.accepted-catalog.v1", "example/repo", (item,))
        args = cli.build_parser().parse_args([
            "materialize-turso", "--catalog", "catalog.json", "--source-id", item.source_id,
            "--cache-dir", "cache", "--organization", "example", "--group", "repo-search",
        ])
        with patch.object(cli, "load_accepted_catalog", return_value=catalog), patch.object(cli, "ensure_local_corpus") as ensure, patch.object(cli, "TursoMaterializer", return_value=Mock()):
            with self.assertRaisesRegex(RepoSearchError, "split"):
                args.func(args)
            ensure.assert_not_called()
