"""Local-first graph reads for split catalog entries; never attach MotherDuck."""
from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
from hashlib import file_digest
from pathlib import Path

from .accepted_catalog import AcceptedCatalog, AcceptedCorpus
from .errors import RepoSearchError
from .json_contract import strict_json_loads
from .local_graph_cache import _native, prepare_catalog_graph
from .projection import QueryConnection, QueryCursor
from .split_turso import split_projection_sha256
from .turso_query import ReadCursor

_FRONTIER = re.compile(
    r"SELECT source_id, target_id, relation, evidence_grade, producer "
    r"FROM edges WHERE (?:source_id|target_id) IN \(\?(?:,\?)*\) "
    r"ORDER BY relation, target_id, source_id, producer LIMIT \?"
)


def _digest(path: Path) -> str:
    with path.open("rb") as f:
        return file_digest(f, "sha256").hexdigest()


def _check_cache(root: Path, item: AcceptedCorpus) -> Path:
    try:
        r = strict_json_loads((root / "receipt.json").read_text())
        db = root / "graph.duckdb"
        if not isinstance(r, dict):
            raise TypeError("invalid local graph receipt")
        if (r["artifact_identity"] != item.artifact_identity
                or r["split_projection_sha256"] != item.split_projection_sha256
                or r["graph_sha256"] != _digest(db)):
            raise ValueError("local serving graph identity or bytes changed")
        return db
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RepoSearchError("BLOCKED_LOCAL_GRAPH_INTEGRITY", "local serving graph failed integrity check") from exc


def _serving_root(item: AcceptedCorpus, cache_root: Path) -> Path:
    pin = item.split_projection_sha256
    if pin is None or not re.fullmatch(r"[0-9a-f]{64}", pin) or not re.fullmatch(r"[0-9a-f]{64}", item.artifact_identity):
        raise RepoSearchError("BLOCKED_PROJECTION_PROFILE", "valid split pin required")
    return cache_root / "graph-serving-v1" / item.artifact_identity / pin


def ensure_serving_graph(catalog: AcceptedCatalog, item: AcceptedCorpus, *,
                         repository_root: Path, cache_root: Path, executable: str = "duckdb") -> Path:
    root = _serving_root(item, cache_root)
    if root.exists():
        return _check_cache(root, item)
    prepared = prepare_catalog_graph(catalog, item.source_id, repository_root=repository_root,
                                     cache_root=cache_root, executable=executable)
    return publish_serving_graph(item, prepared, cache_root=cache_root)


def publish_serving_graph(item: AcceptedCorpus, prepared: dict[str, object], *, cache_root: Path) -> Path:
    """Publish graph bytes from a just-verified local preparation, without recovery."""
    root = _serving_root(item, cache_root)
    pin = item.split_projection_sha256
    receipt = prepared["split"]
    if not isinstance(receipt, dict) or receipt.get("artifact_identity") != item.artifact_identity or split_projection_sha256(receipt) != pin:
        raise RepoSearchError("BLOCKED_PROJECTION_INTEGRITY", "local graph differs from selected split")
    if root.exists():
        return _check_cache(root, item)
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".serving-", dir=root.parent) as tmp:
        staged = Path(tmp) / "candidate"
        staged.mkdir()
        db = staged / "graph.duckdb"
        shutil.copyfile(Path(str(prepared["directory"])) / "graph.duckdb", db)
        r = {"artifact_identity": item.artifact_identity, "split_projection_sha256": pin,
             "graph_sha256": _digest(db)}
        (staged / "receipt.json").write_text(json.dumps(r, sort_keys=True) + "\n")
        try:
            staged.rename(root)
        except OSError:
            if not root.exists():
                raise
    return _check_cache(root, item)


class SplitGraphConnection:
    def __init__(self, search: QueryConnection, graph: Path, executable: str = "duckdb"):
        self.search, self.graph, self.executable = search, graph, executable

    def execute(self, sql: str, parameters: tuple[object, ...] = (), /) -> QueryCursor:
        if not re.search(r"\bedges\b", sql, flags=re.IGNORECASE):
            return self.search.execute(sql, parameters)
        if not _FRONTIER.fullmatch(sql) or sql.count("?") != len(parameters):
            raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "unsupported local graph statement")
        values = []
        for value in parameters[:-1]:
            if not isinstance(value, str):
                raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "invalid graph node")
            values.append("'" + value.replace("'", "''") + "'")
        limit = parameters[-1]
        if type(limit) is not int or not 1 <= limit <= 501:
            raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "invalid graph frontier limit")
        values.append(str(limit))
        parts = sql.split("?")
        rendered = "".join(a+b for a,b in zip(parts[:-1], values, strict=True)) + parts[-1]
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", newline="") as output:
            _native(self.executable, self.graph, rendered + ";", read_only=True, output=output)
            output.seek(0)
            rows: list[tuple[object, ...]] = [tuple(row) for row in csv.reader(output)]
        return ReadCursor(rows)
