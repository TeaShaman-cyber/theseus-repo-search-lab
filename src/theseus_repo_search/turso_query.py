"""Read-only hosted query route, using the same retrieval semantics as SQLite.

The workbench obtains short-lived database tokens. The future serving frontend
must use separately provisioned least-privilege credentials, not platform admin.
"""
from __future__ import annotations

from collections.abc import Iterator
from functools import partial

from .accepted_catalog import AcceptedCatalog, AcceptedCorpus, accepted_catalog_sha256
from .errors import RepoSearchError
from .multicorpus import MultiCorpusResult, federate_search
from .retrieval import SearchHit, SearchMode, search_connection
from .turso_materializer import (
    TursoHttpClient,
    _database_hostname,
    derive_database_name,
)


class ReadCursor:
    def __init__(self, rows: list[tuple[object, ...]]):
        self._rows = iter(rows)

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return self._rows

    def fetchone(self) -> tuple[object, ...] | None:
        return next(self._rows, None)

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class ReadConnection:
    def __init__(self, client: TursoHttpClient, hostname: str, token: str):
        self._client, self._hostname, self._token = client, hostname, token

    def execute(self, sql: str, parameters: tuple[object, ...] = (), /) -> ReadCursor:
        return ReadCursor(self._client.query_rows(self._hostname, self._token, sql, parameters))


def _search_one(item: AcceptedCorpus, client: TursoHttpClient, query: str, limit: int, mode: SearchMode) -> list[SearchHit]:
    name = derive_database_name(item.source_id, item.artifact_identity)
    db = client.get_database(name)
    if db is None:
        raise RepoSearchError("UNAVAILABLE_TURSO_CORPUS", "identity-bound database not found")
    token = client.mint_database_token(name, expiration="5m", authorization="read-only")
    conn = ReadConnection(client, _database_hostname(db), token)
    row = conn.execute("SELECT value FROM meta WHERE key='artifact_identity'").fetchone()
    if row != (item.artifact_identity,):
        raise RepoSearchError("TURSO_DATABASE_IDENTITY_MISMATCH", "database does not match accepted artifact")
    return search_connection(conn, query, limit=limit, mode=mode)


def search_turso_catalog(catalog: AcceptedCatalog, client: TursoHttpClient, *, query: str, limit: int = 20, mode: SearchMode = "discovery") -> MultiCorpusResult:
    if not 1 <= limit <= 50 or not 1 <= len(query) <= 512:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "query length 1..512 and limit 1..50 required")
    return federate_search(
        tuple((item.source_id, partial(_search_one, item, client, query, limit, mode)) for item in catalog.corpora),
        (), catalog_sha256=accepted_catalog_sha256(catalog), limit=limit,
    )
