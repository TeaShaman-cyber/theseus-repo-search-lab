from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .artifact import artifact_identity, load_artifact, write_artifact
from .errors import RepoSearchError
from .graph import dependencies, path as graph_path, reverse_dependencies
from .model import ArtifactScope, EvidenceGrade, ProducerPin
from .normalize import normalize_leandepviz
from .projection import build_projection
from .retrieval import SearchHit, context as build_context, search as search_repo
from .sources import scan_lean_sources


def _emit(payload: dict[str, object], *, stream=sys.stdout) -> None:
    stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


def _error(exc: RepoSearchError) -> int:
    _emit(
        {"status": "ERROR", "code": exc.code, "message": str(exc)},
        stream=sys.stderr,
    )
    return 2


def _search_hit_dict(hit: SearchHit) -> dict[str, object]:
    data = asdict(hit)
    grade = data["evidence_grade"]
    if isinstance(grade, EvidenceGrade):
        data["evidence_grade"] = grade.value
    return data


def _graph_payload(result) -> dict[str, object]:
    return {
        "status": "FOUND",
        "query": result.query,
        "edges": list(result.edges),
        "scope_root_modules": list(result.scope_root_modules),
        "dependency_boundary": result.dependency_boundary,
        "complete_within_scope": result.complete_within_scope,
    }


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
    root_modules = tuple(args.root_module)
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

    sources = scan_lean_sources(args.source_root, source_commit=args.source_commit)
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
