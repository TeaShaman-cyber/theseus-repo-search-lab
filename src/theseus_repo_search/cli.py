from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import subprocess
from hashlib import sha256
from dataclasses import asdict
from pathlib import Path

from .artifact import artifact_identity, load_artifact, write_artifact
from .errors import RepoSearchError
from .graph import dependencies, path as graph_path, reverse_dependencies
from .model import ArtifactScope, EvidenceGrade, ProducerPin
from .normalize import normalize_leandepviz
from .projection import build_projection
from .retrieval import SearchHit, context as build_context, search as search_repo
from .sources import bind_node_sources, scan_lean_sources


def _emit(payload: dict[str, object], *, stream=sys.stdout) -> None:
    stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


def _error(exc: RepoSearchError) -> int:
    if exc.code.startswith("BLOCKED_"):
        status = "BLOCKED"
    elif exc.code.startswith("UNAVAILABLE_"):
        status = "UNAVAILABLE"
    elif exc.code.startswith("DEGRADED_"):
        status = "DEGRADED"
    else:
        status = "ERROR"
    _emit(
        {"status": status, "code": exc.code, "message": str(exc)},
        stream=sys.stderr,
    )
    return 2




def _normalized_github_repo(value: str) -> str | None:
    text = value.strip().removesuffix(".git")
    if text.startswith("https://github.com/"):
        return text[len("https://github.com/"):]
    if text.startswith("git@github.com:"):
        return text[len("git@github.com:"):]
    if text.startswith("ssh://git@github.com/"):
        return text[len("ssh://git@github.com/"):]
    if "/" in text and "://" not in text and "@" not in text:
        return text
    return None


def _git_read(source_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"source root is not a readable Git checkout: {source_root}",
        ) from exc
    return result.stdout.strip()


def _verify_authoritative_source(
    source_root: Path, *, source_repo: str, source_commit: str, source_subdir: str
) -> None:
    repo_root = Path(_git_read(source_root, "rev-parse", "--show-toplevel")).resolve()
    actual_commit = _git_read(source_root, "rev-parse", "HEAD")
    if actual_commit != source_commit:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            f"source commit mismatch: expected {source_commit}, observed {actual_commit}",
        )

    subdir_path = Path(source_subdir) if source_subdir else Path(".")
    if subdir_path.is_absolute() or ".." in subdir_path.parts:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            f"invalid source subdir for authoritative binding: {source_subdir}",
        )
    expected_root = (repo_root / subdir_path).resolve()
    if source_root.resolve() != expected_root:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            f"source root does not match source subdir: expected {expected_root}, observed {source_root.resolve()}",
        )

    origin = _git_read(repo_root, "remote", "get-url", "origin")
    observed_repo = _normalized_github_repo(origin)
    expected_repo = _normalized_github_repo(source_repo)
    if expected_repo is None or observed_repo != expected_repo:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            f"source repository mismatch: expected {source_repo}, observed {origin}",
        )

    relative = source_root.resolve().relative_to(repo_root).as_posix() or "."
    status = _git_read(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
        "--",
        relative,
    )
    if status:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "source checkout is dirty inside the authoritative source scope",
        )


def _search_hit_dict(hit: SearchHit) -> dict[str, object]:
    data = asdict(hit)
    grade = data["evidence_grade"]
    if isinstance(grade, EvidenceGrade):
        data["evidence_grade"] = grade.value
    return data


def _graph_payload(result) -> dict[str, object]:
    return {
        "status": "FOUND" if result.found else "UNKNOWN",
        "query": result.query,
        "edges": list(result.edges),
        "scope_root_modules": list(result.scope_root_modules),
        "dependency_boundary": result.dependency_boundary,
        "complete_within_scope": result.complete_within_scope,
        "created_from_authoritative_commit": result.created_from_authoritative_commit,
    }


