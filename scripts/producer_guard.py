from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.producer_config import (
    LeanArchiveSource,
    LeanGitSource,
    load_lean_archive_source,
    load_lean_source,
)

if TYPE_CHECKING:
    from scripts.materialize_archive_source import verify_materialized_archive_members
elif __package__:
    from .materialize_archive_source import verify_materialized_archive_members
else:
    from materialize_archive_source import verify_materialized_archive_members


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


def verify_tracked_source_clean(repo_dir: Path, source_subdir: str) -> None:
    pathspec = source_subdir or "."
    try:
        result = subprocess.run(
            [
                "git", "-C", str(repo_dir), "status", "--porcelain=v1",
                "--untracked-files=no", "--", pathspec,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"failed to verify tracked source worktree cleanliness: {exc}",
        ) from exc
    if result.stdout.strip():
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"tracked source worktree is dirty: {pathspec}",
        )


def read_bound_toolchain(cwd: Path) -> str:
    toolchain_path = cwd / "lean-toolchain"
    if toolchain_path.is_symlink() or not toolchain_path.is_file():
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"source-owned lean-toolchain must be a non-symlink regular file: {toolchain_path}",
        )
    try:
        observed_toolchain = toolchain_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"cannot read source-owned lean-toolchain: {toolchain_path}: {exc}",
        ) from exc
    if not observed_toolchain:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"source-owned lean-toolchain is empty: {toolchain_path}",
        )
    return observed_toolchain


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
    repo_root = repo_dir.resolve()
    expected_source_root = (repo_root / source_subdir).resolve() if source_subdir else repo_root
    try:
        expected_source_root.relative_to(repo_root)
    except ValueError as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"source subdir escapes verified checkout: {source_subdir}",
        ) from exc
    if cwd.resolve() != expected_source_root:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"extraction cwd must equal verified source root: expected {expected_source_root}, observed {cwd.resolve()}",
        )
    verify_checked_out_commit(repo_dir, expected_commit)
    verify_tracked_source_clean(repo_dir, source_subdir)
    observed_toolchain = read_bound_toolchain(cwd)
    raw_depgraph.parent.mkdir(parents=True, exist_ok=True)
    raw_depgraph.unlink(missing_ok=True)
    run_exact_command(argv, cwd)
    verify_checked_out_commit(repo_dir, expected_commit)
    verify_tracked_source_clean(repo_dir, source_subdir)
    try:
        raw_bytes = raw_depgraph.read_bytes()
    except OSError as exc:
        raise RepoSearchError(
            "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE",
            f"exact extraction did not produce raw dependency graph: {raw_depgraph}",
        ) from exc

    receipt = {
        "schema": "theseus.raw-depgraph-receipt.v2",
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
        "observed": {"lean_toolchain": observed_toolchain},
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


def _verified_archive_materialization(
    source: LeanArchiveSource,
    *,
    materialization_root: Path,
    materialization_receipt: Path,
) -> dict[str, str]:
    try:
        before = materialization_receipt.read_bytes()
    except OSError as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"cannot read archive materialization receipt: {exc}",
        ) from exc
    verify_materialized_archive_members(
        source,
        dest=materialization_root,
        receipt_path=materialization_receipt,
    )
    try:
        after = materialization_receipt.read_bytes()
        receipt = json.loads(after.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"cannot read verified archive materialization receipt: {exc}",
        ) from exc
    if before != after:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "archive materialization receipt changed during verification",
        )
    materialized = receipt.get("materialized") if isinstance(receipt, dict) else None
    if not isinstance(materialized, dict):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "archive materialization receipt has invalid materialized evidence",
        )
    tree_sha256 = materialized.get("tree_sha256")
    member_manifest_sha256 = materialized.get("member_manifest_sha256")
    if not isinstance(tree_sha256, str) or not isinstance(member_manifest_sha256, str):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "archive materialization receipt is missing hash evidence",
        )
    return {
        "tree_sha256": tree_sha256,
        "member_manifest_sha256": member_manifest_sha256,
    }


