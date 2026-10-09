from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .artifact import artifact_identity, load_artifact
from .errors import RepoSearchError
from .model import ArtifactManifest, ArtifactManifestV2

SQLITE_PROJECTION_SCHEMA_VERSION = "repo-search-sqlite-v1"

_V1_SCHEMA_STATEMENTS = (
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

_V2_SCHEMA_STATEMENTS = (
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
      source_revision TEXT NOT NULL
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
      source_revision TEXT NOT NULL,
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


@dataclass(frozen=True)
class ProjectionProvenance:
    artifact_schema: str
    source_kind: str
    source_revision: str
    source_authority: dict[str, object] | None
    created_from_authoritative_source: bool
    revision_column: str

    @property
    def is_legacy_git(self) -> bool:
        return self.artifact_schema == "theseus.repo-index.v1"


def _parse_bool_meta(meta: dict[str, str], key: str) -> bool:
    if key not in meta:
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY", f"missing {key} projection metadata"
        )
    try:
        value = json.loads(meta[key])
    except json.JSONDecodeError as exc:
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY", f"invalid {key} projection metadata"
        ) from exc
    if not isinstance(value, bool):
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY", f"invalid {key} projection metadata"
        )
    return value


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def read_projection_provenance(conn: sqlite3.Connection) -> ProjectionProvenance:
    meta = {str(key): str(value) for key, value in conn.execute("SELECT key, value FROM meta")}
    node_columns = _table_columns(conn, "nodes")
    source_columns = _table_columns(conn, "sources")
    artifact_schema = meta.get("artifact_schema")
    if artifact_schema is None:
        source_revision = meta.get("source_commit")
        if source_revision is None:
            raise RepoSearchError(
                "BLOCKED_PROJECTION_INTEGRITY",
                "missing source_commit projection metadata",
            )
        if (
            "source_commit" not in node_columns
            or "source_commit" not in source_columns
            or "source_revision" in node_columns
            or "source_revision" in source_columns
        ):
            raise RepoSearchError(
                "BLOCKED_PROJECTION_INTEGRITY",
                "v1 projection revision columns do not match Git schema",
            )
        return ProjectionProvenance(
            artifact_schema="theseus.repo-index.v1",
            source_kind="git",
            source_revision=source_revision,
            source_authority=None,
            created_from_authoritative_source=_parse_bool_meta(
                meta, "created_from_authoritative_commit"
            ),
            revision_column="source_commit",
        )

    if artifact_schema != "theseus.repo-index.v2":
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            f"unsupported projection artifact schema: {artifact_schema}",
        )
    forbidden = {"source_commit", "created_from_authoritative_commit"} & set(meta)
    if forbidden:
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            f"v2 projection contains Git-named metadata: {sorted(forbidden)}",
        )
    if (
        "source_revision" not in node_columns
        or "source_revision" not in source_columns
        or "source_commit" in node_columns
        or "source_commit" in source_columns
    ):
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "v2 projection revision columns do not match generic schema",
        )
    if meta.get("source_kind") != "archive":
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "v2 projection source_kind must be archive",
        )
    source_revision = meta.get("source_revision")
    authority_json = meta.get("source_authority_json")
    if source_revision is None or authority_json is None:
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "v2 projection is missing generic source provenance metadata",
        )
    try:
        source_authority = json.loads(authority_json)
    except json.JSONDecodeError as exc:
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "invalid source_authority_json projection metadata",
        ) from exc
    expected_authority_keys = {"kind", "url", "sha256", "format", "subdir"}
    if (
        not isinstance(source_authority, dict)
        or set(source_authority) != expected_authority_keys
        or source_authority.get("kind") != "archive"
    ):
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "v2 projection source authority must be an exact archive object",
        )
    if source_authority.get("sha256") != source_revision:
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            "v2 projection source revision does not match archive authority",
        )
    return ProjectionProvenance(
        artifact_schema=artifact_schema,
        source_kind="archive",
        source_revision=source_revision,
        source_authority=source_authority,
        created_from_authoritative_source=_parse_bool_meta(
            meta, "created_from_authoritative_source"
        ),
        revision_column="source_revision",
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _logical_fingerprint_context(
    conn: sqlite3.Connection,
) -> tuple[str | None, str | None, str]:
    fts_schema_row = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'sources_fts'"
    ).fetchone()
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS temp.sources_fts_vocab "
        "USING fts5vocab(main, sources_fts, 'instance')"
    )
    orphan_posting = conn.execute(
        """
        SELECT DISTINCT v.doc
        FROM temp.sources_fts_vocab v
        LEFT JOIN sources s ON s.rowid=v.doc
        WHERE s.rowid IS NULL
        ORDER BY v.doc
        LIMIT 1
        """
    ).fetchone()
    orphan_docsize = conn.execute(
        """
        SELECT d.id
        FROM sources_fts_docsize d
        LEFT JOIN sources s ON s.rowid=d.id
        WHERE s.rowid IS NULL
        ORDER BY d.id
        LIMIT 1
        """
    ).fetchone()
    if orphan_posting is not None or orphan_docsize is not None:
        orphan_id = (
            orphan_posting[0]
            if orphan_posting is not None
            else orphan_docsize[0]
        )
        raise ValueError(f"orphan FTS document without source row: {orphan_id}")

    fts_averages_row = conn.execute(
        "SELECT hex(block) FROM sources_fts_data WHERE id=1"
    ).fetchone()
    provenance = read_projection_provenance(conn)
    return (
        None if fts_schema_row is None else str(fts_schema_row[0]),
        None if fts_averages_row is None else str(fts_averages_row[0]),
        provenance.revision_column,
    )


