import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.materialize_turso_catalog import (
    TursoHttpClient,
    database_name_for_source,
    materialize_catalog_states,
    materialize_state,
)
from tests.test_release_package import write_fixture
from theseus_repo_search.multicorpus import LocalCorpusState
from theseus_repo_search.projection import build_projection


class FakeTursoClient:
    def __init__(self, root: Path):
        self.root = root
        self.databases: dict[str, Path] = {}
        self.upload_calls = 0
        self.create_calls = 0
        self.protection_calls = 0

    def database_exists(self, name: str) -> bool:
        return name in self.databases

    def create_upload_database(self, name: str, *, group: str, size_limit: str) -> None:
        if name in self.databases:
            raise AssertionError("duplicate fake database")
        self.create_calls += 1
        self.databases[name] = self.root / f"{name}.sqlite.pending"

    def upload_database(self, name: str, sqlite_path: Path) -> None:
        self.upload_calls += 1
        target = self.root / f"{name}.sqlite"
        shutil.copy2(sqlite_path, target)
        self.databases[name] = target

    def query_rows(self, name: str, sql: str) -> list[tuple[object, ...]]:
        path = self.databases[name]
        if path.suffix == ".pending":
            raise RuntimeError("database upload is incomplete")
        with sqlite3.connect(path) as conn:
            return [tuple(row) for row in conn.execute(sql).fetchall()]

    def set_delete_protection(self, name: str, *, enabled: bool) -> None:
        if name not in self.databases:
            raise AssertionError("missing fake database")
        if not enabled:
            raise AssertionError("materializer must only enable protection")
        self.protection_calls += 1


def _state(root: Path, *, source_id: str = "fixture") -> LocalCorpusState:
    root.mkdir(parents=True, exist_ok=True)
    artifact = write_fixture(root)
    db = root / "index.sqlite"
    projection_fp = build_projection(artifact, db)
    with sqlite3.connect(db) as conn:
        identity = conn.execute(
            "SELECT value FROM meta WHERE key='artifact_identity'"
        ).fetchone()[0]
    return LocalCorpusState(
        source_id=source_id,
        release_tag="accepted-artifact/fixture/example",
        artifact_identity=identity,
        fingerprint_sha256="a" * 64,
        artifact_dir=artifact,
        db_path=db,
        projection_fingerprint=projection_fp,
    )


