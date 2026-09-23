#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA = "theseus.repo-search-mutation.v1"
COUNT_FIELDS = (
    "killed",
    "survived",
    "total",
    "no_tests",
    "skipped",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)
SUM_FIELDS = tuple(field for field in COUNT_FIELDS if field != "total")
INCOMPLETE_FIELDS = (
    "no_tests",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base(args: argparse.Namespace) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "mutmut_version": args.mutmut_version,
        "source_commit": args.source_commit,
        "lock_sha256": _sha256(args.lock_file),
        "source": "src/theseus_repo_search/normalize.py",
        "tests": ["tests/test_normalize.py"],
    }


def classify(stats: dict[str, object]) -> tuple[str, dict[str, int], str | None]:
    counts: dict[str, int] = {}
    for key in COUNT_FIELDS:
        value = stats.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return "MUTATION_RUNTIME_FAILED", {}, f"invalid_stats_field:{key}"
        counts[key] = value
    if counts["total"] != sum(counts[key] for key in SUM_FIELDS):
        return "MUTATION_RUNTIME_FAILED", counts, "stats_total_mismatch"
    if any(counts[key] for key in INCOMPLETE_FIELDS):
        return "MUTATION_INCOMPLETE", counts, None
    if counts["survived"]:
        return "SURVIVORS_PRESENT", counts, None
    return "MUTATION_CLEAN", counts, None


def write_receipt(args: argparse.Namespace) -> int:
    payload = _base(args)
    if args.runtime_failure:
        payload.update(
            {
                "state": "MUTATION_RUNTIME_FAILED",
                "reason": args.runtime_failure,
            }
        )
        result = 2
    else:
        try:
            stats = json.loads(args.stats.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            payload.update(
                {
                    "state": "MUTATION_RUNTIME_FAILED",
                    "reason": f"invalid_stats:{type(exc).__name__}",
                }
            )
            result = 2
        else:
            if not isinstance(stats, dict):
                payload.update(
                    {
                        "state": "MUTATION_RUNTIME_FAILED",
                        "reason": "invalid_stats:not_object",
                    }
                )
                result = 2
            else:
                state, counts, reason = classify(stats)
                payload.update({"state": state})
                if counts:
                    payload["counts"] = counts
                if reason:
                    payload["reason"] = reason
                result = 0 if state == "MUTATION_CLEAN" else 1 if state == "SURVIVORS_PRESENT" else 2

    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--mutmut-version", required=True)
    parser.add_argument("--runtime-failure")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stats is None and not args.runtime_failure:
        raise SystemExit("--stats is required unless --runtime-failure is set")
    return write_receipt(args)


if __name__ == "__main__":
    raise SystemExit(main())
