from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from .errors import RepoSearchError
from .graph import dependencies
from .model import EvidenceGrade
from .projection import ProjectionProvenance, read_projection_provenance


@dataclass(frozen=True)
class SearchHit:
    declaration_id: str | None
    declaration_hint: str | None
    source_kind: str
    source_revision: str
    source_authority: dict[str, object] | None
    source_path: str | None
    source_start_line: int | None
    source_end_line: int | None
    evidence_grade: EvidenceGrade
    score: float
    text: str | None
    created_from_authoritative_source: bool

    @property
    def source_commit(self) -> str:
        if self.source_kind != "git":
            raise AttributeError("archive search provenance has no source_commit field")
        return self.source_revision

    @property
    def created_from_authoritative_commit(self) -> bool:
        if self.source_kind != "git":
            raise AttributeError(
                "archive search provenance has no created_from_authoritative_commit field"
            )
        return self.created_from_authoritative_source


def _node_row_by_id(
    conn: sqlite3.Connection, node_id: str, *, revision_column: str
):
    return conn.execute(
        "SELECT id, name, source_path, source_start_line, source_end_line, "
        f"{revision_column}, module FROM nodes WHERE id = ?",
        (node_id,),
    ).fetchone()


def _unique_node_id(conn: sqlite3.Connection, query: str) -> str | None:
    if query.startswith("lean:"):
        row = conn.execute("SELECT id FROM nodes WHERE id = ?", (query,)).fetchone()
        return None if row is None else str(row[0])

    row = conn.execute("SELECT id FROM nodes WHERE id = ?", (f"lean:{query}",)).fetchone()
    if row is not None:
        return str(row[0])

    suffix_rows = conn.execute(
        "SELECT id FROM nodes WHERE id LIKE ? ORDER BY id",
        (f"%.{query}",),
    ).fetchall()
    suffix_ids = sorted({str(row[0]) for row in suffix_rows})
    if len(suffix_ids) == 1:
        return suffix_ids[0]

    name_rows = conn.execute(
        "SELECT id FROM nodes WHERE name = ? ORDER BY id",
        (query,),
    ).fetchall()
    name_ids = sorted({str(row[0]) for row in name_rows})
    if len(name_ids) == 1:
        return name_ids[0]
    return None


def _required_node_id(conn: sqlite3.Connection, query: str) -> str:
    node_id = _unique_node_id(conn, query)
    if node_id is not None:
        return node_id

    if query.startswith("lean:"):
        candidates: list[str] = []
    else:
        suffix_rows = conn.execute(
            "SELECT id FROM nodes WHERE id LIKE ? OR name = ? ORDER BY id",
            (f"%.{query}", query),
        ).fetchall()
        candidates = sorted({str(row[0]) for row in suffix_rows})
    if candidates:
        raise RepoSearchError(
            "UNKNOWN",
            f"ambiguous declaration {query}: {', '.join(candidates)}",
        )
    raise RepoSearchError("UNKNOWN", f"declaration not found: {query}")


