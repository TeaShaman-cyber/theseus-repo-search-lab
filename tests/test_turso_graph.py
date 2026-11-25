import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_retrieval

from theseus_repo_search.graph import dependencies, path, reverse_dependencies
from theseus_repo_search.retrieval import context
from theseus_repo_search.turso_query import ReadConnection, query_graph


class TursoGraphTests(unittest.TestCase):
    def test_cli_partial_path_is_degraded_with_artifact_identity(self):
        from theseus_repo_search import cli
        from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus
        from theseus_repo_search.graph import GraphResult

        item = AcceptedCorpus('zeta', 'tag', 'asset', 'receipt', 'descriptor', 'a'*64, 'b'*64)
        catalog = AcceptedCatalog('theseus.repo-search.accepted-catalog.v1', 'example/repo', (item,))
        args = cli.build_parser().parse_args([
            'turso-graph', '--catalog', 'catalog.json', '--organization', 'example',
            '--source-id', 'zeta', '--operation', 'path', '--name', 'A', '--target', 'B',
        ])
        result = GraphResult('path:A->B', (), ('Pkg',), 'internal_only', False, 'git', 'commit', None, True, False)
        with patch.object(cli, 'load_accepted_catalog', return_value=catalog), patch.object(cli, 'connect_corpus'), patch.object(cli, 'query_graph', return_value=result), patch.object(cli, '_emit') as emit:
            self.assertEqual(args.func(args), 0)
        payload = emit.call_args.args[0]
        self.assertEqual(payload['status'], 'DEGRADED')
        self.assertFalse(payload['found'])
        self.assertEqual(payload['artifact_identity'], item.artifact_identity)
        self.assertEqual(payload['source_revision'], 'commit')

    def test_hosted_graph_and_context_reuse_existing_results(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = test_retrieval.RetrievalTests().build_db(Path(d))
            start, end = 'Zeta23.Tiny.lemmaR_tight_two', 'Zeta23.Tiny.N0star_lower_moment'
            client = Mock()
            with sqlite3.connect(db) as local:
                client.query_rows.side_effect = lambda _host, _token, sql, args=(): local.execute(sql, args).fetchall()
                conn = ReadConnection(client, 'db.example', 'read-token')
                self.assertEqual(query_graph(conn, 'deps', start, depth=1), dependencies(db, start))
                self.assertEqual(query_graph(conn, 'rdeps', end, depth=1), reverse_dependencies(db, end))
                self.assertEqual(query_graph(conn, 'path', start, target=end, depth=1), path(db, start, end, max_depth=1))
                self.assertEqual(query_graph(conn, 'context', start, depth=1), context(db, start, depth=1))

    def test_budget_exhaustion_is_not_a_complete_graph_or_no_path(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = test_retrieval.RetrievalTests().build_db(Path(d))
            start, end = 'lean:Zeta23.Tiny.lemmaR_tight_two', 'lean:Zeta23.Tiny.N0star_lower_moment'
            with sqlite3.connect(db) as local:
                local.execute('INSERT INTO edges VALUES (?,?,?,?,?)', (start,start,'type_dependency','ELABORATED_TYPE_DEPENDENCY','test'))
                client = Mock()
                client.query_rows.side_effect = lambda _host, _token, sql, args=(): local.execute(sql, args).fetchall()
                conn = ReadConnection(client,'db.example','read-token')
                result = query_graph(conn,'deps',start,depth=1,max_edges=1)
                self.assertFalse(result.complete_within_scope)
                self.assertEqual(len(result.edges),1)
                result = query_graph(conn,'path',start,target=end,depth=1,max_edges=1)
                self.assertFalse(result.complete_within_scope)
                self.assertFalse(result.found)
                result = query_graph(conn,'context',start,depth=1,max_edges=1)
                self.assertFalse(result['complete_within_scope'])
