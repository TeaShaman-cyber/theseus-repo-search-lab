from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity, load_artifact

SCHEMA = "theseus.repo-search-consumer-receipt.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_receipt(
    *,
    artifact: Path,
    db: Path,
    replay: Path,
    artifact_name: str,
    repository_head: str,
    workflow_run_id: str,
    workflow_run_attempt: str,
    workflow_ref: str,
    workflow_sha: str,
) -> dict[str, object]:
    manifest, *_ = load_artifact(artifact)
    expected_identity = artifact_identity(manifest)

    with sqlite3.connect(db) as conn:
        quick_row = conn.execute("PRAGMA quick_check").fetchone()
        quick_check = None if quick_row is None else str(quick_row[0])
        projection_meta = dict(conn.execute("SELECT key, value FROM meta"))

    if quick_check != "ok":
        raise ValueError(f"SQLite quick_check failed: {quick_check}")

    projected_identity = projection_meta.get("artifact_identity")
    if projected_identity != expected_identity:
        raise ValueError(
            "projection artifact identity mismatch: "
            f"expected={expected_identity} observed={projected_identity}"
        )

    replay_payload = json.loads(replay.read_text(encoding="utf-8"))
    replay_status = replay_payload.get("status")
    if replay_status != "PASS":
        raise ValueError(f"replay status is not PASS: {replay_status}")

    return {
        "schema": SCHEMA,
        "result": "PASS",
        "workflow": {
            "repository_head": repository_head,
            "run_id": workflow_run_id,
            "run_attempt": workflow_run_attempt,
            "workflow_ref": workflow_ref,
            "workflow_sha": workflow_sha,
        },
        "artifact": {
            "name": artifact_name,
            "identity": expected_identity,
            "source_repo": manifest.source_repo,
            "source_commit": manifest.source_commit,
            "source_subdir": manifest.source_subdir,
        },
        "projection": {
            "quick_check": quick_check,
            "artifact_identity": projected_identity,
            "db_sha256": _sha256(db),
        },
        "replay": {
            "status": replay_status,
            "sha256": _sha256(replay),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--repository-head", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-run-attempt", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipt = build_receipt(
        artifact=args.artifact,
        db=args.db,
        replay=args.replay,
        artifact_name=args.artifact_name,
        repository_head=args.repository_head,
        workflow_run_id=args.workflow_run_id,
        workflow_run_attempt=args.workflow_run_attempt,
        workflow_ref=args.workflow_ref,
        workflow_sha=args.workflow_sha,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
