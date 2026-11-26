"""Bounded source-free replay for explicitly registered intake witnesses."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity
from theseus_repo_search.graph import dependencies
from theseus_repo_search.replay_contract import (
    DEFAULT_RUNNER,
    prepare_registered_replay,
    registered_replay_provenance,
    require_context_chunks,
)
from theseus_repo_search.retrieval import context, search

WITNESSES = Path(__file__).resolve().parents[1] / "producer/pilots/queued-intake-witnesses.json"


def run_replay(db: Path, artifact: Path, descriptor: Path, *, witnesses: Path = WITNESSES, runner_path: Path = DEFAULT_RUNNER) -> dict[str, object]:
    manifest, source = prepare_registered_replay(artifact, db, descriptor, runner_path=runner_path)
    witness = json.loads(witnesses.read_text())[source.source_id]
    target, path, klass = (witness[k] for k in ("declaration", "path", "corpus_class"))
    if klass not in {"PROOF_CORPUS", "PARTIAL_PROOF_CORPUS", "STATEMENT_CORPUS"}:
        raise AssertionError("unsupported evidence class")
    hits = search(db, "lean:" + target, limit=1)
    if len(hits) != 1 or hits[0].declaration_id != "lean:" + target or hits[0].match_mode != "exact" or hits[0].source_path != path:
        raise AssertionError(f"exact witness source binding failed: target={target}, expected_path={path}, observed={[(h.declaration_id, h.source_path, h.match_mode) for h in hits]}")
    graph = dependencies(db, target, depth=1)
    if klass != "STATEMENT_CORPUS" and not graph.edges:
        raise AssertionError("proof-side witness has no in-scope dependency")
    if any(not str(edge["evidence_grade"]).startswith("ELABORATED_") for edge in graph.edges):
        raise AssertionError("unexpected dependency evidence grade")
    ctx = context(db, target, depth=1, token_budget=8000)
    chunks = require_context_chunks(ctx)
    if not any(chunk.get("declaration_hint") == witness.get("source_hint", target.rsplit(".", 1)[-1]) and chunk.get("source_path") == path for chunk in chunks):
        raise AssertionError("bounded context omitted witness")
    return {
        "status": "PASS", "artifact_identity": artifact_identity(manifest),
        "provenance": registered_replay_provenance(artifact, manifest),
        "exact": {"target": target, "source_path": path},
        "edge_count": len(graph.edges), "corpus_class": klass,
        "proof_evidence": "UNKNOWN_NOT_PROOF_EVIDENCE" if klass == "PROOF_CORPUS" else "NOT_PROOF_EVIDENCE",
        "scientific_authority": "NONE",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("db", "artifact", "descriptor", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--source-root", type=Path)
    p.add_argument("--runner", type=Path, default=DEFAULT_RUNNER)
    a = p.parse_args()
    result = run_replay(a.db, a.artifact, a.descriptor, runner_path=a.runner)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
