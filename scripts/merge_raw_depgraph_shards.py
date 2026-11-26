#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

from theseus_repo_search.sharding import merge_raw_depgraph_shards


def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path}: expected JSON object")
    return value


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-subdir", required=True)
    parser.add_argument("--scope-root-module", action="append", required=True)
    parser.add_argument("--producer-kind", required=True)
    parser.add_argument("--producer-tool-repo", required=True)
    parser.add_argument("--producer-tool-commit", required=True)
    parser.add_argument("--producer-tool-hash", required=True)
    parser.add_argument("--out-raw", type=Path, required=True)
    parser.add_argument("--out-receipt", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    shard_dirs = sorted(
        (path for path in args.input_root.iterdir() if path.is_dir() and path.name.startswith("shard-")),
        key=lambda path: path.name,
    )
    if not shard_dirs:
        raise ValueError("no shard-* input directories found")

    raw_graphs: list[dict[str, object]] = []
    shard_evidence: list[dict[str, object]] = []
    expected_toolchain: str | None = None
    expected_extractor: dict[str, object] | None = None

    for index, shard_dir in enumerate(shard_dirs):
        expected_name = f"shard-{index:03d}"
        if shard_dir.name != expected_name:
            raise ValueError(
                f"shard ordering mismatch: expected {expected_name}, observed {shard_dir.name}"
            )
        raw_path = shard_dir / "raw-depgraph.json"
        receipt_path = shard_dir / "raw-depgraph-receipt.json"
        raw = _load_object(raw_path)
        receipt = _load_object(receipt_path)

        if receipt.get("schema") != "theseus.raw-depgraph-receipt.v2":
            raise ValueError(f"{receipt_path}: expected v2 shard receipt")
        expected_source = {
            "repo": args.source_repo,
            "commit": args.source_commit,
            "subdir": args.source_subdir,
        }
        if receipt.get("source") != expected_source:
            raise ValueError(f"{receipt_path}: source mismatch")

        raw_hash = _sha256(raw_path)
        if receipt.get("raw_depgraph") != {"sha256": raw_hash}:
            raise ValueError(f"{receipt_path}: raw dependency graph hash mismatch")

        observed = receipt.get("observed")
        if (
            not isinstance(observed, dict)
            or set(observed) != {"lean_toolchain"}
            or not isinstance(observed.get("lean_toolchain"), str)
            or not observed["lean_toolchain"].strip()
        ):
            raise ValueError(f"{receipt_path}: invalid observed toolchain")
        toolchain = observed["lean_toolchain"]
        if expected_toolchain is None:
            expected_toolchain = toolchain
        elif toolchain != expected_toolchain:
            raise ValueError(f"{receipt_path}: toolchain mismatch")

        producer = receipt.get("producer")
        if not isinstance(producer, dict):
            raise TypeError(f"{receipt_path}: invalid producer")
        if expected_extractor is None:
            expected_extractor = producer
        elif producer != expected_extractor:
            raise ValueError(f"{receipt_path}: extractor producer mismatch")

        scope = receipt.get("scope")
        roots = scope.get("root_modules") if isinstance(scope, dict) else None
        if (
            not isinstance(roots, list)
            or not roots
            or not all(isinstance(root, str) and root for root in roots)
        ):
            raise ValueError(f"{receipt_path}: invalid shard root_modules")

        raw_graphs.append(raw)
        shard_evidence.append(
            {
                "index": index,
                "raw_depgraph_sha256": raw_hash,
                "receipt_sha256": _sha256(receipt_path),
                "root_modules": roots,
            }
        )

    merged = merge_raw_depgraph_shards(raw_graphs)
    args.out_raw.parent.mkdir(parents=True, exist_ok=True)
    args.out_raw.write_text(
        json.dumps(merged, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    merged_raw_sha256 = _sha256(args.out_raw)
    receipt = {
        "schema": "theseus.raw-depgraph-receipt.v4",
        "source": {
            "repo": args.source_repo,
            "commit": args.source_commit,
            "subdir": args.source_subdir,
        },
        "scope": {"root_modules": list(args.scope_root_module)},
        "producer": {
            "kind": args.producer_kind,
            "tool_repo": args.producer_tool_repo,
            "tool_commit": args.producer_tool_commit,
            "tool_hash": args.producer_tool_hash,
        },
        "observed": {"lean_toolchain": expected_toolchain},
        "raw_depgraph": {"sha256": merged_raw_sha256},
        "shards": shard_evidence,
    }
    args.out_receipt.parent.mkdir(parents=True, exist_ok=True)
    args.out_receipt.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "MERGED",
                "shards": len(shard_dirs),
                "nodes": len(merged["nodes"]),
                "edges": len(merged["edges"]),
                "raw_depgraph_sha256": merged_raw_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCKED_SHARD_MERGE: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
