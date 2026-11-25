from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .errors import RepoSearchError
from .projection import (
    ProjectionProvenance,
    QueryConnection,
    read_projection_provenance,
)

MAX_DEPTH = 5


@dataclass(frozen=True)
class GraphResult:
    query: str
    edges: tuple[dict[str, object], ...]
    scope_root_modules: tuple[str, ...]
    dependency_boundary: str
    complete_within_scope: bool
    source_kind: str
    source_revision: str
    source_authority: dict[str, object] | None
    created_from_authoritative_source: bool
    found: bool

    @property
    def created_from_authoritative_commit(self) -> bool:
        if self.source_kind != "git":
            raise AttributeError(
                "archive graph provenance has no created_from_authoritative_commit field"
            )
        return self.created_from_authoritative_source


def _validate_depth(depth: int) -> None:
    if depth < 0:
        raise RepoSearchError("UNKNOWN", "depth must be non-negative")
    if depth > MAX_DEPTH:
        raise RepoSearchError("UNKNOWN", "depth exceeds v1 maximum of 5")


def _metadata(
    conn: QueryConnection,
) -> tuple[tuple[str, ...], str, str, ProjectionProvenance]:
    meta = {str(key): str(value) for key, value in conn.execute("SELECT key, value FROM meta")}
    try:
        root_modules_raw = json.loads(meta["root_modules"])
        boundary = meta["dependency_boundary"]
        producer_kind = meta["producer.kind"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "missing or invalid graph projection metadata",
        ) from exc
    if not isinstance(root_modules_raw, list) or not all(
        isinstance(item, str) for item in root_modules_raw
    ):
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "invalid root_modules projection metadata",
        )
    return (
        tuple(root_modules_raw),
        boundary,
        producer_kind,
        read_projection_provenance(conn),
    )


def _require_elaborated_graph(producer_kind: str) -> None:
    if producer_kind == "lexical_only":
        raise RepoSearchError(
            "UNAVAILABLE_EVIDENCE_GRADE",
            "artifact does not contain elaborated dependency evidence",
        )


def _resolve_name(conn: QueryConnection, name: str) -> str:
    if name.startswith("lean:"):
        row = conn.execute("SELECT id FROM nodes WHERE id = ?", (name,)).fetchone()
        if row is None:
            raise RepoSearchError("UNKNOWN", f"declaration not found: {name}")
        return str(row[0])

    exact_id = f"lean:{name}"
    exact = conn.execute("SELECT id FROM nodes WHERE id = ?", (exact_id,)).fetchone()
    if exact is not None:
        return str(exact[0])

    escaped_name = name.replace("^", "^^").replace("%", "^%").replace("_", "^_")
    rows = conn.execute(
        "SELECT id FROM nodes WHERE name = ? OR id LIKE ? ESCAPE '^' ORDER BY id",
        (name, f"%.{escaped_name}"),
    ).fetchall()
    candidates = sorted({str(row[0]) for row in rows})
    if not candidates:
        raise RepoSearchError("UNKNOWN", f"declaration not found: {name}")
    if len(candidates) > 1:
        raise RepoSearchError(
            "UNKNOWN",
            f"ambiguous declaration {name}: {', '.join(candidates)}",
        )
    return candidates[0]


def _rows_for_frontier(
    conn: QueryConnection,
    frontier: list[str],
    *,
    reverse: bool,
    limit: int | None = None,
) -> list[tuple[str, str, str, str, str]]:
    if not frontier:
        return []
    placeholders = ",".join("?" for _ in frontier)
    column = "target_id" if reverse else "source_id"
    sql = ("SELECT source_id, target_id, relation, evidence_grade, producer "
           f"FROM edges WHERE {column} IN ({placeholders}) "
           "ORDER BY relation, target_id, source_id, producer")
    parameters: tuple[object, ...] = tuple(frontier)
    if limit is not None:
        sql += " LIMIT ?"
        parameters += (limit,)
    return conn.execute(sql, parameters).fetchall()


def _edge_record(
    row: tuple[str, str, str, str, str],
    *,
    depth: int,
) -> dict[str, object]:
    source_id, target_id, relation, evidence_grade, producer = row
    return {
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
        "evidence_grade": evidence_grade,
        "producer": producer,
        "depth": depth,
    }


def _traverse(db_path: Path, name: str, *, depth: int, reverse: bool) -> GraphResult:
    with sqlite3.connect(db_path) as conn:
        return traverse_connection(conn, name, depth=depth, reverse=reverse)


