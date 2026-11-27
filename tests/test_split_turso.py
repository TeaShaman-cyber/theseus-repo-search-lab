import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from tests import test_projection
from theseus_repo_search.accepted_catalog import AcceptedCorpus
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.projection import build_projection
from theseus_repo_search.split_projection import build_split_projection
from theseus_repo_search.split_turso import (
    materialize_split_search,
    split_database_name,
    split_projection_sha256,
)


class SplitTursoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        artifact = self.root / "artifact"
        test_projection.ProjectionTests().build_artifact(artifact)
        self.full = self.root / "full.sqlite"
        self.fp = build_projection(artifact, self.full)
        self.split = self.root / "split"
        self.receipt = build_split_projection(self.full, self.split, self.fp)
        self.item = AcceptedCorpus(
            "fixture", "tag", "asset", "receipt", "descriptor",
            str(self.receipt["artifact_identity"]), "a"*64,
            split_projection_sha256=split_projection_sha256(self.receipt),
        )
        self.remote = self.root / "remote.sqlite"

    def client(self):
        client = Mock()
        client.get_database.side_effect = lambda name: {"hostname": "fixture.invalid"} if self.remote.exists() else None
        client.create_upload_database.return_value = {"hostname": "fixture.invalid"}
        client.mint_database_token.return_value = "fixture"
        def upload(host, token, db):
            with sqlite3.connect(db) as source, sqlite3.connect(self.remote) as target:
                source.backup(target)
        client.upload_sqlite.side_effect = upload
        def query(host, token, sql, parameters=()):
            with sqlite3.connect(self.remote) as c:
                return c.execute(sql, parameters).fetchall()
        client.query_rows.side_effect = query
        return client

    def test_materialization_preserves_split_and_reuse_does_not_upload(self):
        client = self.client()
        result = materialize_split_search(self.item, self.split, self.fp, client, group="repo-search")
        self.assertEqual(result["database"], split_database_name(self.item))
        self.assertEqual(result["status"], "CREATED")
        with sqlite3.connect(self.remote) as c:
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='edges'").fetchone())
            self.assertEqual(c.execute("SELECT value FROM meta WHERE key='split_projection_sha256'").fetchone(), (self.item.split_projection_sha256,))
        again = materialize_split_search(self.item, self.split, self.fp, client, group="repo-search")
        self.assertEqual(again["status"], "REUSED")
        self.assertEqual(client.upload_sqlite.call_count, 1)

    def test_wrong_catalog_pin_blocks_before_remote_mutation(self):
        client = self.client()
        with self.assertRaises(RepoSearchError):
            materialize_split_search(replace(self.item, split_projection_sha256="0"*64), self.split, self.fp, client, group="repo-search")
        client.get_database.assert_not_called()
        client.create_upload_database.assert_not_called()

    def test_existing_candidate_wrong_identity_not_overwritten(self):
        client = self.client()
        materialize_split_search(self.item, self.split, self.fp, client, group="repo-search")
        with sqlite3.connect(self.remote) as c:
            c.execute("UPDATE meta SET value='wrong' WHERE key='artifact_identity'")
        with self.assertRaises(RepoSearchError):
            materialize_split_search(self.item, self.split, self.fp, client, group="repo-search")
        self.assertEqual(client.upload_sqlite.call_count, 1)