def _source_row_for_node(
    conn: sqlite3.Connection, node_row, *, revision_column: str
):
    _, name, source_path, source_start_line, _source_end_line, _, module = node_row
    if source_path is not None:
        rows = conn.execute(
            f"SELECT id, {revision_column}, source_path, source_start_line, source_end_line, "
            "declaration_hint, text FROM sources WHERE source_path = ? "
            "ORDER BY source_start_line, id",
            (source_path,),
        ).fetchall()
        if source_start_line is not None:
            containing = [
                row
                for row in rows
                if row[3] <= source_start_line <= row[4]
            ]
            if len(containing) == 1:
                return containing[0]
        if len(rows) == 1:
            return rows[0]

    fallback_path = (
        source_path if source_path is not None else f"{str(module).replace('.', '/')}.lean"
    )
    rows = conn.execute(
        f"SELECT id, {revision_column}, source_path, source_start_line, source_end_line, "
        "declaration_hint, text FROM sources WHERE source_path = ? AND declaration_hint = ? "
        "ORDER BY source_start_line, id",
        (fallback_path, name),
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def _exact_hit(
    conn: sqlite3.Connection, query: str, *, provenance: ProjectionProvenance
) -> SearchHit | None:
    node_id = _unique_node_id(conn, query)
    if node_id is None:
        return None
    node_row = _node_row_by_id(
        conn, node_id, revision_column=provenance.revision_column
    )
    assert node_row is not None
    source_row = _source_row_for_node(
        conn, node_row, revision_column=provenance.revision_column
    )
    if source_row is None:
        return SearchHit(
            declaration_id=node_id,
            declaration_hint=str(node_row[1]),
            source_kind=provenance.source_kind,
            source_revision=str(node_row[5]),
            source_authority=provenance.source_authority,
            source_path=None if node_row[2] is None else str(node_row[2]),
            source_start_line=node_row[3],
            source_end_line=node_row[4],
            evidence_grade=EvidenceGrade.LEXICAL_HIT,
            score=0.0,
            text=None,
            created_from_authoritative_source=provenance.created_from_authoritative_source,
        )
    return SearchHit(
        declaration_id=node_id,
        declaration_hint=None if source_row[5] is None else str(source_row[5]),
        source_kind=provenance.source_kind,
        source_revision=str(source_row[1]),
        source_authority=provenance.source_authority,
        source_path=str(source_row[2]),
        source_start_line=int(source_row[3]),
        source_end_line=int(source_row[4]),
        evidence_grade=EvidenceGrade.LEXICAL_HIT,
        score=0.0,
        text=str(source_row[6]),
        created_from_authoritative_source=provenance.created_from_authoritative_source,
    )


def _declaration_id_for_hit(
    conn: sqlite3.Connection,
    hint: str | None,
    *,
    source_path: str,
    source_start_line: int,
    source_end_line: int,
) -> str | None:
    if hint is None:
        return None
    rows = conn.execute(
        "SELECT id, source_path, source_start_line, source_end_line "
        "FROM nodes WHERE name = ? ORDER BY id",
        (hint,),
    ).fetchall()
    if not rows:
        return None

    exact = [
        str(row[0])
        for row in rows
        if row[1] == source_path
        and row[2] == source_start_line
        and row[3] == source_end_line
    ]
    if len(exact) == 1:
        return exact[0]

    containing = [
        str(row[0])
        for row in rows
        if row[1] == source_path
        and row[2] is not None
        and source_start_line <= int(row[2]) <= source_end_line
    ]
    if len(containing) == 1:
        return containing[0]

    path_ids = sorted({str(row[0]) for row in rows if row[1] == source_path})
    if len(path_ids) == 1:
        return path_ids[0]

    ids = sorted({str(row[0]) for row in rows})
    return ids[0] if len(ids) == 1 else None


def search(db_path: Path, query: str, *, limit: int = 10) -> list[SearchHit]:
    if limit <= 0:
        return []
    with sqlite3.connect(db_path) as conn:
        provenance = read_projection_provenance(conn)
        exact = _exact_hit(conn, query, provenance=provenance)
        if exact is not None:
            return [exact]

        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            return []
        fts_query = " OR ".join(f'"{term}"' for term in terms)
        rows = conn.execute(
            f"SELECT s.id, s.{provenance.revision_column}, s.source_path, s.source_start_line, "
            "s.source_end_line, s.declaration_hint, s.text, bm25(sources_fts) AS rank "
            "FROM sources_fts JOIN sources s ON s.rowid = sources_fts.rowid "
            "WHERE sources_fts MATCH ? "
            "ORDER BY rank, s.source_path, s.source_start_line LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        return [
            SearchHit(
                declaration_id=_declaration_id_for_hit(
                    conn,
                    row[5],
                    source_path=str(row[2]),
                    source_start_line=int(row[3]),
                    source_end_line=int(row[4]),
                ),
                declaration_hint=None if row[5] is None else str(row[5]),
                source_kind=provenance.source_kind,
                source_revision=str(row[1]),
                source_authority=provenance.source_authority,
                source_path=str(row[2]),
                source_start_line=int(row[3]),
                source_end_line=int(row[4]),
                evidence_grade=EvidenceGrade.LEXICAL_HIT,
                score=float(row[7]),
                text=str(row[6]),
                created_from_authoritative_source=provenance.created_from_authoritative_source,
            )
            for row in rows
        ]


def _context_chunk(
    conn: sqlite3.Connection, node_id: str, distance: int, *, provenance: ProjectionProvenance
):
    node_row = _node_row_by_id(
        conn, node_id, revision_column=provenance.revision_column
    )
    if node_row is None:
        return None
    source_row = _source_row_for_node(
        conn, node_row, revision_column=provenance.revision_column
    )
    if source_row is None:
        return None
    return {
        "declaration_id": node_id,
        "declaration_hint": None if source_row[5] is None else str(source_row[5]),
        "source_kind": provenance.source_kind,
        "source_revision": str(source_row[1]),
        "source_authority": provenance.source_authority,
        "source_path": str(source_row[2]),
        "source_start_line": int(source_row[3]),
        "source_end_line": int(source_row[4]),
        "evidence_grade": EvidenceGrade.LEXICAL_HIT.value,
        "distance": distance,
        "text": str(source_row[6]),
    }


def context(
    db_path: Path,
    name: str,
    *,
    depth: int = 1,
    token_budget: int = 4000,
) -> dict[str, object]:
    graph_result = dependencies(db_path, name, depth=depth)
    with sqlite3.connect(db_path) as conn:
        provenance = read_projection_provenance(conn)
        target_id = _required_node_id(conn, name)
        distances: dict[str, int] = {target_id: 0}
        for edge in graph_result.edges:
            target = str(edge["target_id"])
            edge_depth_raw = edge["depth"]
            if isinstance(edge_depth_raw, bool) or not isinstance(edge_depth_raw, int):
                raise RepoSearchError(
                    "BLOCKED_PROJECTION_INTEGRITY",
                    "graph edge depth must be an integer",
                )
            edge_depth = edge_depth_raw
            distances[target] = min(distances.get(target, edge_depth), edge_depth)

        candidates = []
        for node_id, distance in distances.items():
            chunk = _context_chunk(conn, node_id, distance, provenance=provenance)
            if chunk is not None:
                candidates.append(chunk)

    candidates.sort(
        key=lambda item: (
            int(item["distance"]),
            str(item["source_path"]),
            int(item["source_start_line"]),
            str(item["declaration_id"]),
        )
    )

    selected: list[dict[str, object]] = []
    total_chars = 0
    for chunk in candidates:
        text = str(chunk["text"])
        candidate_chars = total_chars + len(text)
        if ceil(candidate_chars / 4) > token_budget:
            break
        selected.append(chunk)
        total_chars = candidate_chars

    if graph_result.source_kind == "git":
        for chunk in selected:
            chunk["source_commit"] = chunk.pop("source_revision")
            chunk.pop("source_kind", None)
            chunk.pop("source_authority", None)
    result: dict[str, object] = {
        "query": name,
        "target_id": target_id,
        "chunks": selected,
        "graph_edges": list(graph_result.edges),
        "estimated_tokens": ceil(total_chars / 4) if total_chars else 0,
        "token_estimate_method": "ceil(chars/4)",
        "scope_root_modules": list(graph_result.scope_root_modules),
        "dependency_boundary": graph_result.dependency_boundary,
        "complete_within_scope": graph_result.complete_within_scope,
    }
    if graph_result.source_kind == "git":
        result["created_from_authoritative_commit"] = (
            graph_result.created_from_authoritative_source
        )
    else:
        result.update(
            {
                "source_kind": graph_result.source_kind,
                "source_revision": graph_result.source_revision,
                "source_authority": graph_result.source_authority,
                "created_from_authoritative_source": (
                    graph_result.created_from_authoritative_source
                ),
            }
        )
    return result
