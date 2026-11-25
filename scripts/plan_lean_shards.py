#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from theseus_repo_search.sharding import ModuleWeight, partition_modules


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--module-prefix", default="")
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8")


def main() -> int:
    args = _parse_args()
    source_prefix = args.source_prefix.strip("/")
    if not source_prefix:
        raise ValueError("source-prefix must be non-empty")

    source_commit = _git(args.repo_root, "rev-parse", "HEAD").strip()
    # NUL framing disables Git's path quoting and preserves UTF-8 module names.
    tree = _git(args.repo_root, "ls-tree", "-r", "-l", "-z", "HEAD", "--", source_prefix)
    rows = tree.split("\0")
    if rows.pop() != "":
        raise ValueError("git ls-tree output is not NUL-terminated")

    modules: list[ModuleWeight] = []
    prefix = source_prefix + "/"
    for row in rows:
        try:
            left, path = row.split("\t", 1)
            size_text = left.split()[-1]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"cannot parse git ls-tree row: {row!r}") from exc
        if not path.startswith(prefix) or not path.endswith(".lean"):
            continue
        size = int(size_text)
        module = path[len(prefix) : -len(".lean")].replace("/", ".")
        module_prefix = args.module_prefix.strip(".")
        if module_prefix:
            module = f"{module_prefix}.{module}"
        modules.append(ModuleWeight(module=module, size_bytes=size))

    if not modules:
        raise ValueError("no Lean modules found under source-prefix")

    shards = partition_modules(modules, args.shard_count)
    payload = {
        "schema": "theseus.lean-module-shards.v1",
        "source_commit": source_commit,
        "source_prefix": source_prefix,
        "module_prefix": args.module_prefix.strip("."),
        "shard_count": len(shards),
        "module_count": len(modules),
        "total_bytes": sum(item.size_bytes for item in modules),
        "shards": [
            {
                "index": shard.index,
                "module_count": len(shard.modules),
                "total_bytes": shard.total_bytes,
                "modules": [item.module for item in shard.modules],
            }
            for shard in shards
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "PLANNED",
                "source_commit": source_commit,
                "module_count": len(modules),
                "total_bytes": payload["total_bytes"],
                "shard_count": len(shards),
                "min_shard_bytes": min(shard.total_bytes for shard in shards),
                "max_shard_bytes": max(shard.total_bytes for shard in shards),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"BLOCKED_SHARD_PLAN: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
