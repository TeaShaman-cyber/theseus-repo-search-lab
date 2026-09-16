from __future__ import annotations

import argparse
import json
import os
from hashlib import sha256
import subprocess
import sys
from pathlib import Path

from theseus_repo_search.errors import RepoSearchError


def checkout_exact(repo_url: str, commit: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        ["git", "init", str(dest)],
        ["git", "-C", str(dest), "remote", "add", "origin", repo_url],
        ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", commit],
        ["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"],
    ]
    try:
        for command in commands:
            subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"failed to checkout exact source commit {commit}: {exc}",
        ) from exc


def verify_checked_out_commit(repo_dir: Path, expected: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"failed to read checked-out source commit: {exc}",
        ) from exc
    actual = result.stdout.strip()
    if actual != expected:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            f"source commit mismatch: expected {expected}, observed {actual}",
        )
    return actual


def run_exact_command(argv: list[str], cwd: Path) -> None:
    try:
        subprocess.run(argv, cwd=cwd, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RepoSearchError(
            "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE",
            f"exact extraction command failed: {' '.join(argv)}: {exc}",
        ) from exc


def run_bound_extraction(
    argv: list[str],
    *,
    cwd: Path,
    repo_dir: Path,
    expected_commit: str,
    raw_depgraph: Path,
    receipt_path: Path,
    source_repo: str,
    source_subdir: str,
    root_modules: tuple[str, ...],
    producer_kind: str,
    producer_tool_repo: str,
    producer_tool_commit: str,
    producer_tool_hash: str,
) -> None:
    verify_checked_out_commit(repo_dir, expected_commit)
    raw_depgraph.parent.mkdir(parents=True, exist_ok=True)
    raw_depgraph.unlink(missing_ok=True)
    run_exact_command(argv, cwd)
    verify_checked_out_commit(repo_dir, expected_commit)
    try:
        raw_bytes = raw_depgraph.read_bytes()
    except OSError as exc:
        raise RepoSearchError(
            "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE",
            f"exact extraction did not produce raw dependency graph: {raw_depgraph}",
        ) from exc

    receipt = {
        "schema": "theseus.raw-depgraph-receipt.v1",
        "source": {
            "repo": source_repo,
            "commit": expected_commit,
            "subdir": source_subdir,
        },
        "scope": {"root_modules": list(root_modules)},
        "producer": {
            "kind": producer_kind,
            "tool_repo": producer_tool_repo,
            "tool_commit": producer_tool_commit,
            "tool_hash": producer_tool_hash,
        },
        "raw_depgraph": {"sha256": sha256(raw_bytes).hexdigest()},
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temp = receipt_path.with_name(f".{receipt_path.name}.tmp-{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, receipt_path)
    finally:
        temp.unlink(missing_ok=True)


def _status(code: str) -> str:
    if code.startswith("BLOCKED_"):
        return "BLOCKED"
    if code.startswith("DEGRADED_"):
        return "DEGRADED"
    return "ERROR"


def _emit_error(exc: RepoSearchError) -> int:
    sys.stderr.write(
        json.dumps(
            {"status": _status(exc.code), "code": exc.code, "message": str(exc)},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    checkout = subparsers.add_parser("checkout")
    checkout.add_argument("--repo-url", required=True)
    checkout.add_argument("--commit", required=True)
    checkout.add_argument("--dest", type=Path, required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--repo-dir", type=Path, required=True)
    verify.add_argument("--expected", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--cwd", type=Path, required=True)
    run.add_argument("argv", nargs=argparse.REMAINDER)

    extract = subparsers.add_parser("extract")
    extract.add_argument("--cwd", type=Path, required=True)
    extract.add_argument("--repo-dir", type=Path, required=True)
    extract.add_argument("--expected", required=True)
    extract.add_argument("--raw-depgraph", type=Path, required=True)
    extract.add_argument("--receipt", type=Path, required=True)
    extract.add_argument("--source-repo", required=True)
    extract.add_argument("--source-subdir", required=True)
    extract.add_argument("--root-module", action="append", required=True)
    extract.add_argument("--producer-kind", required=True)
    extract.add_argument("--producer-tool-repo", required=True)
    extract.add_argument("--producer-tool-commit", required=True)
    extract.add_argument("--producer-tool-hash", required=True)
    extract.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "checkout":
            checkout_exact(args.repo_url, args.commit, args.dest)
        elif args.command == "verify":
            verify_checked_out_commit(args.repo_dir, args.expected)
        elif args.command == "run":
            command = list(args.argv)
            if command and command[0] == "--":
                command = command[1:]
            if not command:
                raise RepoSearchError(
                    "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE",
                    "no exact extraction command provided",
                )
            run_exact_command(command, args.cwd)
        elif args.command == "extract":
            command = list(args.argv)
            if command and command[0] == "--":
                command = command[1:]
            if not command:
                raise RepoSearchError(
                    "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE",
                    "no exact extraction command provided",
                )
            run_bound_extraction(
                command,
                cwd=args.cwd,
                repo_dir=args.repo_dir,
                expected_commit=args.expected,
                raw_depgraph=args.raw_depgraph,
                receipt_path=args.receipt,
                source_repo=args.source_repo,
                source_subdir=args.source_subdir,
                root_modules=tuple(args.root_module),
                producer_kind=args.producer_kind,
                producer_tool_repo=args.producer_tool_repo,
                producer_tool_commit=args.producer_tool_commit,
                producer_tool_hash=args.producer_tool_hash,
            )
        return 0
    except RepoSearchError as exc:
        return _emit_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
