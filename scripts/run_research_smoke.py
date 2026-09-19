from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.graph import dependencies, reverse_dependencies
from theseus_repo_search.retrieval import search

SCENARIO_SCHEMA = "theseus.repo-search-research-smoke.v1"
RECEIPT_SCHEMA = "theseus.repo-search-research-smoke-receipt.v1"
ALLOWED_KINDS = {"search", "deps", "rdeps", "declared_boundary"}


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_str_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    return [_require_str(item, field) for item in value]


def load_scenario(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCENARIO_SCHEMA:
        raise ValueError(f"unsupported scenario schema: {data.get('schema')}")
    _require_str(data.get("scenario_id"), "scenario_id")
    if not isinstance(data.get("version"), int) or data["version"] < 0:
        raise ValueError("version must be a non-negative integer")
    _require_str_list(data.get("research_refs"), "research_refs")
    _require_str(data.get("source_repo"), "source_repo")
    _require_str_list(data.get("evidence_classes"), "evidence_classes")
    probes = data.get("probes")
    if not isinstance(probes, list) or not probes:
        raise ValueError("probes must be a non-empty list")
    seen: set[str] = set()
    for probe in probes:
        if not isinstance(probe, dict):
            raise ValueError("probe must be an object")
        probe_id = _require_str(probe.get("id"), "probe.id")
        if probe_id in seen:
            raise ValueError(f"duplicate probe id: {probe_id}")
        seen.add(probe_id)
        kind = _require_str(probe.get("kind"), "probe.kind")
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"unsupported probe kind: {kind}")
        if not isinstance(probe.get("regression_guard", False), bool):
            raise ValueError("probe.regression_guard must be boolean")
        if kind == "search":
            _require_str(probe.get("query"), "probe.query")
        elif kind in {"deps", "rdeps"}:
            _require_str(probe.get("declaration"), "probe.declaration")
        elif kind == "declared_boundary":
            _require_str(probe.get("basis"), "probe.basis")
    return data


def _artifact_meta(artifact: Path, db: Path) -> tuple[dict[str, object], dict[str, str]]:
    manifest, *_ = load_artifact(artifact)
    identity = artifact_identity(manifest)
    with sqlite3.connect(db) as conn:
        meta = {str(k): str(v) for k, v in conn.execute("SELECT key, value FROM meta")}
    observed = meta.get("artifact_identity")
    if observed != identity:
        raise ValueError(
            f"projection artifact identity mismatch: expected={identity} observed={observed}"
        )
    return (
        {
            "identity": identity,
            "source_repo": manifest.source_repo,
            "source_commit": manifest.source_commit,
            "source_subdir": manifest.source_subdir,
            "root_modules": list(manifest.scope.root_modules),
            "dependency_boundary": manifest.scope.dependency_boundary,
        },
        meta,
    )


def _search_probe(db: Path, probe: dict[str, Any]) -> dict[str, object]:
    limit = int(probe.get("limit", 10))
    hits = search(db, str(probe["query"]), limit=limit)
    payload = [
        {
            "declaration_id": hit.declaration_id,
            "declaration_hint": hit.declaration_hint,
            "source_path": hit.source_path,
            "source_start_line": hit.source_start_line,
            "source_end_line": hit.source_end_line,
            "evidence_grade": hit.evidence_grade.value,
            "score": hit.score,
        }
        for hit in hits
    ]
    min_hits = int(probe.get("min_hits", 0))
    state = "FOUND_USEFUL_STRUCTURE" if hits else "NO_SIGNAL"
    regression = bool(probe.get("regression_guard", False)) and len(hits) < min_hits
    return {
        "state": state,
        "regression": regression,
        "query": probe["query"],
        "hit_count": len(hits),
        "hits": payload,
        "required_min_hits": min_hits,
    }


