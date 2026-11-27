"""Deterministic split of a verified full SQLite projection.

The accepted artifact and its full logical fingerprint remain authoritative.
Recombine the search metadata/FTS and exported edges logically to verify the
pair; file hashes alone do not establish that the pair matches its parent.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import tempfile
from hashlib import file_digest
from pathlib import Path

from .json_contract import strict_json_loads
from .projection import _fingerprint_rows

PROFILE = "repo-search-split-v1"
FILES = ("search.sqlite", "nodes.csv", "edges.csv")
NODE_HEADER = ("node_key", "id")
EDGE_HEADER = ("source_key", "target_key", "relation", "evidence_grade", "producer")


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _file_receipts(root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in FILES:
        path = root / name
        with path.open("rb") as stream:
            result[name] = {"sha256": file_digest(stream, "sha256").hexdigest(), "bytes": path.stat().st_size}
    return result


def _edge_rows(path: Path, ids: dict[int, str]):
    previous: tuple[str, str, str, str] | None = None
    with path.open(encoding="utf-8", newline="") as stream:
        rows = csv.reader(stream)
        if tuple(next(rows, [])) != EDGE_HEADER:
            raise ValueError("invalid graph edge header")
        for row in rows:
            if len(row) != 5:
                raise ValueError("invalid graph edge row")
            s, t, relation, grade, producer = row
            if not s.isascii() or not s.isdecimal() or not t.isascii() or not t.isdecimal():
                raise ValueError("invalid graph node key")
            if int(s) not in ids or int(t) not in ids:
                raise ValueError("unresolved graph endpoint")
            source, target = ids[int(s)], ids[int(t)]
            key = (source, target, relation, producer)
            if previous is not None and key <= previous:
                raise ValueError("graph edges not strictly ordered and unique")
            previous = key
            yield (source, target, relation, grade, producer)


def _inspect(root: Path, expected_full: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_full):
        raise ValueError("invalid expected full projection fingerprint")
    conn = _read_only(root / "search.sqlite")
    try:
        metadata = dict(conn.execute("SELECT key,value FROM meta"))
        if metadata.pop("projection_profile", None) != PROFILE:
            raise ValueError("invalid search-only projection profile")
        identity = metadata.get("artifact_identity", "")
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("invalid artifact identity")
        if conn.execute("SELECT name FROM sqlite_master WHERE name='edges'").fetchone() is not None:
            raise ValueError("search-only projection contains edges")
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("invalid search projection")
        ids: dict[int, str] = {}
        with (root / "nodes.csv").open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream)
            if tuple(next(rows, [])) != NODE_HEADER:
                raise ValueError("invalid graph node header")
            for k, (row, expected) in enumerate(zip(rows, conn.execute("SELECT id FROM nodes ORDER BY id"), strict=True), 1):
                if row != [str(k), expected[0]]:
                    raise ValueError("graph node dictionary differs from search projection")
                ids[k] = expected[0]
        edges = 0

        def counted_edges():
            nonlocal edges
            for row in _edge_rows(root / "edges.csv", ids):
                edges += 1
                yield row

        actual = _fingerprint_rows(conn, counted_edges(), sorted(metadata.items()))
        if actual != expected_full:
            raise ValueError("split pair differs from accepted full projection fingerprint")
        search_fp = _fingerprint_rows(conn, (), sorted({**metadata, "projection_profile": PROFILE}.items()))
        sources = conn.execute("SELECT count(*) FROM sources").fetchone()[0]
    finally:
        conn.close()
    return {"profile": PROFILE, "artifact_identity": identity, "full_projection_fingerprint": expected_full,
            "search_projection_fingerprint": search_fp, "nodes": len(ids), "edges": edges,
            "sources": sources, "files": _file_receipts(root)}


def verify_split_projection(root: Path, expected_full: str) -> dict[str, object]:
    receipt = strict_json_loads((root / "receipt.json").read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("files") != _file_receipts(root):
        raise ValueError("split file integrity mismatch")
    actual = _inspect(root, expected_full)
    if actual != receipt:
        raise ValueError("split receipt mismatch")
    return actual


def build_split_projection(full: Path, out: Path, expected_full: str) -> dict[str, object]:
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".split-", dir=out.parent) as tmp:
        staged = Path(tmp) / "candidate"
        staged.mkdir()
        original = _read_only(full)
        conn = sqlite3.connect(staged / "search.sqlite")
        try:
            original.backup(conn)
            original.close()
            if conn.execute("SELECT value FROM meta WHERE key='projection_profile'").fetchone() is not None:
                raise ValueError("input must be a full projection")
            ids = {}
            with (staged / "nodes.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(NODE_HEADER)
                for k, (name,) in enumerate(conn.execute("SELECT id FROM nodes ORDER BY id"), 1):
                    ids[name] = k
                    writer.writerow((k, name))
            with (staged / "edges.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(EDGE_HEADER)
                for s, t, r, e, p in conn.execute("SELECT source_id,target_id,relation,evidence_grade,producer FROM edges ORDER BY source_id,target_id,relation,producer"):
                    if s not in ids or t not in ids:
                        raise ValueError("unresolved graph endpoint")
                    writer.writerow((ids[s], ids[t], r, e, p))
            conn.execute("DROP TABLE edges")
            conn.execute("INSERT INTO meta VALUES ('projection_profile', ?)", (PROFILE,))
            conn.commit()
            conn.execute("VACUUM")
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            original.close()
            conn.close()
        receipt = _inspect(staged, expected_full)
        (staged / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if out.exists():
            raise FileExistsError(out)
        staged.rename(out)
    return receipt
