"""Local-first graph reads for split catalog entries; never attach MotherDuck."""
from __future__ import annotations

import csv
import json
import os
import re
import select
import shutil
import subprocess
import tempfile
import time
from contextlib import suppress
from hashlib import file_digest
from pathlib import Path
from typing import Self

from .accepted_catalog import AcceptedCatalog, AcceptedCorpus
from .errors import RepoSearchError
from .json_contract import strict_json_loads
from .local_graph_cache import _LIMITS, _native, prepare_catalog_graph
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


class NativeGraphSession:
    """One bounded standalone DuckDB process per graph operation."""
    def __init__(self, executable: str, graph: Path):
        self.home = tempfile.TemporaryDirectory(prefix="repo-search-duck-session-")
        self.buffer = b""
        self.deadline = time.monotonic() + 60
        try:
            self.process = subprocess.Popen(
                [executable, "-no-init", "-batch", "-bail", "-jsonlines", "-readonly",
                 str(graph.resolve()), "-cmd", _LIMITS],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", ""), "HOME": self.home.name},
            )
        except OSError:
            self.home.cleanup()
            raise

    def query(self, sql: str) -> list[tuple[object, ...]]:
        p = self.process
        assert p.stdin is not None
        assert p.stdout is not None
        try:
            p.stdin.write((sql + "; SELECT 1 AS repo_search_end;\n").encode("utf-8"))
            p.stdin.flush()
            rows: list[tuple[object, ...]] = []
            fields = ("source_id","target_id","relation","evidence_grade","producer")
            while True:
                if b"\n" not in self.buffer:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0 or not select.select([p.stdout], [], [], remaining)[0]:
                        raise TimeoutError("native graph session timed out")
                    data = os.read(p.stdout.fileno(), 65536)
                    if not data:
                        raise EOFError("native graph session ended before receipt")
                    self.buffer += data
                    continue
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                row = strict_json_loads(line.decode("utf-8"))
                if row == {"repo_search_end": 1}:
                    return rows
                if not isinstance(row, dict) or set(row) != set(fields) or any(not isinstance(row[k], str) for k in fields):
                    raise ValueError("invalid native graph row")
                rows.append(tuple(row[k] for k in fields))
                if len(rows) > 501:
                    raise ValueError("native graph result exceeds bounds")
        except (OSError, EOFError, ValueError) as exc:
            raise RepoSearchError("UNAVAILABLE_LOCAL_GRAPH", "native graph session failed") from exc

    def close(self) -> None:
        p = self.process
        try:
            if p.stdin is not None:
                with suppress(BrokenPipeError):
                    p.stdin.close()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        finally:
            if p.stdout is not None:
                p.stdout.close()
            self.home.cleanup()


class SplitGraphConnection:
    def __init__(self, search: QueryConnection, graph: Path, executable: str = "duckdb"):
        self.search, self.graph, self.executable = search, graph, executable
        self._managed = False
        self._session: NativeGraphSession | None = None

    def __enter__(self) -> Self:
        self._managed = True
        return self

    def __exit__(self, *_args: object) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
        self._managed = False

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
        if self._managed:
            if self._session is None:
                self._session = NativeGraphSession(self.executable, self.graph)
            return ReadCursor(self._session.query(rendered))
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", newline="") as output:
            _native(self.executable, self.graph, rendered + ";", read_only=True, output=output)
            output.seek(0)
            rows: list[tuple[object, ...]] = [tuple(row) for row in csv.reader(output)]
        return ReadCursor(rows)