def _logical_payload(conn: sqlite3.Connection) -> dict[str, object]:
    fts_schema, fts_averages, revision_column = _logical_fingerprint_context(conn)
    fts_postings = conn.execute(
        """
        SELECT s.id,v.term,v.col,v.offset
        FROM temp.sources_fts_vocab v
        JOIN sources s ON s.rowid=v.doc
        ORDER BY s.id,v.term,v.col,v.offset
        """
    ).fetchall()
    fts_docsize = conn.execute(
        """
        SELECT s.id,hex(d.sz)
        FROM sources_fts_docsize d
        JOIN sources s ON s.rowid=d.id
        ORDER BY s.id
        """
    ).fetchall()
    return {
        "fts_schema": fts_schema,
        "fts_postings": fts_postings,
        "fts_docsize": fts_docsize,
        "fts_averages": fts_averages,
        "meta": conn.execute(
            "SELECT key, value FROM meta ORDER BY key"
        ).fetchall(),
        "nodes": conn.execute(
            "SELECT id, name, kind, module, source_path, source_start_line, "
            f"source_end_line, {revision_column} FROM nodes ORDER BY id"
        ).fetchall(),
        "edges": conn.execute(
            "SELECT source_id, target_id, relation, evidence_grade, producer "
            "FROM edges ORDER BY source_id, target_id, relation, producer"
        ).fetchall(),
        "sources": conn.execute(
            f"SELECT id, {revision_column}, source_path, source_start_line, "
            "source_end_line, declaration_hint, text, content_sha256 "
            "FROM sources ORDER BY id"
        ).fetchall(),
    }


def _hash_json_rows(digest, rows) -> None:
    digest.update(b"[")
    first = True
    for row in rows:
        if first:
            first = False
        else:
            digest.update(b",")
        digest.update(_canonical_json(row))
    digest.update(b"]")


def _hash_json_member(digest, key: str, *, first: bool) -> None:
    if not first:
        digest.update(b",")
    digest.update(_canonical_json(key))
    digest.update(b":")


