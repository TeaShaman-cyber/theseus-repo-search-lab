from __future__ import annotations

import argparse
import json
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
        return 0
    except RepoSearchError as exc:
        return _emit_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
