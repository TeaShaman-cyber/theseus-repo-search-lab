"""Explicit, independently named Turso search-only candidate.

The full catalog artifact identity never changes. The split receipt pins both
projections; do not route graph operations to this search-only database.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from .accepted_catalog import AcceptedCorpus
from .errors import RepoSearchError
from .split_projection import PROFILE, verify_split_projection
from .turso_materializer import TursoHttpClient, _database_hostname


def split_projection_sha256(receipt: dict[str, object]) -> str:
    # Physical SQLite page layout and staging compression are not authority.
    # The parent logical fingerprint binds every source and edge row.
    payload = {key: receipt[key] for key in (
        "profile", "artifact_identity", "full_projection_fingerprint",
        "search_projection_fingerprint", "nodes", "edges", "sources",
    )}
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def split_database_name(item: AcceptedCorpus) -> str:
    pin = item.split_projection_sha256
    if pin is None or not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ValueError("explicit split receipt pin required")
    if not re.fullmatch(r"[0-9a-f]{64}", item.artifact_identity):
        raise ValueError("invalid artifact identity")
    slug = re.sub(r"[^a-z0-9]+", "-", item.source_id.lower()).strip("-")
    if not slug:
        raise ValueError("empty source slug")
    key = sha256(f"{item.source_id}:{item.artifact_identity}:{pin}".encode()).hexdigest()[:16]
    return f"repo-search-s-{slug[:21].rstrip('-')}-{key}"


def materialize_split_search(item: AcceptedCorpus, root: Path, expected_full: str,
                             client: TursoHttpClient, *, group: str) -> dict[str, object]:
    """Publish/reuse an exact candidate without overwriting any existing DB."""
    receipt = verify_split_projection(root, expected_full)
    pin = split_projection_sha256(receipt)
    if item.artifact_identity != receipt["artifact_identity"] or item.split_projection_sha256 != pin:
        raise RepoSearchError("BLOCKED_PROJECTION_INTEGRITY", "split candidate differs from catalog pin")
    name = split_database_name(item)
    with tempfile.TemporaryDirectory(prefix="repo-search-split-upload-") as tmp:
        upload = Path(tmp) / "search.sqlite"
        shutil.copyfile(root / "search.sqlite", upload)
        with closing(sqlite3.connect(upload)) as c:
            c.execute("INSERT INTO meta VALUES ('split_projection_sha256', ?)", (pin,))
            c.commit()
            if c.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise RepoSearchError("BLOCKED_PROJECTION_INTEGRITY", "split upload requires WAL")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if c.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RepoSearchError("BLOCKED_PROJECTION_INTEGRITY", "split upload integrity check failed")
            expected_geometry = (c.execute("PRAGMA page_size").fetchone(), c.execute("PRAGMA page_count").fetchone())
            if expected_geometry[0] != (4096,) or c.execute("PRAGMA auto_vacuum").fetchone() != (0,):
                raise RepoSearchError("TURSO_UPLOAD_INCOMPATIBLE", "split upload page geometry is incompatible")
        db = client.get_database(name)
        if db is None:
            db = client.create_upload_database(name, group=group)
            host = _database_hostname(db)
            token = client.mint_database_token(name, expiration="15m", authorization="full-access")
            client.upload_sqlite(host, token, upload)
            status = "CREATED"
        else:
            host = _database_hostname(db)
            status = "REUSED"
        token = client.mint_database_token(name, expiration="5m", authorization="read-only")
        def read(sql: str) -> list[tuple[object, ...]]:
            return client.query_rows(host, token, sql, verification=True)
        expected = {
            "SELECT key,value FROM meta WHERE key IN ('artifact_identity','projection_profile','split_projection_sha256') ORDER BY key":
                [("artifact_identity", item.artifact_identity), ("projection_profile", PROFILE), ("split_projection_sha256", pin)],
            "SELECT count(*) FROM nodes": [(receipt["nodes"],)],
            "SELECT count(*) FROM sources": [(receipt["sources"],)],
            "SELECT count(*) FROM sources_fts": [(receipt["sources"],)],
            "SELECT count(*) FROM sqlite_master WHERE name='edges'": [(0,)],
            "PRAGMA quick_check": [("ok",)],
            "PRAGMA page_size": [expected_geometry[0]],
        }
        for sql, rows in expected.items():
            if read(sql) != rows:
                raise RepoSearchError("TURSO_REMOTE_PROJECTION_MISMATCH", "split remote state differs from verified candidate")
        with closing(sqlite3.connect(upload)) as local:
            _verify_remote_content(local, client, host, token)
        client.set_delete_protection(name, True)
        return {"status": status, "database": name, "artifact_identity": item.artifact_identity,
                "split_projection_sha256": pin, "sqlite_bytes": upload.stat().st_size}


def _verify_remote_content(local: sqlite3.Connection, client: TursoHttpClient,
                           host: str, token: str) -> None:
    """Compare query-relevant schema, rows and FTS shadow contents in bounded pages.

    Database page allocation is not identity. FTS segment representation is
    checked conservatively against the upload; an independent FTS rebuild
    may require a new verification strategy, never a silent reuse.
    """
    def compare(sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        expected = local.execute(sql, params).fetchall()
        actual = client.query_rows(host, token, sql, params, verification=True)
        if actual != expected:
            raise RepoSearchError("TURSO_REMOTE_PROJECTION_MISMATCH", "split remote content or schema differs")
        return expected

    compare("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")
    compare("SELECT key,value FROM meta ORDER BY key")
    compare("SELECT k,v FROM sources_fts_config ORDER BY k")
    # Hash bounded ordered blocks remotely rather than transferring source text
    # and FTS segment BLOBs across the network. Include rowid because external
    # content FTS is keyed by it. The same SQL is evaluated against the local
    # verified upload with a standard SHA-256 implementation.
    local.create_function("sha256", 1, lambda text: sha256(text.encode("utf-8")).digest())
    for table, columns, key in (
        ("nodes", "rowid,*", "rowid"),
        ("sources", "rowid,*", "rowid"),
        ("sources_fts_data", "id,hex(block)", "id"),
        ("sources_fts_docsize", "id,hex(sz)", "id"),
    ):
        if columns == "rowid,*":
            columns = "rowid," + ",".join('"' + row[1].replace('"', '""') + '"'
                                          for row in local.execute(f"PRAGMA table_info({table})"))
        prefix = f"SELECT {key} k,hex(sha256(json_array({columns}))) h FROM {table}"
        suffix = f" ORDER BY {key} LIMIT 4096"
        where = ""
        params: tuple[object, ...] = ()
        while True:
            sql = "SELECT count(*),max(k),hex(sha256(coalesce(group_concat(h,''),''))) FROM (" + prefix + where + suffix + ")"
            count, last, _ = compare(sql, params)[0]
            if not count:
                break
            where = f" WHERE {key}>?"
            params = (last,)
    compare("SELECT count(*),hex(sha256(coalesce(group_concat(h,''),''))) FROM ("
            "SELECT hex(sha256(json_array(segid,hex(term),pgno))) h "
            "FROM sources_fts_idx ORDER BY segid,term)")