def _verify_raw_depgraph_receipt(
    receipt_path: Path,
    raw_depgraph_path: Path,
    *,
    source_repo: str,
    source_commit: str,
    source_subdir: str,
    root_modules: tuple[str, ...],
    producer_kind: str,
    producer_tool_repo: str,
    producer_tool_commit: str,
    producer_tool_hash: str,
) -> None:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        raw_hash = sha256(raw_depgraph_path.read_bytes()).hexdigest()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"invalid raw dependency graph receipt: {exc}",
        ) from exc
    if not isinstance(receipt, dict):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "invalid raw dependency graph receipt: root must be an object",
        )
    observed = receipt.get("observed")
    if (
        not isinstance(observed, dict)
        or set(observed) != {"lean_toolchain"}
        or not isinstance(observed.get("lean_toolchain"), str)
        or not observed["lean_toolchain"].strip()
    ):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "raw dependency graph receipt must contain exactly one non-empty observed.lean_toolchain field",
        )

    authority_receipt = dict(receipt)
    authority_receipt.pop("observed")
    expected = {
        "schema": "theseus.raw-depgraph-receipt.v2",
        "source": {
            "repo": source_repo,
            "commit": source_commit,
            "subdir": source_subdir,
        },
        "scope": {"root_modules": list(root_modules)},
        "producer": {
            "kind": producer_kind,
            "tool_repo": producer_tool_repo,
            "tool_commit": producer_tool_commit,
            "tool_hash": producer_tool_hash,
        },
        "raw_depgraph": {"sha256": raw_hash},
    }
    if authority_receipt != expected:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "raw dependency graph receipt does not match source, producer, scope, or graph hash",
        )


def _load_raw_depgraph(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_ARTIFACT_INTEGRITY",
            f"invalid raw dependency graph: {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise RepoSearchError(
            "BLOCKED_ARTIFACT_INTEGRITY",
            "raw dependency graph must be a JSON object",
        )
    return data


def _cmd_build_artifact(args: argparse.Namespace) -> int:
    if args.authoritative_readback:
        _verify_authoritative_source(
            args.source_root,
            source_repo=args.source_repo,
            source_commit=args.source_commit,
            source_subdir=args.source_subdir,
        )
        if not args.lexical_only and args.raw_depgraph_receipt is None:
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                "authoritative exact mode requires a bound raw dependency graph receipt",
            )
    root_modules = tuple(args.root_module)
    if args.authoritative_readback and not args.lexical_only:
        _verify_raw_depgraph_receipt(
            args.raw_depgraph_receipt,
            args.raw_depgraph,
            source_repo=args.source_repo,
            source_commit=args.source_commit,
            source_subdir=args.source_subdir,
            root_modules=root_modules,
            producer_kind=args.producer_kind,
            producer_tool_repo=args.producer_tool_repo,
            producer_tool_commit=args.producer_tool_commit,
            producer_tool_hash=args.producer_tool_hash,
        )
    if args.lexical_only:
        nodes = []
        edges = []
        producer_kind = "lexical_only"
    else:
        raw = _load_raw_depgraph(args.raw_depgraph)
        nodes, edges = normalize_leandepviz(
            raw,
            source_commit=args.source_commit,
            root_modules=root_modules,
            producer_ref=f"{args.producer_tool_repo}@{args.producer_tool_commit}",
        )
        producer_kind = args.producer_kind

    sources = scan_lean_sources(
        args.source_root,
        source_commit=args.source_commit,
        tracked_only=args.authoritative_readback,
    )
    if nodes:
        nodes = bind_node_sources(nodes, sources)
    authority_receipt = None
    if args.authoritative_readback and not args.lexical_only:
        authority_receipt = args.raw_depgraph_receipt.read_bytes()
    manifest = write_artifact(
        args.out,
        nodes=nodes,
        edges=edges,
        sources=sources,
        source_repo=args.source_repo,
        source_commit=args.source_commit,
        source_subdir=args.source_subdir,
        producer=ProducerPin(
            kind=producer_kind,
            tool_repo=args.producer_tool_repo,
            tool_commit=args.producer_tool_commit,
            tool_hash=args.producer_tool_hash,
        ),
        scope=ArtifactScope(
            root_modules=root_modules,
            dependency_boundary="internal_only",
        ),
        created_from_authoritative_commit=args.authoritative_readback,
        authority_receipt=authority_receipt,
    )
    _emit(
        {
            "status": "BUILT",
            "artifact_identity": artifact_identity(manifest),
            "nodes": manifest.nodes_count,
            "edges": manifest.edges_count,
            "sources": len(sources),
            "out": str(args.out),
        }
    )
    return 0


