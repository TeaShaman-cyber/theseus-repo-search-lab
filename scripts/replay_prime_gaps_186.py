from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.graph import dependencies
from theseus_repo_search.replay_contract import validate_registered_replay_manifest
from theseus_repo_search.retrieval import context, search


TARGET = "PrimeGap186.primeGapLiminf_le_186"
LEXICAL_QUERY = "consecutive prime gaps"


def run_replay(db_path: Path, artifact_path: Path, descriptor_path: Path) -> dict[str, object]:
    manifest, _, _, _ = load_artifact(artifact_path)
    with sqlite3.connect(db_path) as conn:
        projected_identity = dict(conn.execute("SELECT key, value FROM meta"))["artifact_identity"]
    if projected_identity != artifact_identity(manifest):
        raise AssertionError("projection/artifact identity mismatch")
    source = validate_registered_replay_manifest(manifest, descriptor_path)
    exact_hits = search(db_path, TARGET, limit=1)
    if len(exact_hits) != 1 or exact_hits[0].source_path != "PrimeGaps186.lean":
        raise AssertionError("prime-gap theorem did not resolve to exact source provenance")

    graph = dependencies(db_path, TARGET, depth=1)
    if not graph.edges:
        raise AssertionError("prime-gap theorem has no in-scope elaborated dependency edges")
    if not all(str(edge["evidence_grade"]).startswith("ELABORATED_") for edge in graph.edges):
        raise AssertionError("graph replay returned non-elaborated dependency evidence")

    lexical = search(db_path, LEXICAL_QUERY, limit=10)
    if not any(hit.declaration_hint == "primeGapLiminf_le_186" for hit in lexical):
        raise AssertionError("grounded prime-gap lexical query missed primeGapLiminf_le_186")

    ctx = context(db_path, TARGET, depth=1, token_budget=4000)
    if not any(chunk["declaration_hint"] == "primeGapLiminf_le_186" for chunk in ctx["chunks"]):
        raise AssertionError("bounded context omitted target theorem source")

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
        "lexical": {
            "query": LEXICAL_QUERY,
            "hits": [hit.declaration_hint for hit in lexical],
        },
        "context": {
            "estimated_tokens": ctx["estimated_tokens"],
            "chunks": ctx["chunks"],
        },
        "descriptor": {
            "source_id": source.source_id,
            "root_modules": list(source.root_modules),
        },
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