def _publish_archive_raw_receipt(receipt_path: Path, receipt: dict[str, object]) -> None:
    receipt_path = receipt_path.resolve()
    if receipt_path.exists():
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"archive raw dependency graph receipt target already exists: {receipt_path}",
        )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{receipt_path.name}.tmp-", dir=receipt_path.parent
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        try:
            os.link(temp, receipt_path)
        except OSError as exc:
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                f"cannot publish archive raw dependency graph receipt without clobber: {exc}",
            ) from exc
    finally:
        temp.unlink(missing_ok=True)


def run_bound_source_extraction(
    argv: list[str],
    *,
    cwd: Path,
    source_container: Path,
    descriptor_path: Path,
    materialization_receipt: Path | None,
    raw_depgraph: Path,
    receipt_path: Path,
    producer_kind: str,
    producer_tool_repo: str,
    producer_tool_commit: str,
    producer_tool_hash: str,
) -> None:
    source = load_lean_source(descriptor_path)
    if isinstance(source, LeanGitSource):
        run_bound_extraction(
            argv,
            cwd=cwd,
            repo_dir=source_container,
            expected_commit=source.source_commit,
            raw_depgraph=raw_depgraph,
            receipt_path=receipt_path,
            source_repo=source.source_repo,
            source_subdir=source.source_subdir,
            root_modules=source.root_modules,
            producer_kind=producer_kind,
            producer_tool_repo=producer_tool_repo,
            producer_tool_commit=producer_tool_commit,
            producer_tool_hash=producer_tool_hash,
        )
        return
    if isinstance(source, LeanArchiveSource):
        if materialization_receipt is None:
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                "archive extraction requires materialization receipt",
            )
        run_bound_archive_extraction(
            argv,
            cwd=cwd,
            materialization_root=source_container,
            materialization_receipt=materialization_receipt,
            source=source,
            raw_depgraph=raw_depgraph,
            receipt_path=receipt_path,
            producer_kind=producer_kind,
            producer_tool_repo=producer_tool_repo,
            producer_tool_commit=producer_tool_commit,
            producer_tool_hash=producer_tool_hash,
        )
        return
    raise TypeError("unsupported Lean source descriptor type")


