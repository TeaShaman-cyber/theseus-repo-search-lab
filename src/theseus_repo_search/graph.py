from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .errors import RepoSearchError


MAX_DEPTH = 5


@dataclass(frozen=True)
class GraphResult:
    query: str
    edges: tuple[dict[str, object], ...]
    scope_root_modules: tuple[str, ...]
    dependency_boundary: str
    complete_within_scope: bool
    created_from_authoritative_commit: bool
    found: bool


def _validate_depth(depth: int) -> None:
    if depth > MAX_DEPTH:
        raise RepoSearchError("UNKNOWN", "depth exceeds v1 maximum of 5")


def _metadata(conn: sqlite3.Connection) -> tuple[tuple[str, ...], str, str, bool]:
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    root_modules = tuple(str(item) for item in json.loads(meta["root_modules"]))
    authoritative = json.loads(meta["created_from_authoritative_commit"])
    if not isinstance(authoritative, bool):
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "invalid created_from_authoritative_commit projection metadata",
        )
    return (
        root_modules,
        meta["dependency_boundary"],
        meta["producer.kind"],
        authoritative,
    )


def _require_elaborated_graph(producer_kind: str) -> None:
    if producer_kind == "lexical_only":
        raise RepoSearchError(
            "UNAVAILABLE_EVIDENCE_GRADE",
            "artifact does not contain elaborated dependency evidence",
        )


def _resolve_name(conn: sqlite3.Connection, name: str) -> str:
    if name.startswith("lean:"):
        row = conn.execute("SELECT id FROM nodes WHERE id = ?", (name,)).fetchone()
        if row is None:
            raise RepoSearchError("UNKNOWN", f"declaration not found: {name}")
        return str(row[0])

    exact_id = f"lean:{name}"
    rows = conn.execute(
        "SELECT id FROM nodes WHERE id = ? OR name = ? OR id LIKE ? ORDER BY id",
        (exact_id, name, f"%.{name}"),
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
    conn: sqlite3.Connection,
    frontier: list[str],
    *,
    reverse: bool,
) -> list[tuple[str, str, str, str, str]]:
    if not frontier:
        return []
    placeholders = ",".join("?" for _ in frontier)
    column = "target_id" if reverse else "source_id"
    return conn.execute(
        "SELECT source_id, target_id, relation, evidence_grade, producer "
        f"FROM edges WHERE {column} IN ({placeholders}) "
        "ORDER BY relation, target_id, source_id, producer",
        frontier,
    ).fetchall()


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
    _validate_depth(depth)
    with sqlite3.connect(db_path) as conn:
        roots, boundary, producer_kind, authoritative = _metadata(conn)
        _require_elaborated_graph(producer_kind)
        start = _resolve_name(conn, name)

        visited_nodes = {start}
        seen_edges: set[tuple[str, str, str, str]] = set()
        frontier = [start]
        result_edges: list[dict[str, object]] = []

        for current_depth in range(1, depth + 1):
            rows = _rows_for_frontier(conn, frontier, reverse=reverse)
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
            if not frontier:
                break

    kind = "reverse_dependencies" if reverse else "dependencies"
    return GraphResult(
        query=f"{kind}:{name}",
        edges=tuple(result_edges),
        scope_root_modules=roots,
        dependency_boundary=boundary,
        complete_within_scope=True,
        created_from_authoritative_commit=authoritative,
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
    _validate_depth(max_depth)
    with sqlite3.connect(db_path) as conn:
        roots, boundary, producer_kind, authoritative = _metadata(conn)
        _require_elaborated_graph(producer_kind)
        source_id = _resolve_name(conn, source)
        target_id = _resolve_name(conn, target)

        if source_id == target_id:
            path_edges: list[dict[str, object]] = []
            path_found = True
        else:
            path_edges = []
            queue = deque([(source_id, tuple(), 0)])
            visited = {source_id}
            found_path: tuple[dict[str, object], ...] | None = None

            while queue and found_path is None:
                current, prior_edges, current_depth = queue.popleft()
                if current_depth >= max_depth:
                    continue
                rows = _rows_for_frontier(conn, [current], reverse=False)
                for row in rows:
                    neighbor = row[1]
                    edge_record = _edge_record(row, depth=current_depth + 1)
                    next_path = prior_edges + (edge_record,)
                    if neighbor == target_id:
                        found_path = next_path
                        break
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, next_path, current_depth + 1))

            path_found = found_path is not None
            if found_path is not None:
                path_edges = list(found_path)

    return GraphResult(
        query=f"path:{source}->{target}",
        edges=tuple(path_edges),
        scope_root_modules=roots,
        dependency_boundary=boundary,
        complete_within_scope=True,
        created_from_authoritative_commit=authoritative,
        found=path_found,
    )