def _cmd_verify_artifact(args: argparse.Namespace) -> int:
    manifest, nodes, edges, sources = load_artifact(args.artifact)
    _emit(
        {
            "status": "VERIFIED",
            "artifact_identity": artifact_identity(manifest),
            "nodes": len(nodes),
            "edges": len(edges),
            "sources": len(sources),
            "dependency_boundary": manifest.scope.dependency_boundary,
        }
    )
    return 0


def _cmd_build_index(args: argparse.Namespace) -> int:
    fingerprint = build_projection(args.artifact, args.db)
    _emit({"status": "BUILT", "fingerprint": fingerprint, "db": str(args.db)})
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    hits = search_repo(args.db, args.query, limit=args.limit)
    _emit(
        {
            "status": "FOUND" if hits else "UNKNOWN",
            "query": args.query,
            "hits": [_search_hit_dict(hit) for hit in hits],
        }
    )
    return 0


def _cmd_deps(args: argparse.Namespace) -> int:
    _emit(_graph_payload(dependencies(args.db, args.name, depth=args.depth)))
    return 0


def _cmd_rdeps(args: argparse.Namespace) -> int:
    _emit(_graph_payload(reverse_dependencies(args.db, args.name, depth=args.depth)))
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    _emit(
        _graph_payload(
            graph_path(args.db, args.source, args.target, max_depth=args.max_depth)
        )
    )
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    payload = build_context(
        args.db,
        args.name,
        depth=args.depth,
        token_budget=args.token_budget,
    )
    _emit({"status": "FOUND", **payload})
    return 0


def _add_build_artifact(subparsers) -> None:
    parser = subparsers.add_parser("build-artifact")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-subdir", required=True)
    parser.add_argument("--root-module", action="append", required=True)
    parser.add_argument("--producer-kind", required=True)
    parser.add_argument("--producer-tool-repo", required=True)
    parser.add_argument("--producer-tool-commit", required=True)
    parser.add_argument("--producer-tool-hash", required=True)
    parser.add_argument("--authoritative-readback", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--raw-depgraph", type=Path)
    mode.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--raw-depgraph-receipt", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.set_defaults(func=_cmd_build_artifact)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-search")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_build_artifact(subparsers)

    verify = subparsers.add_parser("verify-artifact")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.set_defaults(func=_cmd_verify_artifact)

    index = subparsers.add_parser("build-index")
    index.add_argument("--artifact", type=Path, required=True)
    index.add_argument("--db", type=Path, required=True)
    index.set_defaults(func=_cmd_build_index)

    search = subparsers.add_parser("search")
    search.add_argument("--db", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=_cmd_search)

    for command, handler in (("deps", _cmd_deps), ("rdeps", _cmd_rdeps)):
        graph = subparsers.add_parser(command)
        graph.add_argument("--db", type=Path, required=True)
        graph.add_argument("--name", required=True)
        graph.add_argument("--depth", type=int, default=1)
        graph.set_defaults(func=handler)

    path_parser = subparsers.add_parser("path")
    path_parser.add_argument("--db", type=Path, required=True)
    path_parser.add_argument("--source", required=True)
    path_parser.add_argument("--target", required=True)
    path_parser.add_argument("--max-depth", type=int, default=5)
    path_parser.set_defaults(func=_cmd_path)

    context_parser = subparsers.add_parser("context")
    context_parser.add_argument("--db", type=Path, required=True)
    context_parser.add_argument("--name", required=True)
    context_parser.add_argument("--depth", type=int, default=1)
    context_parser.add_argument("--token-budget", type=int, default=4000)
    context_parser.set_defaults(func=_cmd_context)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except RepoSearchError as exc:
        return _error(exc)
    except sqlite3.Error as exc:
        return _error(
            RepoSearchError(
                "UNAVAILABLE_PROJECTION",
                f"SQLite projection unavailable: {exc}",
            )
        )
