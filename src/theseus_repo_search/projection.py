from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

from .artifact import artifact_identity, load_artifact
from .errors import RepoSearchError


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE nodes (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      kind TEXT NOT NULL,
      module TEXT NOT NULL,
      source_path TEXT,
      source_start_line INTEGER,
      source_end_line INTEGER,
      source_commit TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE edges (
      source_id TEXT NOT NULL,
      target_id TEXT NOT NULL,
      relation TEXT NOT NULL,
      evidence_grade TEXT NOT NULL,
      producer TEXT NOT NULL,
      PRIMARY KEY (source_id, target_id, relation, producer)
    )
    """,
    "CREATE INDEX edges_target_idx ON edges(target_id)",
    """
    CREATE TABLE sources (
      id TEXT PRIMARY KEY,
      source_commit TEXT NOT NULL,
      source_path TEXT NOT NULL,
      source_start_line INTEGER NOT NULL,
      source_end_line INTEGER NOT NULL,
      declaration_hint TEXT,
      text TEXT NOT NULL,
      content_sha256 TEXT NOT NULL
    )
    """,
)

_FTS_STATEMENT = """
CREATE VIRTUAL TABLE sources_fts USING fts5(
  declaration_hint,
  text,
  content='sources',
  content_rowid='rowid',
  tokenize='unicode61'
)
"""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _logical_payload(conn: sqlite3.Connection) -> dict[str, object]:
    return {
        "meta": conn.execute(
            "SELECT key, value FROM meta ORDER BY key"
        ).fetchall(),
        "nodes": conn.execute(
            "SELECT id, name, kind, module, source_path, source_start_line, "
            "source_end_line, source_commit FROM nodes ORDER BY id"
        ).fetchall(),
        "edges": conn.execute(
            "SELECT source_id, target_id, relation, evidence_grade, producer "
            "FROM edges ORDER BY source_id, target_id, relation, producer"
        ).fetchall(),
        "sources": conn.execute(
            "SELECT id, source_commit, source_path, source_start_line, "
            "source_end_line, declaration_hint, text, content_sha256 "
            "FROM sources ORDER BY id"
        ).fetchall(),
    }


def projection_fingerprint(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        payload = _logical_payload(conn)
    return sha256(_canonical_json(payload)).hexdigest()


def build_projection(artifact_dir: Path, db_path: Path) -> str:
    manifest, nodes, edges, sources = load_artifact(artifact_dir)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        with conn:
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            try:
                conn.execute(_FTS_STATEMENT)
            except sqlite3.OperationalError as exc:
                if "fts5" in str(exc).lower():
                    raise RepoSearchError(
                        "UNAVAILABLE_FTS5",
                        "SQLite FTS5 is unavailable in this runtime",
                    ) from exc
                raise

            meta_rows = (
                ("artifact_identity", artifact_identity(manifest)),
                ("source_commit", manifest.source_commit),
                (
                    "root_modules",
                    json.dumps(
                        list(manifest.scope.root_modules),
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                ),
                ("dependency_boundary", manifest.scope.dependency_boundary),
                ("producer.kind", manifest.producer.kind),
            )
            conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", meta_rows)

            conn.executemany(
                "INSERT INTO nodes(id, name, kind, module, source_path, "
                "source_start_line, source_end_line, source_commit) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        node.id,
                        node.name,
                        node.kind,
                        node.module,
                        node.source_path,
                        node.source_start_line,
                        node.source_end_line,
                        node.source_commit,
                    )
                    for node in nodes
                ],
            )
            conn.executemany(
                "INSERT INTO edges(source_id, target_id, relation, evidence_grade, producer) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        edge.source_id,
                        edge.target_id,
                        edge.relation,
                        edge.evidence_grade.value,
                        edge.producer,
                    )
                    for edge in edges
                ],
            )
            conn.executemany(
                "INSERT INTO sources(id, source_commit, source_path, source_start_line, "
                "source_end_line, declaration_hint, text, content_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        chunk.id,
                        chunk.source_commit,
                        chunk.source_path,
                        chunk.source_start_line,
                        chunk.source_end_line,
                        chunk.declaration_hint,
                        chunk.text,
                        chunk.content_sha256,
                    )
                    for chunk in sources
                ],
            )
            conn.execute("INSERT INTO sources_fts(sources_fts) VALUES('rebuild')")
    finally:
        conn.close()

    return projection_fingerprint(db_path)
