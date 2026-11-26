"""Bounded, identity-bound graph analytics over an accepted ADX projection."""
from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from .accepted_catalog import AcceptedCatalog, AcceptedCorpus, accepted_catalog_sha256
from .errors import RepoSearchError
from .multicorpus import MultiCorpusHit


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
    return stem + "_nodes", stem + "_edges", stem + "_ready_v2"


READY_COLUMNS = "artifact_identity,source_revision,dependency_boundary,node_count,edge_count,root_modules,source_kind,source_authority,created_from_authoritative_source,producer_kind"


def reach(item: AcceptedCorpus, client: KustoClient, name: str, *, depth: int = 3,
          limit: int = 100, reverse: bool = False) -> dict[str, object]:
    if not 1 <= len(name) <= 512 or not 1 <= depth <= 3 or not 1 <= limit <= 500:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "reach: name 1..512, depth 1..3, limit 1..500")
    n, e, r = tables(item)
    ready = client.query(f"{r} | project {READY_COLUMNS} | take 2")
    if (len(ready) != 1 or len(ready[0]) != 10 or ready[0][0] != item.artifact_identity
            or not all(isinstance(x, str) for x in ready[0][:3])
            or not all(type(x) is int and x >= 0 for x in ready[0][3:5])):
        raise RepoSearchError("BLOCKED_KUSTO_IDENTITY", "ADX projection not ready for accepted artifact")
    _, revision, boundary, nc, ec, scope, kind, authority, authoritative, producer = ready[0]
    try:
        roots = json.loads(str(scope))
        source_authority = json.loads(str(authority))
    except ValueError as exc:
        raise RepoSearchError("BLOCKED_KUSTO_IDENTITY", "invalid graph scope/provenance") from exc
    if (not isinstance(roots, list) or not all(isinstance(x, str) for x in roots)
            or kind not in ("git", "archive") or type(authoritative) is not bool
            or not isinstance(producer, str) or not producer
            or (kind == "git" and source_authority is not None)
            or (kind == "archive" and not isinstance(source_authority, dict))):
        raise RepoSearchError("BLOCKED_KUSTO_IDENTITY", "invalid graph scope/provenance")
    if producer == "lexical_only":
        raise RepoSearchError("UNAVAILABLE_KUSTO_GRAPH", "corpus is lexical-only")
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
        "scope_root_modules": roots, "source_kind": kind, "source_authority": source_authority,
        "created_from_authoritative_source": authoritative, "producer_kind": producer,
        "root": root, "direction": "reverse" if reverse else "forward",
        "depth": depth, "projection_nodes": nc, "projection_edges": ec,
        "evidence": "reachability_over_indexed_typed_edges",
    }
    if matches == [[0]]:
        return {**payload, "status": "UNKNOWN", "found": False, "nodes": [], "complete_within_scope": False}
    if ec == 0:
        return {**payload, "status": "FOUND", "found": True, "nodes": [], "complete_within_scope": True}
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

def federated_reach(catalog: AcceptedCatalog, client: KustoClient, name: str, *,
                    depth: int = 3, limit: int = 100, reverse: bool = False) -> dict[str, object]:
    if not 1 <= len(name) <= 512 or not 1 <= depth <= 3 or not 1 <= limit <= 500:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "reach-all: invalid query bounds")
    results = []
    unavailable = []
    for item in catalog.corpora:
        try:
            result = reach(item, client, name, depth=depth, limit=limit, reverse=reverse)
            results.append({**result, "source_id": item.source_id, "artifact_identity": item.artifact_identity})
        except RepoSearchError as exc:
            unavailable.append({"source_id": item.source_id, "artifact_identity": item.artifact_identity,
                                "code": exc.code, "message": str(exc)})
    truncated = any(r["status"] == "DEGRADED" for r in results)
    found = any(r["found"] for r in results)
    status = ("UNAVAILABLE" if not results else "DEGRADED" if unavailable or truncated else
              "FOUND" if found else "UNKNOWN")
    return {"status": status, "backend": "kusto", "query": name,
            "catalog_sha256": accepted_catalog_sha256(catalog),
            "searched_corpora": len(results), "unavailable_corpora": len(unavailable),
            "unavailable": unavailable, "results": results,
            "complete_within_scope": not unavailable and not truncated and found,
            "result_limit_per_corpus": limit}

def expand_search_hits(catalog: AcceptedCatalog, client: KustoClient,
                       hits: Sequence[MultiCorpusHit], *, depth: int = 2,
                       limit: int = 30, reverse: bool = False) -> dict[str, object]:
    """Expand only distinct declaration occurrences selected by lexical Top-K."""
    if len(hits) > 10 or not 1 <= depth <= 3 or not 1 <= limit <= 50:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "graph search: at most 10 seeds, depth 1..3, limit 1..50")
    items = {item.source_id: item for item in catalog.corpora}
    seen: set[tuple[str, str | None, str]] = set()
    graphs = []
    unavailable = []
    for hit in hits:
        item = items[hit.source_id]
        root = hit.hit.declaration_id
        key = (hit.source_id, root, hit.candidate_id if root is None else "")
        if key in seen:
            continue
        seen.add(key)
        origin = {"source_id": item.source_id, "artifact_identity": item.artifact_identity,
                  "candidate_id": hit.candidate_id, "root": root}
        try:
            if root is None:
                raise RepoSearchError("NO_DECLARATION_ID", "lexical source hit has no graph node")
            graph = reach(item, client, root, depth=depth, limit=limit, reverse=reverse)
            if graph["source_revision"] != hit.hit.source_revision or not graph["found"]:
                raise RepoSearchError("BLOCKED_KUSTO_IDENTITY", "search seed does not match graph revision/node")
            graphs.append({**graph, **origin})
        except RepoSearchError as exc:
            unavailable.append({**origin, "code": exc.code, "message": str(exc)})
    status = ("UNKNOWN" if not hits else "UNAVAILABLE" if not graphs else
              "DEGRADED" if unavailable or any(g["status"] == "DEGRADED" for g in graphs) else "FOUND")
    return {"graph_status": status, "graph_seeds": len(seen), "graphs": graphs,
            "graph_unavailable": unavailable, "graph_limit_per_seed": limit,
            "graph_coverage": "selected_lexical_declaration_seeds"}
