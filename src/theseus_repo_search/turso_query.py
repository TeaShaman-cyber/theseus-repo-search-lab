"""Read-only hosted query route, using the same retrieval semantics as SQLite.

The workbench obtains short-lived database tokens. The future serving frontend
must use separately provisioned least-privilege credentials, not platform admin.
"""
from __future__ import annotations

from collections.abc import Iterator
from functools import partial

from .accepted_catalog import AcceptedCatalog, AcceptedCorpus, accepted_catalog_sha256
from .errors import RepoSearchError
from .graph import GraphResult, path_connection, traverse_connection
from .multicorpus import MultiCorpusResult, federate_search
from .retrieval import SearchHit, SearchMode, context_connection, search_connection
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


def connect_corpus(item: AcceptedCorpus, client: TursoHttpClient) -> ReadConnection:
    name = derive_database_name(item.source_id, item.artifact_identity)
    db = client.get_database(name)
    if db is None:
        raise RepoSearchError("UNAVAILABLE_TURSO_CORPUS", "identity-bound database not found")
    token = client.mint_database_token(name, expiration="5m", authorization="read-only")
    conn = ReadConnection(client, _database_hostname(db), token)
    row = conn.execute("SELECT value FROM meta WHERE key='artifact_identity'").fetchone()
    if row != (item.artifact_identity,):
        raise RepoSearchError("TURSO_DATABASE_IDENTITY_MISMATCH", "database does not match accepted artifact")
    return conn


def _search_one(item: AcceptedCorpus, client: TursoHttpClient, query: str, limit: int, mode: SearchMode) -> list[SearchHit]:
    return search_connection(connect_corpus(item, client), query, limit=limit, mode=mode)


def search_turso_catalog(catalog: AcceptedCatalog, client: TursoHttpClient, *, query: str, limit: int = 20, mode: SearchMode = "discovery", source_id: str | None = None) -> MultiCorpusResult:
    if not 1 <= limit <= 50 or not 1 <= len(query) <= 512:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "query length 1..512 and limit 1..50 required")
    selected = tuple(item for item in catalog.corpora if source_id is None or item.source_id == source_id)
    if not selected:
        raise RepoSearchError("BLOCKED_ACCEPTED_CATALOG", "source not in accepted catalog")
    return federate_search(
        tuple((item.source_id, partial(_search_one, item, client, query, limit, mode)) for item in selected),
        (), catalog_sha256=accepted_catalog_sha256(catalog), limit=limit,
    )


def query_graph(conn: ReadConnection, operation: str, name: str, *, target: str | None = None,
                depth: int = 1, max_edges: int = 200, token_budget: int = 4000) -> GraphResult | dict[str, object]:
    if not 1 <= len(name) <= 512 or not 0 <= depth <= 5 or not 1 <= max_edges <= 500:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "invalid graph query bounds")
    if operation == "path":
        if target is None or not 1 <= len(target) <= 512:
            raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "path requires target of length 1..512")
        return path_connection(conn, name, target, max_depth=depth, max_edges=max_edges)
    if operation not in {"deps", "rdeps", "context"} or target is not None:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "invalid graph operation or unexpected target")
    if operation == "context":
        if not 1 <= token_budget <= 4000:
            raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "context token budget must be 1..4000")
        graph = traverse_connection(conn, name, depth=depth, reverse=False, max_edges=min(max_edges, 50))
        return context_connection(conn, name, graph_result=graph, token_budget=token_budget)
    return traverse_connection(conn, name, depth=depth, reverse=operation == "rdeps", max_edges=max_edges)
