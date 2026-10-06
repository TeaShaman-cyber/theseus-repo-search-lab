from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from theseus_repo_search.artifact import artifact_identity
from theseus_repo_search.replay_contract import (
    prepare_registered_replay,
    registered_replay_provenance,
)
from theseus_repo_search.retrieval import context, search

TARGET = "DCR.dcr_three_of_confluent"
EXPECTED_SOURCE = "Singular.lean"


def run_replay(
    db_path: Path, artifact_path: Path, descriptor_path: Path
) -> dict[str, object]:
    manifest, _source = prepare_registered_replay(artifact_path, db_path, descriptor_path)
    exact_hits = search(db_path, TARGET, limit=1)
    if len(exact_hits) != 1 or exact_hits[0].source_path != EXPECTED_SOURCE:
        raise AssertionError(
            "DCR.dcr_three_of_confluent did not resolve to Singular.lean"
        )

    ctx = context(db_path, TARGET, depth=1, token_budget=4000)
    chunks = cast(list[dict[str, object]], ctx["chunks"])
    if not any(
        chunk["declaration_hint"] == "dcr_three_of_confluent"
        for chunk in chunks
    ):
        raise AssertionError(
            "bounded context omitted DCR.dcr_three_of_confluent source"
        )

    return {
        "status": "PASS",
        "artifact_identity": artifact_identity(manifest),
        "provenance": registered_replay_provenance(artifact_path, manifest),
        "exact": {
            "target": TARGET,
            "source_path": exact_hits[0].source_path,
            "source_start_line": exact_hits[0].source_start_line,
            "source_end_line": exact_hits[0].source_end_line,
        },
        "context": {
            "estimated_tokens": ctx["estimated_tokens"],
            "chunks": chunks,
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