def run_bound_archive_extraction(
    argv: list[str],
    *,
    cwd: Path,
    materialization_root: Path,
    materialization_receipt: Path,
    source: LeanArchiveSource,
    raw_depgraph: Path,
    receipt_path: Path,
    producer_kind: str,
    producer_tool_repo: str,
    producer_tool_commit: str,
    producer_tool_hash: str,
) -> None:
    materialization_root = materialization_root.resolve()
    receipt_path = receipt_path.resolve()
    if receipt_path.exists():
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"archive raw dependency graph receipt target already exists: {receipt_path}",
        )
    expected_source_root = source.resolve_source_root(materialization_root)
    if cwd.resolve() != expected_source_root:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "extraction cwd must equal verified archive source root: "
            f"expected {expected_source_root}, observed {cwd.resolve()}",
        )

    pre_evidence = _verified_archive_materialization(
        source,
        materialization_root=materialization_root,
        materialization_receipt=materialization_receipt,
    )
    observed_toolchain = read_bound_toolchain(cwd)
    raw_depgraph.parent.mkdir(parents=True, exist_ok=True)
    raw_depgraph.unlink(missing_ok=True)
    run_exact_command(argv, cwd)
    post_evidence = _verified_archive_materialization(
        source,
        materialization_root=materialization_root,
        materialization_receipt=materialization_receipt,
    )
    if post_evidence != pre_evidence:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "archive materialization evidence changed during extraction",
        )
    try:
        raw_bytes = raw_depgraph.read_bytes()
    except OSError as exc:
        raise RepoSearchError(
            "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE",
            f"exact extraction did not produce raw dependency graph: {raw_depgraph}",
        ) from exc

    receipt: dict[str, object] = {
        "schema": "theseus.raw-depgraph-receipt.v3",
        "source": {
            "kind": "archive",
            "url": source.archive_url,
            "sha256": source.archive_sha256,
            "format": source.archive_format,
            "subdir": source.source_subdir,
        },
        "materialization": pre_evidence,
        "scope": {"root_modules": list(source.root_modules)},
        "producer": {
            "kind": producer_kind,
            "tool_repo": producer_tool_repo,
            "tool_commit": producer_tool_commit,
            "tool_hash": producer_tool_hash,
        },
        "observed": {"lean_toolchain": observed_toolchain},
        "raw_depgraph": {"sha256": sha256(raw_bytes).hexdigest()},
    }
    _publish_archive_raw_receipt(receipt_path, receipt)


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

    toolchain = subparsers.add_parser("toolchain")
    toolchain.add_argument("--cwd", type=Path, required=True)

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

    extract_source = subparsers.add_parser("extract-source")
    extract_source.add_argument("--cwd", type=Path, required=True)
    extract_source.add_argument("--source-container", type=Path, required=True)
    extract_source.add_argument("--source", type=Path, required=True)
    extract_source.add_argument("--materialization-receipt", type=Path)
    extract_source.add_argument("--raw-depgraph", type=Path, required=True)
    extract_source.add_argument("--receipt", type=Path, required=True)
    extract_source.add_argument("--producer-kind", required=True)
    extract_source.add_argument("--producer-tool-repo", required=True)
    extract_source.add_argument("--producer-tool-commit", required=True)
    extract_source.add_argument("--producer-tool-hash", required=True)
    extract_source.add_argument("argv", nargs=argparse.REMAINDER)

    extract_archive = subparsers.add_parser("extract-archive")
    extract_archive.add_argument("--cwd", type=Path, required=True)
    extract_archive.add_argument("--materialization-root", type=Path, required=True)
    extract_archive.add_argument("--materialization-receipt", type=Path, required=True)
    extract_archive.add_argument("--source", type=Path, required=True)
    extract_archive.add_argument("--raw-depgraph", type=Path, required=True)
    extract_archive.add_argument("--receipt", type=Path, required=True)
    extract_archive.add_argument("--producer-kind", required=True)
    extract_archive.add_argument("--producer-tool-repo", required=True)
    extract_archive.add_argument("--producer-tool-commit", required=True)
    extract_archive.add_argument("--producer-tool-hash", required=True)
    extract_archive.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "checkout":
            checkout_exact(args.repo_url, args.commit, args.dest)
        elif args.command == "verify":
            verify_checked_out_commit(args.repo_dir, args.expected)
        elif args.command == "toolchain":
            sys.stdout.write(read_bound_toolchain(args.cwd) + "\n")
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
        elif args.command == "extract-source":
            command = list(args.argv)
            if command and command[0] == "--":
                command = command[1:]
            if not command:
                raise RepoSearchError(
                    "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE",
                    "no exact extraction command provided",
                )
            run_bound_source_extraction(
                command,
                cwd=args.cwd,
                source_container=args.source_container,
                descriptor_path=args.source,
                materialization_receipt=args.materialization_receipt,
                raw_depgraph=args.raw_depgraph,
                receipt_path=args.receipt,
                producer_kind=args.producer_kind,
                producer_tool_repo=args.producer_tool_repo,
                producer_tool_commit=args.producer_tool_commit,
                producer_tool_hash=args.producer_tool_hash,
            )
        elif args.command == "extract-archive":
            command = list(args.argv)
            if command and command[0] == "--":
                command = command[1:]
            if not command:
                raise RepoSearchError(
                    "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE",
                    "no exact extraction command provided",
                )
            source = load_lean_archive_source(args.source)
            run_bound_archive_extraction(
                command,
                cwd=args.cwd,
                materialization_root=args.materialization_root,
                materialization_receipt=args.materialization_receipt,
                source=source,
                raw_depgraph=args.raw_depgraph,
                receipt_path=args.receipt,
                producer_kind=args.producer_kind,
                producer_tool_repo=args.producer_tool_repo,
                producer_tool_commit=args.producer_tool_commit,
                producer_tool_hash=args.producer_tool_hash,
            )
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
