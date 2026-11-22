import sqlite3
import tempfile
import unittest
from pathlib import Path

from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.multicorpus import LocalCorpusState
from theseus_repo_search.turso_materializer import (
    TursoMaterializer,
    derive_database_name,
    prepare_upload_copy,
)


def _state(db_path: Path, *, source_id: str = "annals-challenge-marton") -> LocalCorpusState:
    return LocalCorpusState(
        source_id=source_id,
        release_tag=f"accepted-artifact/{source_id}/fixture",
        artifact_identity="a" * 64,
        fingerprint_sha256="b" * 64,
        artifact_dir=db_path.parent / "artifact",
        db_path=db_path,
        projection_fingerprint="c" * 64,
    )


def _write_projection(path: Path, *, identity: str = "a" * 64) -> None:
    conn = sqlite3.connect(path)
    try:
        with conn:
            conn.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE nodes (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  module TEXT NOT NULL,
                  source_path TEXT,
                  source_start_line INTEGER,
                  source_end_line INTEGER,
                  source_commit TEXT NOT NULL
                );
                CREATE TABLE edges (
                  source_id TEXT NOT NULL,
                  target_id TEXT NOT NULL,
                  relation TEXT NOT NULL,
                  evidence_grade TEXT NOT NULL,
                  producer TEXT NOT NULL,
                  PRIMARY KEY (source_id, target_id, relation, producer)
                );
                CREATE TABLE sources (
                  id TEXT PRIMARY KEY,
                  source_commit TEXT NOT NULL,
                  source_path TEXT NOT NULL,
                  source_start_line INTEGER NOT NULL,
                  source_end_line INTEGER NOT NULL,
                  declaration_hint TEXT,
                  text TEXT NOT NULL,
                  content_sha256 TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE sources_fts USING fts5(
                  declaration_hint,
                  text,
                  content='sources',
                  content_rowid='rowid',
                  tokenize='unicode61'
                );
                """
            )
            conn.execute("INSERT INTO meta VALUES ('artifact_identity', ?)", (identity,))
            conn.execute("INSERT INTO meta VALUES ('source_commit', ?)", ("d" * 40,))
            conn.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("lean:Pkg.target", "target", "thm", "Pkg", "Pkg.lean", 1, 1, "d" * 40),
            )
            conn.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "src:Pkg.lean:1:1",
                    "d" * 40,
                    "Pkg.lean",
                    1,
                    1,
                    "target",
                    "theorem target mentions Bernoulli",
                    "e" * 64,
                ),
            )
            conn.execute("INSERT INTO sources_fts(sources_fts) VALUES('rebuild')")
            conn.execute("INSERT INTO sources_fts(sources_fts) VALUES('integrity-check')")
    finally:
        conn.close()


class FakeTursoClient:
    def __init__(self, *, existing=None, remote=None):
        self.existing = existing
        self.remote = remote
        self.created = []
        self.uploaded = []
        self.protected = []

    def get_database(self, name):
        if self.existing is None:
            return None
        return dict(self.existing, name=name)

    def create_upload_database(self, name, *, group, size_limit=None):
        self.created.append((name, group, size_limit))
        return {"name": name, "hostname": f"{name}.example.invalid"}

    def mint_database_token(self, name, *, expiration, authorization):
        self.token_request = (name, expiration, authorization)
        return "secret-not-for-output"

    def upload_sqlite(self, hostname, token, db_path):
        self.uploaded.append((hostname, token, Path(db_path).read_bytes()))

    def read_projection_state(self, hostname, token):
        assert token == "secret-not-for-output"
        return dict(self.remote)

    def set_delete_protection(self, name, enabled):
        self.protected.append((name, enabled))


class TursoMaterializerTests(unittest.TestCase):
    def test_database_name_is_valid_bounded_and_stable(self):
        identity = "a" * 64
        short = derive_database_name("openai-navier-stokes", identity)
        self.assertEqual(short, "repo-search-openai-navier-stokes-aaaaaaaa")

        long_source = "Very_Long.Source/" + "x" * 100
        first = derive_database_name(long_source, identity)
        second = derive_database_name(long_source, identity)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 52)
        self.assertRegex(first, r"^[a-z0-9-]+$")
        self.assertTrue(first.startswith("repo-search-"))

    def test_prepare_upload_copy_preserves_source_and_meets_turso_file_contract(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "source.sqlite"
            _write_projection(source)
            source_bytes = source.read_bytes()
            prepared = prepare_upload_copy(source, root / "upload.sqlite")

            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(prepared.artifact_identity, "a" * 64)
            self.assertEqual(prepared.nodes_count, 1)
            self.assertEqual(prepared.edges_count, 0)
            self.assertEqual(prepared.sources_count, 1)
            self.assertEqual(prepared.fts_count, 1)
            self.assertEqual(prepared.page_size, 4096)
            self.assertEqual(prepared.auto_vacuum, 0)
            self.assertEqual(prepared.encoding.upper(), "UTF-8")
            self.assertEqual(prepared.journal_mode.lower(), "wal")
            self.assertEqual(prepared.quick_check, "ok")
            self.assertEqual(prepared.integrity_check, "ok")
            self.assertEqual(prepared.sqlite_bytes, prepared.page_size * prepared.page_count)

    def test_new_database_is_upload_seeded_verified_then_protected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            db = root / "source.sqlite"
            _write_projection(db)
            state = _state(db)
            with tempfile.TemporaryDirectory() as stage:
                prepared = prepare_upload_copy(db, Path(stage) / "upload.sqlite")
                remote = {
                    "artifact_identity": "a" * 64,
                    "nodes_count": prepared.nodes_count,
                    "edges_count": prepared.edges_count,
                    "sources_count": prepared.sources_count,
                    "fts_count": prepared.fts_count,
                    "quick_check": "ok",
                    "integrity_check": "ok",
                    "page_size": prepared.page_size,
                    "page_count": prepared.page_count,
                    "journal_mode": "wal",
                }
            client = FakeTursoClient(remote=remote)
            result = TursoMaterializer(client).materialize(
                state,
                group="repo-search",
                database="repo-search-annals-challenge-marton",
            )

            self.assertEqual(result.status, "CREATED")
            self.assertEqual(client.created, [
                ("repo-search-annals-challenge-marton", "repo-search", None)
            ])
            self.assertEqual(len(client.uploaded), 1)
            self.assertEqual(client.protected, [
                ("repo-search-annals-challenge-marton", True)
            ])
            self.assertNotIn("secret-not-for-output", repr(result))

    def test_existing_matching_database_is_reused_without_upload(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            db = root / "source.sqlite"
            _write_projection(db)
            state = _state(db)
            with tempfile.TemporaryDirectory() as stage:
                prepared = prepare_upload_copy(db, Path(stage) / "upload.sqlite")
                remote = {
                    "artifact_identity": state.artifact_identity,
                    "nodes_count": prepared.nodes_count,
                    "edges_count": prepared.edges_count,
                    "sources_count": prepared.sources_count,
                    "fts_count": prepared.fts_count,
                    "quick_check": "ok",
                    "integrity_check": "ok",
                    "page_size": prepared.page_size,
                    "page_count": prepared.page_count,
                    "journal_mode": "wal",
                }
            client = FakeTursoClient(
                existing={"hostname": "existing.example.invalid"},
                remote=remote,
            )
            result = TursoMaterializer(client).materialize(
                state,
                group="repo-search",
                database="repo-search-annals-challenge-marton",
            )

            self.assertEqual(result.status, "REUSED")
            self.assertEqual(client.created, [])
            self.assertEqual(client.uploaded, [])
            self.assertEqual(client.protected, [
                ("repo-search-annals-challenge-marton", True)
            ])

    def test_existing_mismatched_database_fails_closed_without_upload_or_protection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            db = root / "source.sqlite"
            _write_projection(db)
            client = FakeTursoClient(
                existing={"hostname": "existing.example.invalid"},
                remote={
                    "artifact_identity": "f" * 64,
                    "nodes_count": 1,
                    "edges_count": 0,
                    "sources_count": 1,
                    "fts_count": 1,
                    "quick_check": "ok",
                    "integrity_check": "ok",
                    "page_size": 4096,
                    "page_count": 6,
                    "journal_mode": "wal",
                },
            )

            with self.assertRaises(RepoSearchError) as caught:
                TursoMaterializer(client).materialize(
                    _state(db),
                    group="repo-search",
                    database="repo-search-annals-challenge-marton",
                )

            self.assertEqual(caught.exception.code, "TURSO_DATABASE_IDENTITY_MISMATCH")
            self.assertEqual(client.uploaded, [])
            self.assertEqual(client.protected, [])


if __name__ == "__main__":
    unittest.main()
class TursoHttpClientTests(unittest.TestCase):
    def test_create_database_uses_database_upload_seed(self):
        from unittest import mock

        from theseus_repo_search.turso_materializer import TursoHttpClient

        client = TursoHttpClient(
            organization="example",
            platform_base_url="https://turso.int.exe.xyz",
        )
        with mock.patch.object(
            client,
            "_json_request",
            return_value={"database": {"hostname": "db.example.invalid"}},
        ) as request:
            result = client.create_upload_database(
                "repo-search-fixture",
                group="repo-search",
                size_limit="1gb",
            )

        self.assertEqual(result["hostname"], "db.example.invalid")
        request.assert_called_once_with(
            "POST",
            "https://turso.int.exe.xyz/v1/organizations/example/databases",
            payload={
                "name": "repo-search-fixture",
                "group": "repo-search",
                "seed": {"type": "database_upload"},
                "size_limit": "1gb",
            },
        )

    def test_upload_sqlite_streams_file_over_https(self):
        from unittest import mock

        from theseus_repo_search.turso_materializer import TursoHttpClient

        class Response:
            status = 200

            def read(self):
                return b""

        class Connection:
            def __init__(self):
                self.request = None
                self.headers = []
                self.chunks = []
                self.closed = False

            def putrequest(self, method, path):
                self.request = (method, path)

            def putheader(self, name, value):
                self.headers.append((name, value))

            def endheaders(self):
                pass

            def send(self, chunk):
                self.chunks.append(bytes(chunk))

            def getresponse(self):
                return Response()

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "upload.sqlite"
            payload = b"x" * (1024 * 1024 + 17)
            path.write_bytes(payload)
            connection = Connection()
            client = TursoHttpClient(organization="example")
            with mock.patch(
                "theseus_repo_search.turso_materializer.http.client.HTTPSConnection",
                return_value=connection,
            ) as https:
                client.upload_sqlite(
                    "fixture.example.turso.io",
                    "database-token",
                    path,
                )

        https.assert_called_once_with(
            "fixture.example.turso.io",
            443,
            timeout=30.0,
        )
        self.assertEqual(connection.request, ("POST", "/v1/upload"))
        self.assertIn(("Authorization", "Bearer database-token"), connection.headers)
        self.assertIn(("Content-Length", str(len(payload))), connection.headers)
        self.assertEqual(b"".join(connection.chunks), payload)
        self.assertGreater(len(connection.chunks), 1)
        self.assertTrue(connection.closed)


class TursoCliTests(unittest.TestCase):
    def test_materialize_turso_cli_uses_one_corpus_and_provider_proxy_without_token(self):
        import argparse
        from unittest import mock

        from theseus_repo_search import cli
        from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus
        from theseus_repo_search.turso_materializer import TursoMaterializationResult

        corpus = AcceptedCorpus(
            source_id="annals-challenge-marton",
            release_tag="tag/annals",
            package_asset="annals.tar.gz",
            receipt_asset="annals.json",
            source_descriptor="producer/sources/annals.json",
            artifact_identity="a" * 64,
            fingerprint_sha256="b" * 64,
        )
        catalog = AcceptedCatalog(
            schema="theseus.repo-search.accepted-catalog.v1",
            release_repository="owner/repo",
            corpora=(corpus,),
        )
        state = _state(Path("/cache/index.sqlite"))
        result = TursoMaterializationResult(
            status="REUSED",
            source_id=corpus.source_id,
            database="repo-search-annals-challenge-marton",
            artifact_identity=corpus.artifact_identity,
            projection_fingerprint="c" * 64,
            sqlite_bytes=454656,
        )
        args = argparse.Namespace(
            catalog=Path("/repo/producer/accepted-corpora.json"),
            cache_dir=Path("/cache"),
            source_id=corpus.source_id,
            organization="saymondzen",
            group="repo-search",
            database=None,
            size_limit=None,
            token_expiration="15m",
            platform_base_url="https://turso.int.exe.xyz",
            platform_token_env=None,
        )

        client = mock.Mock()
        materializer = mock.Mock()
        materializer.materialize.return_value = result
        with (
            mock.patch.object(cli, "load_accepted_catalog", return_value=catalog),
            mock.patch.object(cli, "ensure_local_corpus", return_value=state) as ensure,
            mock.patch.object(cli, "TursoHttpClient", return_value=client) as client_cls,
            mock.patch.object(cli, "TursoMaterializer", return_value=materializer),
            mock.patch.object(cli, "_emit") as emit,
        ):
            code = cli._cmd_materialize_turso(args)

        self.assertEqual(code, 0)
        ensure.assert_called_once_with(
            catalog,
            corpus.source_id,
            repository_root=Path("/repo"),
            cache_root=Path("/cache"),
        )
        client_cls.assert_called_once_with(
            organization="saymondzen",
            platform_base_url="https://turso.int.exe.xyz",
            platform_token=None,
        )
        materializer.materialize.assert_called_once_with(
            state,
            group="repo-search",
            database=None,
            size_limit=None,
            token_expiration="15m",
        )
        emit.assert_called_once()
        payload = emit.call_args.args[0]
        self.assertEqual(payload["status"], "REUSED")
        self.assertNotIn("token", payload)

class TursoCatalogSyncCliTests(unittest.TestCase):
    def _args(self):
        import argparse

        return argparse.Namespace(
            catalog=Path("/repo/producer/accepted-corpora.json"),
            cache_dir=Path("/cache"),
            organization="saymondzen",
            group="repo-search",
            size_limit=None,
            token_expiration="15m",
            platform_base_url="https://turso.int.exe.xyz",
            platform_token_env=None,
        )

    def _catalog(self):
        from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus

        corpora = tuple(
            AcceptedCorpus(
                source_id=source_id,
                release_tag=f"tag/{source_id}",
                package_asset=f"{source_id}.tar.gz",
                receipt_asset=f"{source_id}.json",
                source_descriptor=f"producer/sources/{source_id}.json",
                artifact_identity=identity * 64,
                fingerprint_sha256=fingerprint * 64,
            )
            for source_id, identity, fingerprint in (
                ("alpha", "a", "b"),
                ("beta", "c", "d"),
            )
        )
        return AcceptedCatalog(
            schema="theseus.repo-search.accepted-catalog.v1",
            release_repository="owner/repo",
            corpora=corpora,
        )

    def test_sync_turso_catalog_processes_every_accepted_corpus(self):
        from unittest import mock

        from theseus_repo_search import cli
        from theseus_repo_search.turso_materializer import TursoMaterializationResult

        catalog = self._catalog()
        states = {
            "alpha": _state(Path("/cache/alpha.sqlite"), source_id="alpha"),
            "beta": _state(Path("/cache/beta.sqlite"), source_id="beta"),
        }
        results = [
            TursoMaterializationResult(
                status="REUSED",
                source_id=source_id,
                database=f"repo-search-{source_id}-{state.artifact_identity[:8]}",
                artifact_identity=state.artifact_identity,
                projection_fingerprint=state.projection_fingerprint,
                sqlite_bytes=123,
            )
            for source_id, state in states.items()
        ]
        materializer = mock.Mock()
        materializer.materialize.side_effect = results

        with (
            mock.patch.object(cli, "load_accepted_catalog", return_value=catalog),
            mock.patch.object(
                cli,
                "ensure_local_corpus",
                side_effect=lambda _catalog, source_id, **_kwargs: states[source_id],
            ) as ensure,
            mock.patch.object(cli, "TursoHttpClient", return_value=mock.Mock()),
            mock.patch.object(cli, "TursoMaterializer", return_value=materializer),
            mock.patch.object(cli, "_emit") as emit,
        ):
            code = cli._cmd_sync_turso_catalog(self._args())

        self.assertEqual(code, 0)
        self.assertEqual([call.args[1] for call in ensure.call_args_list], ["alpha", "beta"])
        self.assertEqual(materializer.materialize.call_count, 2)
        payload = emit.call_args.args[0]
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["synced_corpora"], 2)
        self.assertEqual(payload["failed_corpora"], 0)
        self.assertEqual(payload["failures"], [])

    def test_sync_turso_catalog_reports_failure_and_continues(self):
        from unittest import mock

        from theseus_repo_search import cli
        from theseus_repo_search.turso_materializer import TursoMaterializationResult

        catalog = self._catalog()
        states = {
            "alpha": _state(Path("/cache/alpha.sqlite"), source_id="alpha"),
            "beta": _state(Path("/cache/beta.sqlite"), source_id="beta"),
        }
        materializer = mock.Mock()
        materializer.materialize.side_effect = [
            RepoSearchError("TURSO_REMOTE_PROJECTION_MISMATCH", "alpha bad"),
            TursoMaterializationResult(
                status="REUSED",
                source_id="beta",
                database="repo-search-beta",
                artifact_identity=states["beta"].artifact_identity,
                projection_fingerprint=states["beta"].projection_fingerprint,
                sqlite_bytes=123,
            ),
        ]

        with (
            mock.patch.object(cli, "load_accepted_catalog", return_value=catalog),
            mock.patch.object(
                cli,
                "ensure_local_corpus",
                side_effect=lambda _catalog, source_id, **_kwargs: states[source_id],
            ),
            mock.patch.object(cli, "TursoHttpClient", return_value=mock.Mock()),
            mock.patch.object(cli, "TursoMaterializer", return_value=materializer),
            mock.patch.object(cli, "_emit") as emit,
        ):
            code = cli._cmd_sync_turso_catalog(self._args())

        self.assertEqual(code, 2)
        self.assertEqual(materializer.materialize.call_count, 2)
        payload = emit.call_args.args[0]
        self.assertEqual(payload["status"], "DEGRADED")
        self.assertEqual(payload["synced_corpora"], 1)
        self.assertEqual(payload["failed_corpora"], 1)
        self.assertEqual(payload["failures"][0]["source_id"], "alpha")
        self.assertEqual(
            payload["failures"][0]["code"],
            "TURSO_REMOTE_PROJECTION_MISMATCH",
        )
