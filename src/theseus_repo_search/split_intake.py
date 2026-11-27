"""Explicit candidate materialization. Selection and retirement stay separate."""
from __future__ import annotations

import csv
import io
import json
import os
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
from .split_graph_query import publish_serving_graph
from .split_turso import (
    materialize_split_search,
    split_database_name,
    split_projection_sha256,
)
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


def _qualified(name: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in name.split("."))


def _md_rows(launcher: Path, database: str, sql: str, *, excluded_env: str | None = None) -> list[list[str]]:
    # Use the configured provider boundary, never mint or persist credentials here.
    with tempfile.TemporaryDirectory(prefix="repo-search-md-sql-") as tmp:
        path = Path(tmp) / "query.sql"
        path.write_text(sql, encoding="utf-8")
        allowed = {"PATH","HOME","LANG","LC_ALL","SSL_CERT_FILE","SSL_CERT_DIR","HTTPS_PROXY","HTTP_PROXY","NO_PROXY"}
        env = {k:v for k,v in os.environ.items() if k in allowed and k != excluded_env}
        result = subprocess.run([sys.executable, str(launcher.resolve()), database, str(path)],
                                check=True, capture_output=True, text=True, timeout=300, env=env)
    return list(csv.reader(io.StringIO(result.stdout)))[1:]


def _export_graph(root: Path, output: Path, executable: str) -> None:
    _native(executable, root / "graph.duckdb", f"""
        COPY (SELECT * FROM nodes ORDER BY node_key) TO {_literal(output/'nodes.parquet')}
            (FORMAT PARQUET, COMPRESSION ZSTD);
        COPY (SELECT * FROM compact_edges ORDER BY source_key,target_key,relation,producer)
            TO {_literal(output/'edges.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD);
    """, read_only=True)


def materialize_motherduck(item: AcceptedCorpus, root: Path, receipt: dict[str, object], *,
                           launcher: Path, database: str, executable: str = "duckdb",
                           excluded_env: str | None = None) -> dict[str, object]:
    if item.artifact_identity != receipt.get("artifact_identity") or item.split_projection_sha256 != split_projection_sha256(receipt):
        raise RepoSearchError("BLOCKED_PROJECTION_INTEGRITY", "graph candidate differs from catalog pin")
    targets = motherduck_targets(item, database)
    nodes, edges, meta = map(_qualified, targets)
    names = ",".join("'" + name.rsplit(".", 1)[1] + "'" for name in targets)
    def run(sql: str) -> list[list[str]]:
        return _md_rows(launcher, database, sql, excluded_env=excluded_env)
    count = run(f"""
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
            run(f"""
                BEGIN TRANSACTION;
                CREATE TABLE {nodes} AS SELECT * FROM {pn};
                CREATE TABLE {edges} AS SELECT * FROM {pe};
                CREATE TABLE {meta} AS SELECT '{identity}' artifact_identity, '{pin}' split_projection_sha256;
                COMMIT;
            """)
        schema = run(f"""
            SELECT table_name,column_name,data_type,ordinal_position FROM information_schema.columns
            WHERE table_catalog='{database}' AND table_schema='main' AND table_name IN ({names})
            ORDER BY table_name,ordinal_position;
        """)
        expected_schema: list[list[str]] = []
        for table, columns in zip(targets, (
            (("node_key","BIGINT"),("id","VARCHAR")),
            (("source_key","BIGINT"),("target_key","BIGINT"),("relation","VARCHAR"),
             ("evidence_grade","VARCHAR"),("producer","VARCHAR")),
            (("artifact_identity","VARCHAR"),("split_projection_sha256","VARCHAR")),
        ), strict=True):
            expected_schema.extend([table.rsplit(".",1)[1], name, kind, str(pos)]
                                   for pos,(name,kind) in enumerate(columns,1))
        expected_schema.sort(key=lambda row:(row[0],int(row[3])))
        if schema != expected_schema:
            raise RepoSearchError("BLOCKED_GRAPH_INTEGRITY", "MotherDuck graph schema differs")
        actual = run(f"""
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
    return {"status": status, "tables": list(targets), "nodes": receipt["nodes"], "edges": receipt["edges"]}


def materialize_corpus(catalog: AcceptedCatalog, source_id: str, *,
                       repository_root: Path, cache_root: Path, client: TursoHttpClient,
                       group: str, launcher: Path, database: str,
                       executable: str = "duckdb", platform_token_env: str | None = None) -> dict[str, Any]:
    item = next((row for row in catalog.corpora if row.source_id == source_id), None)
    if item is None or item.split_projection_sha256 is None:
        raise RepoSearchError("BLOCKED_PROJECTION_PROFILE", "candidate catalog must pin a split projection")
    motherduck_targets(item, database)
    prepared = prepare_catalog_graph(catalog, source_id, repository_root=repository_root,
                                     cache_root=cache_root, executable=executable)
    receipt: Any = prepared["split"]
    if item.artifact_identity != receipt["artifact_identity"] or item.split_projection_sha256 != split_projection_sha256(receipt):
        raise RepoSearchError("BLOCKED_PROJECTION_INTEGRITY", "prepared split differs from catalog pin")
    serving_graph = publish_serving_graph(item, prepared, cache_root=cache_root)
    root = Path(str(prepared["directory"]))
    # Persist the exact intent before touching either provider. Each invocation
    # has a separate receipt so an ambiguous failure remains recoverable.
    journal_dir = cache_root / "intake-receipts" / item.artifact_identity / item.split_projection_sha256
    journal_dir.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix="intake-", suffix=".json", dir=journal_dir)
    os.close(fd)
    journal = Path(filename)
    result: dict[str, Any] = {"source_id": source_id, "artifact_identity": item.artifact_identity,
            "split_projection_sha256": item.split_projection_sha256, "local": prepared,
            "serving_graph": str(serving_graph), "status": "PENDING",
            "targets": {"turso": split_database_name(item), "motherduck": list(motherduck_targets(item,database))},
            "phase":"TURSO", "receipt_path": str(journal)}
    def persist() -> None:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=journal_dir, delete=False) as f:
            json.dump(result, f, sort_keys=True, indent=2)
            f.write("\n")
            staged = Path(f.name)
        os.replace(staged, journal)
    persist()
    try:
        result["search"] = materialize_split_search(item, root, receipt["full_projection_fingerprint"],
                                                     client, group=group)
        result["phase"] = "MOTHERDUCK"
        persist()
        result["graph"] = materialize_motherduck(item, root, receipt, launcher=launcher,
                                                database=database, executable=executable,
                                                excluded_env=platform_token_env)
    except (RepoSearchError, OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        result["status"] = "RECONCILIATION_REQUIRED"
        result["error_code"] = exc.code if isinstance(exc,RepoSearchError) else type(exc).__name__
        persist()
        raise RepoSearchError("DEGRADED_SPLIT_INTAKE", f"partial intake; reconcile {journal}") from exc
    result["status"] = "READY"
    result["phase"] = "COMPLETE"
    persist()
    return result
