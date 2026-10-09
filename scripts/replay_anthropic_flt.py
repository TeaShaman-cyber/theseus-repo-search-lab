from __future__ import annotations

import argparse
import json
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity
from theseus_repo_search.graph import dependencies
from theseus_repo_search.replay_contract import (
    prepare_registered_replay,
    registered_replay_provenance,
)
from theseus_repo_search.retrieval import search

TARGET = "fermat_last_theorem"
EXPECTED_DIRECT_DEPENDENCY = "lean:FLT.fermatLastTheorem"


def run_replay(db_path: Path, artifact_path: Path, descriptor_path: Path) -> dict[str, object]:
    manifest, _source = prepare_registered_replay(artifact_path, db_path, descriptor_path)
    exact_hits = search(db_path, TARGET, limit=1)
    if len(exact_hits) != 1:
        raise AssertionError("complete FLT root theorem did not resolve exactly")
    hit = exact_hits[0]
    if hit.source_path != "Theorems/Thm_fermat_last_theorem.lean":
        raise AssertionError(f"unexpected FLT root source path: {hit.source_path}")

    graph = dependencies(db_path, TARGET, depth=1)
    if not graph.edges:
        raise AssertionError("complete FLT root theorem has no static dependency edge")
    if not all(edge["evidence_grade"] == "STATIC_REFERENCE" for edge in graph.edges):
        raise AssertionError("build-free FLT graph promoted static references to stronger evidence")
    if not all(edge["relation"] == "static_reference" for edge in graph.edges):
        raise AssertionError("build-free FLT graph returned a non-static relation")
    targets = {str(edge["target_id"]) for edge in graph.edges}
    if EXPECTED_DIRECT_DEPENDENCY not in targets:
        raise AssertionError(
            f"FLT root dependency missing: expected {EXPECTED_DIRECT_DEPENDENCY}, got {sorted(targets)}"
        )

    return {
        "status": "PASS",
        "artifact_identity": artifact_identity(manifest),
        "provenance": registered_replay_provenance(artifact_path, manifest),
        "exact": {
            "target": TARGET,
            "source_path": hit.source_path,
            "source_start_line": hit.source_start_line,
            "source_end_line": hit.source_end_line,
        },
        "graph": {"edge_count": len(graph.edges), "edges": list(graph.edges)},
        "evidence_boundary": "STATIC_REFERENCE_ONLY_NO_LEAN_BUILD_CLAIM",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_replay(args.db, args.artifact, args.descriptor)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
