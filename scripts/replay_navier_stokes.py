from __future__ import annotations

import argparse
import json
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity
from theseus_repo_search.graph import dependencies
from theseus_repo_search.replay_contract import (
    prepare_registered_replay,
    registered_replay_provenance,
    require_context_chunks,
)
from theseus_repo_search.retrieval import context, search

SOURCE_PATH = "NavierStokes/ComparatorSolution.lean"
THEOREMS = (
    (
        "NavierStokes.Comparator.navier_stokes_breakdown_R3",
        "lean:NavierStokes.ComparatorBridge.navier_stokes_breakdown_R3",
    ),
    (
        "NavierStokes.Comparator.navier_stokes_breakdown_periodic",
        "lean:NavierStokes.ComparatorBridge.navier_stokes_breakdown_periodic",
    ),
)


def _replay_theorem(db_path: Path, target: str, required_dependency: str) -> dict[str, object]:
    exact_hits = search(db_path, target, limit=1)
    if len(exact_hits) != 1 or exact_hits[0].source_path != SOURCE_PATH:
        raise AssertionError(f"{target} did not resolve to exact source provenance")

    graph = dependencies(db_path, target, depth=1)
    if not graph.edges:
        raise AssertionError(f"{target} has no in-scope elaborated dependency edges")
    if not all(str(edge["evidence_grade"]).startswith("ELABORATED_") for edge in graph.edges):
        raise AssertionError("graph replay returned non-elaborated dependency evidence")
    direct_targets = {str(edge["target_id"]) for edge in graph.edges}
    if required_dependency not in direct_targets:
        raise AssertionError(
            f"{target} missing required direct dependency: {required_dependency}"
        )

    ctx = context(db_path, target, depth=1, token_budget=4000)
    chunks = require_context_chunks(ctx)
    declaration_hint = target.rsplit(".", 1)[-1]
    if not any(
        chunk["declaration_hint"] == declaration_hint
        and chunk["source_path"] == SOURCE_PATH
        for chunk in chunks
    ):
        raise AssertionError(f"bounded context omitted {target} source")

    return {
        "target": target,
        "source_path": exact_hits[0].source_path,
        "source_start_line": exact_hits[0].source_start_line,
        "source_end_line": exact_hits[0].source_end_line,
        "edge_count": len(graph.edges),
        "edges": list(graph.edges),
        "context": {
            "estimated_tokens": ctx["estimated_tokens"],
            "chunks": chunks,
        },
    }


def run_replay(db_path: Path, artifact_path: Path, descriptor_path: Path) -> dict[str, object]:
    manifest, _ = prepare_registered_replay(artifact_path, db_path, descriptor_path)
    theorem_receipts = [
        _replay_theorem(db_path, target, required_dependency)
        for target, required_dependency in THEOREMS
    ]
    return {
        "status": "PASS",
        "artifact_identity": artifact_identity(manifest),
        "provenance": registered_replay_provenance(artifact_path, manifest),
        "theorems": theorem_receipts,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_replay(args.db, args.artifact, args.descriptor)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