class TursoMaterializerTests(unittest.TestCase):
    def test_refuses_existing_database_with_different_identity_without_upload(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            state = _state(root / "local")
            remote = root / "remote"
            remote.mkdir()
            client = FakeTursoClient(remote)

            wrong = remote / "repo-search-fixture.sqlite"
            shutil.copy2(state.db_path, wrong)
            with sqlite3.connect(wrong) as conn:
                conn.execute(
                    "UPDATE meta SET value=? WHERE key='artifact_identity'",
                    ("f" * 64,),
                )
            client.databases["repo-search-fixture"] = wrong

            with self.assertRaisesRegex(
                ValueError, "remote artifact identity mismatch"
            ):
                materialize_state(
                    state,
                    client=client,
                    group="repo-search",
                    size_limit="1gb",
                )

            self.assertEqual(client.create_calls, 0)
            self.assertEqual(client.upload_calls, 0)
            self.assertEqual(client.protection_calls, 0)

    def test_uploads_exact_projection_via_wal_copy_and_verifies_readback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            state = _state(root / "local")
            remote = root / "remote"
            remote.mkdir()
            client = FakeTursoClient(remote)

            with sqlite3.connect(state.db_path) as conn:
                original_journal = conn.execute("PRAGMA journal_mode").fetchone()[0]

            receipt = materialize_state(
                state,
                client=client,
                group="repo-search",
                size_limit="1gb",
            )

            self.assertEqual(receipt["status"], "UPLOADED")
            self.assertEqual(receipt["artifact_identity"], state.artifact_identity)
            self.assertEqual(receipt["projection_fingerprint"], state.projection_fingerprint)
            self.assertTrue(receipt["delete_protection"])
            self.assertEqual(client.create_calls, 1)
            self.assertEqual(client.upload_calls, 1)
            self.assertEqual(client.protection_calls, 1)
            self.assertEqual(
                client.query_rows(
                    "repo-search-fixture",
                    "SELECT value FROM meta WHERE key='artifact_identity'",
                ),
                [(state.artifact_identity,)],
            )
            self.assertEqual(
                client.query_rows("repo-search-fixture", "PRAGMA quick_check"),
                [("ok",)],
            )
            self.assertEqual(
                client.query_rows("repo-search-fixture", "PRAGMA integrity_check"),
                [("ok",)],
            )
            self.assertEqual(
                client.query_rows("repo-search-fixture", "PRAGMA journal_mode"),
                [("wal",)],
            )
            with sqlite3.connect(state.db_path) as conn:
                self.assertEqual(
                    conn.execute("PRAGMA journal_mode").fetchone()[0],
                    original_journal,
                )

    def test_same_identity_existing_database_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            state = _state(root / "local")
            remote = root / "remote"
            remote.mkdir()
            client = FakeTursoClient(remote)

            first = materialize_state(
                state,
                client=client,
                group="repo-search",
                size_limit="1gb",
            )
            self.assertEqual(first["status"], "UPLOADED")
            upload_calls = client.upload_calls

            second = materialize_state(
                state,
                client=client,
                group="repo-search",
                size_limit="1gb",
            )
            self.assertEqual(second["status"], "ALREADY_PRESENT")
            self.assertEqual(client.upload_calls, upload_calls)
            self.assertEqual(client.create_calls, 1)
            self.assertEqual(client.protection_calls, 2)

    def test_catalog_unavailable_corpus_fails_before_any_remote_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            state = _state(root / "local")
            remote = root / "remote"
            remote.mkdir()
            client = FakeTursoClient(remote)

            class Unavailable:
                source_id = "broken"
                code = "RELEASE_EVIDENCE_INVALID"
                message = "bad release"

            with self.assertRaisesRegex(
                ValueError, "accepted catalog is not fully materializable"
            ):
                materialize_catalog_states(
                    (state,),
                    (Unavailable(),),
                    client=client,
                    group="repo-search",
                    size_limit="1gb",
                )

            self.assertEqual(client.create_calls, 0)
            self.assertEqual(client.upload_calls, 0)
            self.assertEqual(client.protection_calls, 0)

    def test_database_name_is_fail_closed_instead_of_silently_slugifying(self):
        self.assertEqual(
            database_name_for_source("openai-long-gaps"),
            "repo-search-openai-long-gaps",
        )
        with self.assertRaises(ValueError):
            database_name_for_source("UPPER CASE")
        with self.assertRaises(ValueError):
            database_name_for_source("x" * 60)

    def test_http_client_creates_upload_seed_database(self):
        requests = []

        class Response:
            def __init__(self, payload: bytes):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self.payload

        def opener(request, timeout):
            requests.append((request, timeout))
            return Response(
                b'{"database":{"Name":"repo-search-fixture","Hostname":"fixture.example"}}'
            )

        client = TursoHttpClient(
            platform_api_base="https://platform.example",
            organization="example-org",
            platform_token="secret-platform-token",
            platform_opener=opener,
        )
        client.create_upload_database(
            "repo-search-fixture",
            group="repo-search",
            size_limit="1gb",
        )

        self.assertEqual(len(requests), 1)
        request, timeout = requests[0]
        self.assertEqual(timeout, 30)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.full_url,
            "https://platform.example/v1/organizations/example-org/databases",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer secret-platform-token")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["name"], "repo-search-fixture")
        self.assertEqual(payload["group"], "repo-search")
        self.assertEqual(payload["size_limit"], "1gb")
        self.assertEqual(payload["seed"], {"type": "database_upload"})

    def test_http_client_streams_sqlite_upload_with_database_token(self):
        platform_requests = []

        class PlatformResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"jwt":"database-token"}'

        def platform_opener(request, timeout):
            platform_requests.append((request, timeout))
            return PlatformResponse()

        class UploadResponse:
            status = 200

            def read(self):
                return b"{}"

        class Connection:
            def __init__(self, hostname, timeout):
                self.hostname = hostname
                self.timeout = timeout
                self.method = None
                self.path = None
                self.headers = {}
                self.body = bytearray()
                self.closed = False

            def putrequest(self, method, path):
                self.method = method
                self.path = path

            def putheader(self, name, value):
                self.headers[name] = value

            def endheaders(self):
                pass

            def send(self, chunk):
                self.body.extend(chunk)

            def getresponse(self):
                return UploadResponse()

            def close(self):
                self.closed = True

        connections = []

        def connection_factory(hostname, timeout):
            conn = Connection(hostname, timeout)
            connections.append(conn)
            return conn

        client = TursoHttpClient(
            platform_api_base="https://platform.example",
            organization="example-org",
            platform_token="platform-token",
            platform_opener=platform_opener,
            connection_factory=connection_factory,
        )
        client._database_hosts["repo-search-fixture"] = "fixture.example"

        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "fixture.sqlite"
            db.write_bytes(b"SQLite payload")
            client.upload_database("repo-search-fixture", db)

        self.assertEqual(len(platform_requests), 1)
        token_request, _ = platform_requests[0]
        self.assertIn("/auth/tokens?", token_request.full_url)
        self.assertIn("authorization=full-access", token_request.full_url)

        self.assertEqual(len(connections), 1)
        conn = connections[0]
        self.assertEqual(conn.hostname, "fixture.example")
        self.assertEqual(conn.method, "POST")
        self.assertEqual(conn.path, "/v1/upload")
        self.assertEqual(conn.headers["Authorization"], "Bearer database-token")
        self.assertEqual(conn.headers["Content-Length"], str(len(b"SQLite payload")))
        self.assertEqual(bytes(conn.body), b"SQLite payload")
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main()