def projection_fingerprint(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        fts_schema, fts_averages, revision_column = _logical_fingerprint_context(conn)
        digest = sha256()
        digest.update(b"{")

        _hash_json_member(digest, "edges", first=True)
        _hash_json_rows(
            digest,
            conn.execute(
                "SELECT source_id, target_id, relation, evidence_grade, producer "
                "FROM edges ORDER BY source_id, target_id, relation, producer"
            ),
        )

        _hash_json_member(digest, "fts_averages", first=False)
        digest.update(_canonical_json(fts_averages))

        _hash_json_member(digest, "fts_docsize", first=False)
        _hash_json_rows(
            digest,
            conn.execute(
                """
                SELECT s.id,hex(d.sz)
                FROM sources_fts_docsize d
                JOIN sources s ON s.rowid=d.id
                ORDER BY s.id
                """
            ),
        )

        _hash_json_member(digest, "fts_postings", first=False)
        _hash_json_rows(
            digest,
            conn.execute(
                """
                SELECT s.id,v.term,v.col,v.offset
                FROM temp.sources_fts_vocab v
                JOIN sources s ON s.rowid=v.doc
                ORDER BY s.id,v.term,v.col,v.offset
                """
            ),
        )

        _hash_json_member(digest, "fts_schema", first=False)
        digest.update(_canonical_json(fts_schema))

        _hash_json_member(digest, "meta", first=False)
        _hash_json_rows(
            digest,
            conn.execute("SELECT key, value FROM meta ORDER BY key"),
        )

        _hash_json_member(digest, "nodes", first=False)
        _hash_json_rows(
            digest,
            conn.execute(
                "SELECT id, name, kind, module, source_path, source_start_line, "
                f"source_end_line, {revision_column} FROM nodes ORDER BY id"
            ),
        )

        _hash_json_member(digest, "sources", first=False)
        _hash_json_rows(
            digest,
            conn.execute(
                f"SELECT id, {revision_column}, source_path, source_start_line, "
                "source_end_line, declaration_hint, text, content_sha256 "
                "FROM sources ORDER BY id"
            ),
        )
        digest.update(b"}")
        return digest.hexdigest()


def _populate_projection(artifact_dir: Path, db_path: Path) -> None:
    manifest, nodes, edges, sources = load_artifact(artifact_dir)
    meta_rows: tuple[tuple[str, str], ...]
    if isinstance(manifest, ArtifactManifest):
        schema_statements = _V1_SCHEMA_STATEMENTS
        revision_column = "source_commit"
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
            (
                "created_from_authoritative_commit",
                json.dumps(manifest.created_from_authoritative_commit),
            ),
        )
    elif isinstance(manifest, ArtifactManifestV2):
        schema_statements = _V2_SCHEMA_STATEMENTS
        revision_column = "source_revision"
        authority = manifest.source_authority
        authority_payload: dict[str, object] = {
            "kind": authority.kind,
            "url": authority.url,
            "sha256": authority.sha256,
            "format": authority.format,
            "subdir": authority.subdir,
        }
        meta_rows = (
            ("artifact_identity", artifact_identity(manifest)),
            ("artifact_schema", manifest.schema),
            ("source_kind", authority.kind),
            ("source_revision", manifest.source_revision),
            ("source_authority_json", _canonical_json(authority_payload).decode("utf-8")),
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
            (
                "created_from_authoritative_source",
                json.dumps(manifest.created_from_authoritative_source),
            ),
        )
    else:
        raise RepoSearchError(
            "BLOCKED_ARTIFACT_INTEGRITY", "unsupported artifact manifest type"
        )

    conn = sqlite3.connect(db_path)
    try:
        with conn:
            for statement in schema_statements:
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

            conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", meta_rows)
            conn.executemany(
                "INSERT INTO nodes(id, name, kind, module, source_path, "
                f"source_start_line, source_end_line, {revision_column}) "
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
                        node.source_revision,
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
                f"INSERT INTO sources(id, {revision_column}, source_path, source_start_line, "
                "source_end_line, declaration_hint, text, content_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        chunk.id,
                        chunk.source_revision,
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
            conn.execute("INSERT INTO sources_fts(sources_fts) VALUES('integrity-check')")
    finally:
        conn.close()


def build_projection(artifact_dir: Path, db_path: Path) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{db_path.name}.tmp-", suffix=".sqlite", dir=db_path.parent
    )
    os.close(fd)
    staged = Path(temp_name)
    try:
        _populate_projection(artifact_dir, staged)
        with sqlite3.connect(staged) as conn:
            quick = conn.execute("PRAGMA quick_check").fetchone()
            if quick is None or quick[0] != "ok":
                raise RepoSearchError(
                    "BLOCKED_PROJECTION_INTEGRITY",
                    f"SQLite quick_check failed: {None if quick is None else quick[0]}",
                )
        fingerprint = projection_fingerprint(staged)
        os.replace(staged, db_path)
        return fingerprint
    finally:
        if staged.exists():
            staged.unlink()
