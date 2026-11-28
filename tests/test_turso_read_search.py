import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import test_retrieval

from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.retrieval import search
from theseus_repo_search.turso_query import ReadConnection, search_turso_catalog


class TursoReadSearchTests(unittest.TestCase):
    def test_cli_exposes_only_bounded_search_arguments(self):
        from theseus_repo_search.cli import build_parser

        args = build_parser().parse_args(['turso-search', '--catalog', 'catalog.json',
                                         '--organization', 'example', '--query', 'moment'])
        self.assertEqual(args.limit, 20)
        self.assertEqual(args.mode, 'discovery')
        self.assertFalse(hasattr(args, 'sql'))

    def test_parameter_binding_and_result(self):
        client = Mock()
        client.query_rows.return_value = [("one", 2)]
        conn = ReadConnection(client, "db.example", "read-token")
        self.assertEqual(conn.execute("SELECT ?", ("x' OR 1=1",)).fetchall(), [("one", 2)])
        client.query_rows.assert_called_once_with("db.example", "read-token", "SELECT ?", ("x' OR 1=1",))

    def test_same_search_logic_and_explicit_partial_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = test_retrieval.RetrievalTests().build_db(Path(d))
            identity = "a" * 64
            catalog = AcceptedCatalog("theseus.repo-search.accepted-catalog.v1", "example/repo", tuple(
                AcceptedCorpus(s, f"accepted/{s}", "a", "r", "descriptor", identity, "b"*64)
                for s in ("good", "bad")))
            client = Mock()
            from theseus_repo_search.turso_materializer import derive_database_name
            client.get_database.side_effect = lambda name: (
                {"hostname":"db.example"} if name == derive_database_name("good", identity) else None
            )
            client.mint_database_token.return_value = "read-token"
            with sqlite3.connect(db) as local:
                local.execute("INSERT OR REPLACE INTO meta VALUES ('artifact_identity', ?)", (identity,))
                local.commit()
            def query_rows(_host, _token, sql, args=()):
                with sqlite3.connect(db) as connection:
                    return connection.execute(sql, args).fetchall()
            client.query_rows.side_effect = query_rows
            result = search_turso_catalog(catalog, client, query="moment", limit=3)
            self.assertEqual(result.searched_corpora, 1)
            self.assertEqual(result.unavailable_corpora[0].source_id, "bad")
            self.assertEqual([x.hit for x in result.hits], search(db, "moment", limit=3))
            self.assertEqual(client.mint_database_token.call_args.kwargs["authorization"], "read-only")

    def test_identity_mismatch_does_not_query_corpus(self):
        item = AcceptedCorpus("good", "tag", "a", "r", "descriptor", "a"*64, "b"*64)
        catalog = AcceptedCatalog("theseus.repo-search.accepted-catalog.v1", "example/repo", (item,))
        client = Mock()
        client.get_database.return_value = {"hostname":"db.example"}
        client.query_rows.return_value = [("wrong",)]
        result = search_turso_catalog(catalog, client, query="moment", limit=3)
        self.assertEqual(result.searched_corpora, 0)
        self.assertEqual(result.unavailable_corpora[0].code, "TURSO_DATABASE_IDENTITY_MISMATCH")
        self.assertEqual(client.query_rows.call_count, 1)

    def test_http_uses_bound_arguments_and_rejects_short_result(self):
        from theseus_repo_search.turso_materializer import TursoHttpClient
        c = TursoHttpClient(organization="example")
        c._json_request = Mock(return_value={"results": [{"type":"ok","response":{"result":{"rows":[]}}}]})
        with self.assertRaises(RepoSearchError):
            c.query_rows("db.example", "token", "SELECT ?, ?", ("a'b", 4))
        stmt = c._json_request.call_args.kwargs["payload"]["requests"][0]["stmt"]
        self.assertEqual(stmt["args"], [{"type":"text","value":"a'b"},{"type":"integer","value":"4"}])
