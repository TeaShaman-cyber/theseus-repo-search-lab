from __future__ import annotations

import argparse
import json
from pathlib import Path

from theseus_repo_search.graph import dependencies
from theseus_repo_search.replay_contract import prepare_registered_replay
from theseus_repo_search.retrieval import context, search

TARGET = "ErdosProblems.MulticolourTriangleRamsey.erdos_183"
LEXICAL_QUERY = "erdos 183 triangle ramsey"
EXPECTED_DEPENDENCIES = {
    "lean:ErdosProblems.MulticolourTriangleRamsey.divergentRamseyRoot",
}


def run_replay(db_path: Path, artifact_path: Path, descriptor_path: Path) -> dict[str, object]:
    manifest, _ = prepare_registered_replay(artifact_path, db_path, descriptor_path)
    exact_hits = search(db_path, TARGET, limit=1)
    if len(exact_hits) != 1 or exact_hits[0].source_path != "MulticolorTriangleRamsey.lean":
        raise AssertionError("erdos_183 did not resolve to exact ten-proofs source provenance")

    graph = dependencies(db_path, TARGET, depth=1)
    if not graph.edges:
        raise AssertionError("erdos_183 has no in-scope elaborated dependency edges")
    if not all(str(edge["evidence_grade"]).startswith("ELABORATED_") for edge in graph.edges):
        raise AssertionError("graph replay returned non-elaborated dependency evidence")
    direct_targets = {str(edge["target_id"]) for edge in graph.edges}
    if not EXPECTED_DEPENDENCIES.issubset(direct_targets):
        missing = sorted(EXPECTED_DEPENDENCIES - direct_targets)
        raise AssertionError(f"ten-proofs endpoint missing required direct dependencies: {missing}")

    lexical = search(db_path, LEXICAL_QUERY, limit=10)
    if not any(hit.declaration_hint == "erdos_183" for hit in lexical):
        raise AssertionError("grounded ten-proofs lexical query missed erdos_183")

    ctx = context(db_path, TARGET, depth=1, token_budget=4000)
    if not any(chunk["declaration_hint"] == "erdos_183" for chunk in ctx["chunks"]):
        raise AssertionError("bounded context omitted ten-proofs endpoint source")

    return {
        "status": "PASS",
        "provenance": {
            "repo": manifest.source_repo,
            "commit": manifest.source_commit,
            "subdir": manifest.source_subdir,
        },
        "exact": {
            "target": TARGET,
            "source_path": exact_hits[0].source_path,
            "source_start_line": exact_hits[0].source_start_line,
            "source_end_line": exact_hits[0].source_end_line,
        },
        "graph": {"edge_count": len(graph.edges), "edges": list(graph.edges)},
        "lexical": {"query": LEXICAL_QUERY, "hits": [hit.declaration_hint for hit in lexical]},
        "context": {"estimated_tokens": ctx["estimated_tokens"], "chunks": ctx["chunks"]},
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
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
