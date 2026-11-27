"""Prepare a disposable native DuckDB graph next to a verified split projection."""
from __future__ import annotations

import csv
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TextIO

from .accepted_catalog import AcceptedCatalog
from .multicorpus import ensure_local_corpus
from .split_projection import build_split_projection, verify_split_projection

_LIMITS = "SET memory_limit='512MB'; SET threads=2; SET autoinstall_known_extensions=false; SET autoload_known_extensions=false;"


def _literal(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def _native(executable: str, db: Path, sql: str, *, read_only: bool = False, output: TextIO | None = None) -> None:
    # A standalone local process, no cloud credentials, startup file or attach.
    with tempfile.TemporaryDirectory(prefix="repo-search-duck-home-") as home:
        command = [executable, "-no-init", "-no-stdin", "-csv", "-noheader"]
        if read_only:
            command.append("-readonly")
        command.extend([str(db.resolve()), "-c", _LIMITS + sql])
        subprocess.run(
            command, check=True, text=True, stdout=output if output is not None else subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180,
            env={"PATH": os.environ.get("PATH", ""), "HOME": home},
        )


def _build_graph(root: Path, dest: Path, executable: str) -> None:
    nodes = _literal(root / "nodes.csv")
    edges = _literal(root / "edges.csv")
    _native(executable, dest, f"""
        CREATE TABLE nodes AS SELECT * FROM read_csv({nodes}, header=true,
            auto_detect=false, columns={{'node_key':'BIGINT','id':'VARCHAR'}});
        CREATE TABLE compact_edges AS SELECT * FROM read_csv({edges}, header=true,
            auto_detect=false, columns={{'source_key':'BIGINT','target_key':'BIGINT',
                'relation':'VARCHAR','evidence_grade':'VARCHAR','producer':'VARCHAR'}});
        CREATE VIEW edges AS SELECT a.id source_id,b.id target_id,e.relation,
            e.evidence_grade,e.producer FROM compact_edges e
            JOIN nodes a ON a.node_key=e.source_key
            JOIN nodes b ON b.node_key=e.target_key;
        CHECKPOINT;
    """)


def _verify_graph(root: Path, db: Path, executable: str) -> None:
    # Stream the actual serving view, not just the staging table/counts.
    # Temporary output is local and disposed even on a mismatched row.
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", newline="") as result:
        _native(executable, db, "SELECT node_key,id FROM nodes ORDER BY node_key;",
                read_only=True, output=result)
        result.seek(0)
        ids: dict[str, str] = {}
        with (root / "nodes.csv").open(encoding="utf-8", newline="") as f:
            rows = csv.reader(f)
            next(rows)
            for expected, actual in zip(rows, csv.reader(result), strict=True):
                if expected != actual:
                    raise ValueError("native graph node dictionary differs from verified split")
                ids[expected[0]] = expected[1]
        result.seek(0)
        result.truncate()
        _native(executable, db, "SELECT source_id,target_id,relation,evidence_grade,producer FROM edges ORDER BY source_id,target_id,relation,producer;",
                read_only=True, output=result)
        result.seek(0)
        with (root / "edges.csv").open(encoding="utf-8", newline="") as f:
            rows = csv.reader(f)
            next(rows)
            for (s, t, r, g, p), actual in zip(rows, csv.reader(result), strict=True):
                if [ids[s], ids[t], r, g, p] != actual:
                    raise ValueError("native graph edges differ from verified split")


def prepare_local_graph(full: Path, root: Path, expected_full: str, *,
                        executable: str = "duckdb") -> dict[str, object]:
    """One idempotent preparation operation; do not select or upload anything."""
    receipt = (verify_split_projection(root, expected_full) if root.exists()
               else build_split_projection(full, root, expected_full))
    db = root / "graph.duckdb"
    if db.exists():
        _verify_graph(root, db, executable)
        status = "REUSED"
    else:
        with tempfile.TemporaryDirectory(prefix=".graph-", dir=root) as tmp:
            staged = Path(tmp) / "graph.duckdb"
            _build_graph(root, staged, executable)
            _verify_graph(root, staged, executable)
            # Publish only a verified file. link is atomic and never overwrites
            # a concurrent winner; independently verify that winner if present.
            try:
                os.link(staged, db)
            except FileExistsError:
                _verify_graph(root, db, executable)
                status = "REUSED"
            else:
                status = "CREATED"
    return {"split": receipt, "graph_status": status, "graph_bytes": db.stat().st_size}


def prepare_catalog_graph(catalog: AcceptedCatalog, source_id: str, *,
                          repository_root: Path, cache_root: Path,
                          executable: str = "duckdb") -> dict[str, object]:
    """Use the existing accepted-release recovery path, not manual file transfer."""
    state = ensure_local_corpus(catalog, source_id, repository_root=repository_root,
                                cache_root=cache_root)
    root = cache_root / "split-v1" / state.artifact_identity / state.projection_fingerprint
    result = prepare_local_graph(state.db_path, root, state.projection_fingerprint,
                                 executable=executable)
    return {**result, "directory": str(root), "source_id": source_id}