def _graph_probe(db: Path, probe: dict[str, Any], *, reverse: bool) -> dict[str, object]:
    depth = int(probe.get("depth", 1))
    declaration = str(probe["declaration"])
    try:
        result = (
            reverse_dependencies(db, declaration, depth=depth)
            if reverse
            else dependencies(db, declaration, depth=depth)
        )
    except RepoSearchError as exc:
        state = (
            "UNKNOWN_WITHIN_CURRENT_CONTRACT"
            if exc.code == "UNKNOWN"
            else "DEGRADED"
        )
        return {
            "state": state,
            "regression": bool(probe.get("regression_guard", False)),
            "declaration": declaration,
            "error": {"code": exc.code, "message": str(exc)},
            "edges": [],
        }

    edges = list(result.edges)
    required_ids = set(str(item) for item in probe.get("required_ids", []))
    observed_ids = {
        str(edge["source_id"] if reverse else edge["target_id"]) for edge in edges
    }
    missing = sorted(required_ids - observed_ids)
    min_edges = int(probe.get("min_edges", 0))
    regression = bool(probe.get("regression_guard", False)) and (
        len(edges) < min_edges or bool(missing)
    )
    return {
        "state": "FOUND_USEFUL_STRUCTURE" if edges else "NO_SIGNAL",
        "regression": regression,
        "declaration": declaration,
        "edge_count": len(edges),
        "required_min_edges": min_edges,
        "required_ids": sorted(required_ids),
        "missing_required_ids": missing,
        "edges": edges,
        "complete_within_scope": result.complete_within_scope,
        "dependency_boundary": result.dependency_boundary,
    }


def _run_probe(db: Path, probe: dict[str, Any]) -> dict[str, object]:
    kind = str(probe["kind"])
    if kind == "search":
        result = _search_probe(db, probe)
    elif kind == "deps":
        result = _graph_probe(db, probe, reverse=False)
    elif kind == "rdeps":
        result = _graph_probe(db, probe, reverse=True)
    else:
        result = {
            "state": "CORPUS_BOUNDARY",
            "regression": False,
            "basis": probe["basis"],
        }
    return {
        "id": probe["id"],
        "kind": kind,
        "regression_guard": bool(probe.get("regression_guard", False)),
        **result,
    }


def _overall(states: set[str], regression: bool) -> str:
    if regression or "DEGRADED" in states:
        return "DEGRADED"
    if "FOUND_USEFUL_STRUCTURE" in states:
        return "FOUND_USEFUL_STRUCTURE"
    if "CORPUS_BOUNDARY" in states:
        return "CORPUS_BOUNDARY"
    if "NO_SIGNAL" in states:
        return "NO_SIGNAL"
    return "UNKNOWN_WITHIN_CURRENT_CONTRACT"


def run_scenario(
    *,
    db: Path,
    artifact: Path,
    scenario: dict[str, Any],
    tool_commit: str,
) -> dict[str, object]:
    artifact_meta, _ = _artifact_meta(artifact, db)
    expected_repo = str(scenario["source_repo"])
    if artifact_meta["source_repo"] != expected_repo:
        raise ValueError(
            f"scenario source_repo mismatch: expected={expected_repo} "
            f"observed={artifact_meta['source_repo']}"
        )

    probe_results = [_run_probe(db, probe) for probe in scenario["probes"]]
    states = {str(result["state"]) for result in probe_results}
    regression = any(bool(result["regression"]) for result in probe_results)
    return {
        "schema": RECEIPT_SCHEMA,
        "scenario": {
            "id": scenario["scenario_id"],
            "version": scenario["version"],
            "research_refs": scenario["research_refs"],
            "evidence_classes_sought": scenario["evidence_classes"],
        },
        "tool_commit": tool_commit,
        "artifact": artifact_meta,
        "probes": probe_results,
        "observed_states": sorted(states),
        "overall_disposition": _overall(states, regression),
        "regression_detected": regression,
        "scientific_authority": "NONE",
        "scientific_authority_note": (
            "Receipt records retrieval evidence and boundaries only; it does not "
            "establish mathematical correctness or research priority."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--tool-commit", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fail-on-regression", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipt = run_scenario(
        db=args.db,
        artifact=args.artifact,
        scenario=load_scenario(args.scenario),
        tool_commit=args.tool_commit,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 2 if args.fail_on_regression and receipt["regression_detected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
