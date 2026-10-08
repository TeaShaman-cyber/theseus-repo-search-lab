"""A bounded statement-corpus replay; does not assert a formal proof."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity
from theseus_repo_search.replay_contract import (
    prepare_registered_replay,
    registered_replay_provenance,
    require_context_chunks,
)
from theseus_repo_search.retrieval import context, search

TARGET = "ConjectureOfMarton.theorem_1_2"
SOURCE = "AnnalsChallenge/AnnalsOfMathematics/2025-201-2-ConjectureOfMarton.lean"


def run_replay(db_path: Path, artifact_path: Path, descriptor_path: Path) -> dict[str, object]:
    manifest, source = prepare_registered_replay(artifact_path, db_path, descriptor_path)
    if source.source_id != "annals-challenge-marton":
        raise AssertionError("selected descriptor is not Marton statement corpus")
    hits = search(db_path, TARGET, limit=10)
    exact = [hit for hit in hits if hit.declaration_hint == "theorem_1_2" and hit.source_path == SOURCE]
    if len(exact) != 1:
        raise AssertionError("Marton theorem source declaration not resolved exactly")
    ctx = context(db_path, TARGET, depth=1, token_budget=4000)
    chunks = require_context_chunks(ctx)
    if not any(chunk.get("declaration_hint") == "theorem_1_2" for chunk in chunks):
        raise AssertionError("bounded context did not recover Marton statement declaration")
    return {
        "status": "PASS",
        "artifact_identity": artifact_identity(manifest),
        "provenance": registered_replay_provenance(artifact_path, manifest),
        "exact": {
            "target": TARGET,
            "source_path": exact[0].source_path,
            "source_start_line": exact[0].source_start_line,
            "source_end_line": exact[0].source_end_line,
        },
        "context": {"estimated_tokens": ctx["estimated_tokens"], "chunks": chunks},
        "scientific_authority": "NONE",
        "corpus_class": "STATEMENT_CORPUS",
        "proof_evidence": "NOT_PROOF_EVIDENCE",
        "proof_note": "Pinned upstream theorem uses sorry; replay does not audit Lean axioms or prove Marton.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    result = run_replay(args.db, args.artifact, args.descriptor)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
