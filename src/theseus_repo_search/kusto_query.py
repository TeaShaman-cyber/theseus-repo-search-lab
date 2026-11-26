"""Bounded, identity-bound graph analytics over an accepted ADX projection."""
from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .accepted_catalog import AcceptedCorpus
from .errors import RepoSearchError


class KustoClient:
    def __init__(self, cluster: str, database: str, *, az: str = "/home/exedev/.local/bin/az-owner"):
        if not re.fullmatch(r"https://[a-z0-9-]+\.[a-z0-9-]+\.kusto\.windows\.net", cluster):
            raise RepoSearchError("BLOCKED_QUERY_PROFILE", "invalid ADX cluster URL")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", database) or not Path(az).is_absolute():
            raise RepoSearchError("BLOCKED_QUERY_PROFILE", "invalid ADX database or CLI wrapper")
        self.cluster, self.database = cluster, database
        try:
            self._token = subprocess.check_output(
                [az, "account", "get-access-token", "--resource", cluster,
                 "--query", "accessToken", "-o", "tsv", "--only-show-errors"],
                text=True, timeout=45,
            ).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise RepoSearchError("UNAVAILABLE_KUSTO_AUTH", "governed ADX token acquisition failed") from exc

    def query(self, kql: str) -> list[list[object]]:
        req = urllib.request.Request(
            self.cluster + "/v2/rest/query",
            data=json.dumps({"db": self.database, "csl": kql}).encode(),
            headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                frames = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RepoSearchError("UNAVAILABLE_KUSTO_QUERY", f"ADX query HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            raise RepoSearchError("UNAVAILABLE_KUSTO_QUERY", "ADX query transport/response failure") from exc
        if not isinstance(frames, list) or not all(isinstance(f, dict) for f in frames):
            raise RepoSearchError("UNAVAILABLE_KUSTO_QUERY", "invalid ADX response")
        completion = [f for f in frames if f.get("FrameType") == "DataSetCompletion"]
        if len(completion) != 1 or any(f.get("HasErrors") or f.get("OneApiErrors") or f.get("Cancelled") for f in frames):
            raise RepoSearchError("DEGRADED_KUSTO_QUERY", "ADX incomplete query")
        primary = [f for f in frames if f.get("TableKind") == "PrimaryResult"]
        if len(primary) != 1 or not isinstance(primary[0].get("Rows"), list):
            raise RepoSearchError("UNAVAILABLE_KUSTO_QUERY", "invalid ADX result")
        return primary[0]["Rows"]


def tables(item: AcceptedCorpus) -> tuple[str, str, str]:
    if not re.fullmatch(r"[0-9a-f]{64}", item.artifact_identity):
        raise RepoSearchError("BLOCKED_ACCEPTED_CATALOG", "invalid artifact identity")
    stem = "rs_g_" + item.artifact_identity[:16]
    return stem + "_nodes", stem + "_edges", stem + "_ready"


def reach(item: AcceptedCorpus, client: KustoClient, name: str, *, depth: int = 3,
          limit: int = 100, reverse: bool = False) -> dict[str, object]:
    if not 1 <= len(name) <= 512 or not 1 <= depth <= 3 or not 1 <= limit <= 500:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "reach: name 1..512, depth 1..3, limit 1..500")
    n, e, r = tables(item)
    ready = client.query(f"{r} | project artifact_identity,source_revision,dependency_boundary,node_count,edge_count | take 2")
    if (len(ready) != 1 or len(ready[0]) != 5 or ready[0][0] != item.artifact_identity
            or not all(isinstance(x, str) for x in ready[0][:3])
            or not all(type(x) is int and x >= 0 for x in ready[0][3:])):
        raise RepoSearchError("BLOCKED_KUSTO_IDENTITY", "ADX projection not ready for accepted artifact")
    _, revision, boundary, nc, ec = ready[0]
    counts = client.query(f"print node_count=toscalar({n} | count),edge_count=toscalar({e} | count)")
    if counts != [[nc, ec]]:
        raise RepoSearchError("BLOCKED_KUSTO_IDENTITY", "ADX projection row counts drifted")
    root = name if name.startswith("lean:") else "lean:" + name
    literal = json.dumps(root, ensure_ascii=False)
    matches = client.query(f"{n} | where id == {literal} | count")
    if matches not in ([[0]], [[1]]):
        raise RepoSearchError("BLOCKED_KUSTO_IDENTITY", "ADX node identity is not unique")
    payload: dict[str, object] = {
        "backend": "kusto", "source_id": item.source_id, "artifact_identity": item.artifact_identity,
        "source_revision": revision, "dependency_boundary": boundary,
        "root": root, "direction": "reverse" if reverse else "forward",
        "depth": depth, "projection_nodes": nc, "projection_edges": ec,
        "evidence": "reachability_over_indexed_typed_edges",
    }
    if matches == [[0]]:
        return {**payload, "status": "UNKNOWN", "found": False, "nodes": [], "complete_within_scope": False}
    pattern = f"(a)<-[e*1..{depth}]-(b)" if reverse else f"(a)-[e*1..{depth}]->(b)"
    rows = client.query(
        f"{e} | make-graph source_id --> target_id with_node_id=key "
        f"| graph-match {pattern} where a.key in ({literal}) and a.key != b.key "
         "project root=a.key,node=b.key,depth=array_length(map(e, source_id)) "
        f"| summarize depth=min(depth) by root,node | project node,depth | order by depth asc,node asc | take {limit+1}"
    )
    if (len(rows) > limit+1 or any(len(row) != 2 or not isinstance(row[0], str)
            or type(row[1]) is not int or not 1 <= row[1] <= depth for row in rows)
            or len({row[0] for row in rows}) != len(rows)):
        raise RepoSearchError("DEGRADED_KUSTO_QUERY", "invalid reachability result")
    complete = len(rows) <= limit
    return {**payload, "status": "FOUND" if complete else "DEGRADED", "found": True,
            "nodes": [{"id": row[0], "depth": row[1]} for row in rows[:limit]],
            "complete_within_scope": complete}
