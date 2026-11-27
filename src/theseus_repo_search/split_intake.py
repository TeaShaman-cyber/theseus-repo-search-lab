"""Explicit candidate materialization. Selection and retirement stay separate."""
from __future__ import annotations

import csv
import io
import re
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from .accepted_catalog import AcceptedCatalog, AcceptedCorpus
from .errors import RepoSearchError
from .local_graph_cache import _literal, _native, prepare_catalog_graph
from .split_turso import materialize_split_search, split_projection_sha256
from .turso_materializer import TursoHttpClient


def motherduck_targets(item: AcceptedCorpus, database: str) -> tuple[str, str, str]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise ValueError("invalid MotherDuck database identifier")
    pin = item.split_projection_sha256
    if pin is None or not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ValueError("explicit split projection pin required")
    if not re.fullmatch(r"[0-9a-f]{64}", item.artifact_identity):
        raise ValueError("invalid artifact identity")
    key = sha256(f"{item.source_id}:{item.artifact_identity}:{pin}".encode()).hexdigest()[:32]
    prefix = f"{database}.main.rs_split_{key}_"
    return prefix + "nodes", prefix + "edges", prefix + "meta"


def _md_rows(launcher: Path, database: str, sql: str) -> list[list[str]]:
    # Use the configured provider boundary, never mint or persist credentials here.
    with tempfile.TemporaryDirectory(prefix="repo-search-md-sql-") as tmp:
        path = Path(tmp) / "query.sql"
        path.write_text(sql, encoding="utf-8")
        result = subprocess.run([sys.executable, str(launcher.resolve()), database, str(path)],
                                check=True, capture_output=True, text=True, timeout=300)
    return list(csv.reader(io.StringIO(result.stdout)))[1:]


def _export_graph(root: Path, output: Path, executable: str) -> None:
    _native(executable, root / "graph.duckdb", f"""
        COPY (SELECT * FROM nodes ORDER BY node_key) TO {_literal(output/'nodes.parquet')}
            (FORMAT PARQUET, COMPRESSION ZSTD);
        COPY (SELECT * FROM compact_edges ORDER BY source_key,target_key,relation,producer)
            TO {_literal(output/'edges.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD);
    """, read_only=True)


def materialize_motherduck(item: AcceptedCorpus, root: Path, receipt: dict[str, object], *,
                           launcher: Path, database: str, executable: str = "duckdb") -> dict[str, object]:
    if item.artifact_identity != receipt.get("artifact_identity") or item.split_projection_sha256 != split_projection_sha256(receipt):
        raise RepoSearchError("BLOCKED_PROJECTION_INTEGRITY", "graph candidate differs from catalog pin")
    nodes, edges, meta = motherduck_targets(item, database)
    names = ",".join("'" + name.rsplit(".", 1)[1] + "'" for name in (nodes, edges, meta))
    count = _md_rows(launcher, database, f"""
        SELECT count(*) FROM information_schema.tables
        WHERE table_catalog='{database}' AND table_schema='main' AND table_name IN ({names});
    """)
    if count not in ([["0"]], [["3"]]):
        raise RepoSearchError("BLOCKED_GRAPH_TARGET", "partial or ambiguous MotherDuck graph target")
    status = "REUSED" if count == [["3"]] else "CREATED"
    with tempfile.TemporaryDirectory(prefix="repo-search-md-graph-", dir=root) as tmp:
        staging = Path(tmp)
        _export_graph(root, staging, executable)
        pn = "read_parquet(" + _literal(staging / "nodes.parquet") + ")"
        pe = "read_parquet(" + _literal(staging / "edges.parquet") + ")"
        pin = item.split_projection_sha256
        identity = item.artifact_identity
        if status == "CREATED":
            _md_rows(launcher, database, f"""
                BEGIN TRANSACTION;
                CREATE TABLE {nodes} AS SELECT * FROM {pn};
                CREATE TABLE {edges} AS SELECT * FROM {pe};
                CREATE TABLE {meta} AS SELECT '{identity}' artifact_identity, '{pin}' split_projection_sha256;
                COMMIT;
            """)
        actual = _md_rows(launcher, database, f"""
            SELECT
              (SELECT count(*) FROM {nodes}) nodes,
              (SELECT count(*) FROM {edges}) edges,
              (SELECT count(*) FROM (SELECT * FROM {pn} EXCEPT ALL SELECT * FROM {nodes})) node_missing,
              (SELECT count(*) FROM (SELECT * FROM {nodes} EXCEPT ALL SELECT * FROM {pn})) node_extra,
              (SELECT count(*) FROM (SELECT * FROM {pe} EXCEPT ALL SELECT * FROM {edges})) edge_missing,
              (SELECT count(*) FROM (SELECT * FROM {edges} EXCEPT ALL SELECT * FROM {pe})) edge_extra,
              (SELECT CASE WHEN count(*)=1 AND count(*) FILTER (
                WHERE artifact_identity='{identity}' AND split_projection_sha256='{pin}')=1
                THEN 1 ELSE 0 END FROM {meta}) identity_ok;
        """)
        expected = [[str(receipt["nodes"]), str(receipt["edges"]), "0","0","0","0","1"]]
        if actual != expected:
            raise RepoSearchError("BLOCKED_GRAPH_INTEGRITY", "MotherDuck graph row parity failed")
    return {"status": status, "tables": [nodes, edges, meta], "nodes": receipt["nodes"], "edges": receipt["edges"]}


def materialize_corpus(catalog: AcceptedCatalog, source_id: str, *,
                       repository_root: Path, cache_root: Path, client: TursoHttpClient,
                       group: str, launcher: Path, database: str,
                       executable: str = "duckdb") -> dict[str, Any]:
    item = next((row for row in catalog.corpora if row.source_id == source_id), None)
    if item is None or item.split_projection_sha256 is None:
        raise RepoSearchError("BLOCKED_PROJECTION_PROFILE", "candidate catalog must pin a split projection")
    motherduck_targets(item, database)
    prepared = prepare_catalog_graph(catalog, source_id, repository_root=repository_root,
                                     cache_root=cache_root, executable=executable)
    receipt: Any = prepared["split"]
    if item.artifact_identity != receipt["artifact_identity"] or item.split_projection_sha256 != split_projection_sha256(receipt):
        raise RepoSearchError("BLOCKED_PROJECTION_INTEGRITY", "prepared split differs from catalog pin")
    root = Path(str(prepared["directory"]))
    search = materialize_split_search(item, root, receipt["full_projection_fingerprint"],
                                      client, group=group)
    graph = materialize_motherduck(item, root, receipt, launcher=launcher,
                                   database=database, executable=executable)
    return {"source_id": source_id, "artifact_identity": item.artifact_identity,
            "split_projection_sha256": item.split_projection_sha256,
            "local": prepared, "search": search, "graph": graph}