def traverse_connection(conn: QueryConnection, name: str, *, depth: int, reverse: bool,
                        max_edges: int | None = None) -> GraphResult:
    _validate_depth(depth)
    roots, boundary, producer_kind, provenance = _metadata(conn)
    _require_elaborated_graph(producer_kind)
    start = _resolve_name(conn, name)

    visited_nodes = {start}
    seen_edges: set[tuple[str, str, str, str]] = set()
    frontier = [start]
    result_edges: list[dict[str, object]] = []

    remaining = max_edges
    complete = True
    for current_depth in range(1, depth + 1):
        rows = _rows_for_frontier(conn, frontier, reverse=reverse,
                                  limit=None if remaining is None else remaining + 1)
        if remaining is not None:
            if len(rows) > remaining:
                complete = False
                rows = rows[:remaining]
            remaining -= len(rows)
        next_nodes: set[str] = set()
        for row in rows:
            source_id, target_id, relation, _, producer = row
            edge_key = (source_id, target_id, relation, producer)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                result_edges.append(_edge_record(row, depth=current_depth))

            neighbor = source_id if reverse else target_id
            if neighbor not in visited_nodes:
                visited_nodes.add(neighbor)
                next_nodes.add(neighbor)
        frontier = sorted(next_nodes)
        if not complete or not frontier:
            break

    kind = "reverse_dependencies" if reverse else "dependencies"
    return GraphResult(
        query=f"{kind}:{name}",
        edges=tuple(result_edges),
        scope_root_modules=roots,
        dependency_boundary=boundary,
        complete_within_scope=complete and producer_kind != "olean-export-deps-preview",
        source_kind=provenance.source_kind,
        source_revision=provenance.source_revision,
        source_authority=provenance.source_authority,
        created_from_authoritative_source=provenance.created_from_authoritative_source,
        found=True,
    )


def dependencies(db_path: Path, name: str, *, depth: int = 1) -> GraphResult:
    return _traverse(db_path, name, depth=depth, reverse=False)


def reverse_dependencies(db_path: Path, name: str, *, depth: int = 1) -> GraphResult:
    return _traverse(db_path, name, depth=depth, reverse=True)


def path(
    db_path: Path,
    source: str,
    target: str,
    *,
    max_depth: int = 5,
) -> GraphResult:
    with sqlite3.connect(db_path) as conn:
        return path_connection(conn, source, target, max_depth=max_depth)


def path_connection(conn: QueryConnection, source: str, target: str, *, max_depth: int = 5,
                    max_edges: int | None = None) -> GraphResult:
    _validate_depth(max_depth)
    roots, boundary, producer_kind, provenance = _metadata(conn)
    _require_elaborated_graph(producer_kind)
    source_id = _resolve_name(conn, source)
    target_id = _resolve_name(conn, target)

    complete = True
    remaining = max_edges
    if source_id == target_id:
        path_edges: list[dict[str, object]] = []
        path_found = True
    else:
        path_edges = []
        queue: deque[tuple[str, tuple[dict[str, object], ...], int]] = deque(
            [(source_id, (), 0)]
        )
        visited = {source_id}
        found_path: tuple[dict[str, object], ...] | None = None

        while queue and found_path is None:
            current, prior_edges, current_depth = queue.popleft()
            if current_depth >= max_depth:
                continue
            rows = _rows_for_frontier(conn, [current], reverse=False,
                                      limit=None if remaining is None else remaining + 1)
            if remaining is not None:
                if len(rows) > remaining:
                    complete = False
                    rows = rows[:remaining]
                remaining -= len(rows)
            for row in rows:
                neighbor = row[1]
                edge_record = _edge_record(row, depth=current_depth + 1)
                next_path = (*prior_edges, edge_record)
                if neighbor == target_id:
                    found_path = next_path
                    break
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, next_path, current_depth + 1))
            if not complete:
                break

        path_found = found_path is not None
        if found_path is not None:
            path_edges = list(found_path)

    return GraphResult(
        query=f"path:{source}->{target}",
        edges=tuple(path_edges),
        scope_root_modules=roots,
        dependency_boundary=boundary,
        complete_within_scope=complete and producer_kind != "olean-export-deps-preview",
        source_kind=provenance.source_kind,
        source_revision=provenance.source_revision,
        source_authority=provenance.source_authority,
        created_from_authoritative_source=provenance.created_from_authoritative_source,
        found=path_found,
    )
