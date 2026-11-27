from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from .accepted_catalog import (
    AcceptedCatalog,
    accepted_catalog_sha256,
    corpus_payload,
    load_accepted_catalog,
)
from .artifact import (
    artifact_identity,
    load_artifact,
    write_archive_artifact_v2,
    write_artifact,
)
from .errors import RepoSearchError
from .graph import GraphResult, dependencies, reverse_dependencies
from .graph import path as graph_path
from .kusto_query import KustoClient, expand_search_hits, federated_reach, reach
from .model import (
    ArchiveAuthority,
    ArtifactScope,
    Edge,
    EvidenceGrade,
    Node,
    ProducerPin,
)
from .multicorpus import ensure_local_catalog, ensure_local_corpus, search_local_catalog
from .normalize import normalize_leandepviz
from .overlap import catalog_overlap
from .producer_config import (
    LeanArchiveSource,
    LeanGitSource,
    load_lean_archive_source,
    load_lean_source,
)
from .projection import build_projection, read_projection_provenance
from .retrieval import SearchHit
from .retrieval import context as build_context
from .retrieval import search as search_repo
from .sharding import validate_shard_receipt_entries
from .sources import (
    bind_manifest_backed_node_sources,
    bind_node_sources,
    filter_manifest_backed_edges,
    scan_lean_sources,
    scan_manifest_backed_lean_sources,
)
from .turso_materializer import TursoHttpClient, TursoMaterializer
from .turso_query import connect_corpus, query_graph, search_turso_catalog


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
    if hit.source_kind == "git":
        data["source_commit"] = data.pop("source_revision")
        data["created_from_authoritative_commit"] = data.pop(
            "created_from_authoritative_source"
        )
        data.pop("source_kind", None)
        data.pop("source_authority", None)
    return data


def _compact_graph_scope(payload: dict[str, object]) -> dict[str, object]:
    # Exact module lists remain in the artifact. Avoid repeating a large closure
    # in every graph response, and make the presentation truncation explicit.
    roots = payload.get("scope_root_modules")
    if isinstance(roots, list) and len(roots) > 32:
        encoded = json.dumps(roots, ensure_ascii=False, separators=(",", ":")).encode()
        payload["scope_root_module_count"] = len(roots)
        payload["scope_root_modules_sha256"] = sha256(encoded).hexdigest()
        payload["scope_root_modules_truncated"] = True
        payload["scope_root_modules"] = roots[:32]
    return payload


def _graph_payload(result) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": ("FOUND" if result.found else "UNKNOWN") if result.complete_within_scope else "DEGRADED",
        "query": result.query,
        "found": result.found,
        "edges": list(result.edges),
        "scope_root_modules": list(result.scope_root_modules),
        "dependency_boundary": result.dependency_boundary,
        "complete_within_scope": result.complete_within_scope,
        "evidence_boundary": result.evidence_boundary,
    }
    if result.source_kind == "git":
        payload["created_from_authoritative_commit"] = (
            result.created_from_authoritative_source
        )
    else:
        payload.update(
            {
                "source_kind": result.source_kind,
                "source_revision": result.source_revision,
                "source_authority": result.source_authority,
                "created_from_authoritative_source": (
                    result.created_from_authoritative_source
                ),
            }
        )
    return _compact_graph_scope(payload)


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
    common = {
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
    schema = authority_receipt.get("schema")
    if schema == "theseus.raw-depgraph-receipt.v2":
        expected = {"schema": schema, **common}
        if authority_receipt != expected:
            raise RepoSearchError(
                "BLOCKED_SOURCE_MISMATCH",
                "raw dependency graph receipt does not match source, producer, scope, or graph hash",
            )
        return

    if schema != "theseus.raw-depgraph-receipt.v4":
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "raw dependency graph receipt does not match source, producer, scope, or graph hash",
        )
    if set(authority_receipt) != {"schema", *common, "shards"}:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "sharded raw dependency graph receipt has unexpected fields",
        )
    for key, value in common.items():
        if authority_receipt.get(key) != value:
            raise RepoSearchError(
                "BLOCKED_SOURCE_MISMATCH",
                "raw dependency graph receipt does not match source, producer, scope, or graph hash",
            )

    try:
        validate_shard_receipt_entries(authority_receipt.get("shards"))
    except ValueError as exc:
        raise RepoSearchError("BLOCKED_SOURCE_BINDING", str(exc)) from exc


def _verify_archive_raw_depgraph_receipt_bytes(
    receipt_bytes: bytes,
    raw_depgraph_bytes: bytes,
    *,
    source: LeanArchiveSource,
    tree_sha256: str,
    member_manifest_sha256: str,
    producer_kind: str,
    producer_tool_repo: str,
    producer_tool_commit: str,
    producer_tool_hash: str,
) -> None:
    try:
        receipt = json.loads(receipt_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"invalid archive raw dependency graph receipt: {exc}",
        ) from exc
    raw_hash = sha256(raw_depgraph_bytes).hexdigest()
    if not isinstance(receipt, dict):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "invalid archive raw dependency graph receipt: root must be an object",
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
            "archive raw dependency graph receipt must contain exactly one "
            "non-empty observed.lean_toolchain field",
        )

    authority_receipt = dict(receipt)
    authority_receipt.pop("observed")
    expected = {
        "schema": "theseus.raw-depgraph-receipt.v3",
        "source": {
            "kind": "archive",
            "url": source.archive_url,
            "sha256": source.archive_sha256,
            "format": source.archive_format,
            "subdir": source.source_subdir,
        },
        "materialization": {
            "tree_sha256": tree_sha256,
            "member_manifest_sha256": member_manifest_sha256,
        },
        "scope": {"root_modules": list(source.root_modules)},
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
            "archive raw dependency graph receipt does not match source, "
            "materialization, producer, scope, or graph hash",
        )


def _verify_archive_raw_depgraph_receipt(
    receipt_path: Path,
    raw_depgraph_path: Path,
    *,
    source: LeanArchiveSource,
    tree_sha256: str,
    member_manifest_sha256: str,
    producer_kind: str,
    producer_tool_repo: str,
    producer_tool_commit: str,
    producer_tool_hash: str,
) -> None:
    try:
        receipt_bytes = receipt_path.read_bytes()
        raw_depgraph_bytes = raw_depgraph_path.read_bytes()
    except OSError as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"cannot read archive raw dependency graph evidence: {exc}",
        ) from exc
    _verify_archive_raw_depgraph_receipt_bytes(
        receipt_bytes,
        raw_depgraph_bytes,
        source=source,
        tree_sha256=tree_sha256,
        member_manifest_sha256=member_manifest_sha256,
        producer_kind=producer_kind,
        producer_tool_repo=producer_tool_repo,
        producer_tool_commit=producer_tool_commit,
        producer_tool_hash=producer_tool_hash,
    )


def _archive_source_identity(source: LeanArchiveSource) -> dict[str, object]:
    return {
        "kind": "archive",
        "url": source.archive_url,
        "sha256": source.archive_sha256,
        "format": source.archive_format,
        "subdir": source.source_subdir,
    }


def _load_archive_materialization_evidence(
    receipt_path: Path,
    member_manifest_path: Path,
    *,
    source: LeanArchiveSource,
    materialization_root: Path,
) -> tuple[str, str]:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        member_manifest_bytes = member_manifest_path.read_bytes()
        member_manifest = json.loads(member_manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"invalid archive materialization evidence: {exc}",
        ) from exc
    if not isinstance(receipt, dict) or not isinstance(member_manifest, dict):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "archive materialization evidence roots must be objects",
        )
    expected_source = _archive_source_identity(source)
    if receipt.get("schema") != "theseus.archive-materialization-receipt.v1":
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "unsupported archive materialization receipt schema",
        )
    if receipt.get("source") != expected_source:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "archive materialization receipt source mismatch",
        )
    materialized = receipt.get("materialized")
    if not isinstance(materialized, dict):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "archive materialization receipt materialized section must be an object",
        )
    tree_sha256 = materialized.get("tree_sha256")
    member_manifest_sha256 = materialized.get("member_manifest_sha256")
    source_root_relative = materialized.get("source_root_relative")
    for label, digest in (
        ("tree_sha256", tree_sha256),
        ("member_manifest_sha256", member_manifest_sha256),
    ):
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                f"archive materialization receipt {label} must be SHA-256 hex",
            )
    expected_root = source.resolve_source_root(materialization_root.resolve())
    expected_relative = expected_root.relative_to(materialization_root.resolve()).as_posix() or "."
    if source_root_relative != expected_relative:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "archive materialization source-root mapping mismatch",
        )
    if sha256(member_manifest_bytes).hexdigest() != member_manifest_sha256:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "archive member manifest digest does not match materialization receipt",
        )
    if member_manifest.get("schema") != "theseus.archive-member-manifest.v1":
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "unsupported archive member manifest schema",
        )
    if member_manifest.get("source") != expected_source:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "archive member manifest source mismatch",
        )
    if member_manifest.get("source_root_relative") != expected_relative:
        raise RepoSearchError(
            "BLOCKED_SOURCE_MISMATCH",
            "archive member manifest source-root mapping mismatch",
        )
    if not isinstance(tree_sha256, str) or not isinstance(member_manifest_sha256, str):
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            "archive materialization digest types changed after validation",
        )
    return tree_sha256, member_manifest_sha256


def _load_raw_depgraph_bytes(data: bytes) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RepoSearchError(
            "BLOCKED_ARTIFACT_INTEGRITY",
            f"invalid raw dependency graph: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise RepoSearchError(
            "BLOCKED_ARTIFACT_INTEGRITY",
            "raw dependency graph must be a JSON object",
        )
    return value


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
    nodes: list[Node]
    edges: list[Edge]
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
        exclude_prefixes=tuple(args.exclude_source_prefix),
    )
    if nodes:
        nodes = bind_node_sources(nodes, sources)
    authority_receipt = None
    raw_depgraph_bytes = None
    if args.authoritative_readback and not args.lexical_only:
        authority_receipt = args.raw_depgraph_receipt.read_bytes()
        raw_depgraph_bytes = args.raw_depgraph.read_bytes()
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
            exclude_source_prefixes=tuple(args.exclude_source_prefix),
        ),
        created_from_authoritative_commit=args.authoritative_readback,
        authority_receipt=authority_receipt,
        raw_depgraph=raw_depgraph_bytes,
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


def _cmd_build_source_artifact(args: argparse.Namespace) -> int:
    source = load_lean_source(args.source)
    if isinstance(source, LeanGitSource):
        return _cmd_build_artifact(
            argparse.Namespace(
                source_root=args.source_root,
                source_repo=source.source_repo,
                source_commit=source.source_commit,
                source_subdir=source.source_subdir,
                root_module=list(source.root_modules),
                exclude_source_prefix=list(source.exclude_source_prefixes),
                producer_kind=args.producer_kind,
                producer_tool_repo=args.producer_tool_repo,
                producer_tool_commit=args.producer_tool_commit,
                producer_tool_hash=args.producer_tool_hash,
                authoritative_readback=True,
                raw_depgraph=args.raw_depgraph,
                lexical_only=False,
                raw_depgraph_receipt=args.raw_depgraph_receipt,
                out=args.out,
            )
        )
    if isinstance(source, LeanArchiveSource):
        if args.materialization_receipt is None or args.member_manifest is None:
            raise RepoSearchError(
                "BLOCKED_SOURCE_BINDING",
                "archive artifact build requires materialization receipt and member manifest",
            )
        return _cmd_build_archive_artifact(
            argparse.Namespace(
                source=args.source,
                materialization_root=args.source_container,
                materialization_receipt=args.materialization_receipt,
                member_manifest=args.member_manifest,
                raw_depgraph=args.raw_depgraph,
                raw_depgraph_receipt=args.raw_depgraph_receipt,
                producer_kind=args.producer_kind,
                producer_tool_repo=args.producer_tool_repo,
                producer_tool_commit=args.producer_tool_commit,
                producer_tool_hash=args.producer_tool_hash,
                out=args.out,
            )
        )
    raise TypeError("unsupported Lean source descriptor type")


def _cmd_build_archive_artifact(args: argparse.Namespace) -> int:
    source = load_lean_archive_source(args.source)
    materialization_root = args.materialization_root.resolve()
    source_root = source.resolve_source_root(materialization_root)
    tree_sha256, member_manifest_sha256 = _load_archive_materialization_evidence(
        args.materialization_receipt,
        args.member_manifest,
        source=source,
        materialization_root=materialization_root,
    )
    try:
        authority_receipt = args.raw_depgraph_receipt.read_bytes()
        raw_depgraph_bytes = args.raw_depgraph.read_bytes()
    except OSError as exc:
        raise RepoSearchError(
            "BLOCKED_SOURCE_BINDING",
            f"cannot read archive raw dependency graph evidence: {exc}",
        ) from exc
    _verify_archive_raw_depgraph_receipt_bytes(
        authority_receipt,
        raw_depgraph_bytes,
        source=source,
        tree_sha256=tree_sha256,
        member_manifest_sha256=member_manifest_sha256,
        producer_kind=args.producer_kind,
        producer_tool_repo=args.producer_tool_repo,
        producer_tool_commit=args.producer_tool_commit,
        producer_tool_hash=args.producer_tool_hash,
    )
    raw = _load_raw_depgraph_bytes(raw_depgraph_bytes)
    nodes, edges = normalize_leandepviz(
        raw,
        source_commit=source.archive_sha256,
        root_modules=source.root_modules,
        producer_ref=f"{args.producer_tool_repo}@{args.producer_tool_commit}",
    )
    sources = scan_manifest_backed_lean_sources(
        source_root,
        source_revision=source.archive_sha256,
        member_manifest_path=args.member_manifest,
        expected_manifest_sha256=member_manifest_sha256,
        exclude_prefixes=source.exclude_source_prefixes,
    )
    nodes = bind_manifest_backed_node_sources(nodes, sources)
    edges = filter_manifest_backed_edges(edges, nodes)
    manifest = write_archive_artifact_v2(
        args.out,
        nodes=nodes,
        edges=edges,
        sources=sources,
        source_authority=ArchiveAuthority(
            url=source.archive_url,
            sha256=source.archive_sha256,
            format=source.archive_format,
            subdir=source.source_subdir,
        ),
        producer=ProducerPin(
            kind=args.producer_kind,
            tool_repo=args.producer_tool_repo,
            tool_commit=args.producer_tool_commit,
            tool_hash=args.producer_tool_hash,
        ),
        scope=ArtifactScope(
            root_modules=source.root_modules,
            dependency_boundary="internal_only",
            exclude_source_prefixes=source.exclude_source_prefixes,
        ),
        created_from_authoritative_source=True,
        authority_receipt=authority_receipt,
        raw_depgraph=raw_depgraph_bytes,
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


def _multicorpus_status(result, *, mode: str) -> str:
    if result.unavailable_corpora:
        if result.searched_corpora == 0:
            return "UNAVAILABLE"
        return "DEGRADED"
    if not result.hits:
        return "UNKNOWN"
    if mode == "evidence" or all(item.hit.match_mode == "exact" for item in result.hits):
        return "FOUND"
    return "CANDIDATE"


def _turso_platform_token(args: argparse.Namespace) -> str | None:
    if args.platform_token_env is None:
        return None
    token = os.environ.get(args.platform_token_env)
    if not token:
        raise RepoSearchError(
            "BLOCKED_TURSO_PLATFORM_AUTH",
            f"environment variable is empty or missing: {args.platform_token_env}",
        )
    return token


def _turso_client(args: argparse.Namespace) -> TursoHttpClient:
    return TursoHttpClient(
        organization=args.organization,
        platform_base_url=args.platform_base_url,
        platform_token=_turso_platform_token(args),
    )


def _cmd_materialize_turso(args: argparse.Namespace) -> int:
    try:
        catalog = load_accepted_catalog(args.catalog)
    except (OSError, TypeError, ValueError) as exc:
        raise RepoSearchError(
            "BLOCKED_ACCEPTED_CATALOG",
            f"accepted corpus catalog invalid: {exc}",
        ) from exc

    item = next((c for c in catalog.corpora if c.source_id == args.source_id), None)
    if item is not None and item.split_projection_sha256 is not None:
        raise RepoSearchError("BLOCKED_PROJECTION_PROFILE", "split projection requires its explicit materializer")
    repository_root = args.catalog.resolve().parent.parent
    state = ensure_local_corpus(
        catalog,
        args.source_id,
        repository_root=repository_root,
        cache_root=args.cache_dir,
    )
    client = _turso_client(args)
    result = TursoMaterializer(client).materialize(
        state,
        group=args.group,
        database=args.database,
        size_limit=args.size_limit,
        token_expiration=args.token_expiration,
    )
    _emit(asdict(result))
    return 0


def _cmd_sync_turso_catalog(args: argparse.Namespace) -> int:
    try:
        catalog = load_accepted_catalog(args.catalog)
    except (OSError, TypeError, ValueError) as exc:
        raise RepoSearchError(
            "BLOCKED_ACCEPTED_CATALOG",
            f"accepted corpus catalog invalid: {exc}",
        ) from exc

    repository_root = args.catalog.resolve().parent.parent
    materializer = TursoMaterializer(_turso_client(args))
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    for corpus in catalog.corpora:
        try:
            if corpus.split_projection_sha256 is not None:
                raise RepoSearchError("BLOCKED_PROJECTION_PROFILE", "split projection requires its explicit materializer")
            state = ensure_local_corpus(
                catalog,
                corpus.source_id,
                repository_root=repository_root,
                cache_root=args.cache_dir,
            )
            result = materializer.materialize(
                state,
                group=args.group,
                size_limit=args.size_limit,
                token_expiration=args.token_expiration,
            )
        except RepoSearchError as exc:
            failures.append(
                {
                    "source_id": corpus.source_id,
                    "code": exc.code,
                    "message": str(exc),
                }
            )
            continue
        results.append(asdict(result))

    _emit(
        {
            "status": "READY" if not failures else "DEGRADED",
            "catalog_sha256": accepted_catalog_sha256(catalog),
            "total_corpora": len(catalog.corpora),
            "synced_corpora": len(results),
            "failed_corpora": len(failures),
            "failures": failures,
            "results": results,
        }
    )
    return 0 if not failures else 2


def _cmd_multicorpus_search(args: argparse.Namespace) -> int:
    try:
        catalog = load_accepted_catalog(args.catalog)
    except (OSError, TypeError, ValueError) as exc:
        raise RepoSearchError(
            "BLOCKED_ACCEPTED_CATALOG",
            f"accepted corpus catalog invalid: {exc}",
        ) from exc

    repository_root = args.catalog.resolve().parent.parent
    states, unavailable = ensure_local_catalog(
        catalog,
        repository_root=repository_root,
        cache_root=args.cache_dir,
    )
    result = search_local_catalog(
        states,
        unavailable,
        catalog_sha256=accepted_catalog_sha256(catalog),
        query=args.query,
        limit=args.limit,
        mode=args.mode,
    )
    return _emit_multicorpus_result(args, result)


def _cmd_turso_search(args: argparse.Namespace) -> int:
    try:
        catalog = load_accepted_catalog(args.catalog)
    except (OSError, TypeError, ValueError) as exc:
        raise RepoSearchError("BLOCKED_ACCEPTED_CATALOG", f"accepted corpus catalog invalid: {exc}") from exc
    result = search_turso_catalog(catalog, _turso_client(args), query=args.query, limit=args.limit, mode=args.mode)
    return _emit_multicorpus_result(args, result)


def _cmd_turso_graph(args: argparse.Namespace, *, catalog: AcceptedCatalog | None = None) -> int:
    if catalog is None:
        try:
            catalog = load_accepted_catalog(args.catalog)
        except (OSError, TypeError, ValueError) as exc:
            raise RepoSearchError("BLOCKED_ACCEPTED_CATALOG", f"accepted corpus catalog invalid: {exc}") from exc
    item = next((c for c in catalog.corpora if c.source_id == args.source_id), None)
    if item is None:
        raise RepoSearchError("BLOCKED_ACCEPTED_CATALOG", "source not in accepted catalog")
    if item.split_projection_sha256 is not None:
        raise RepoSearchError("UNAVAILABLE_GRAPH_BACKEND", "split graph query route not configured")
    conn = connect_corpus(item, _turso_client(args))
    result = query_graph(conn, args.operation, args.name, target=args.target, depth=args.depth,
                         max_edges=args.max_edges, token_budget=args.token_budget)
    if isinstance(result, GraphResult):
        payload = _graph_payload(result)
        payload["found"] = result.found
        payload["source_revision"] = result.source_revision
    else:
        payload = result
        payload["status"] = "FOUND"
        provenance = read_projection_provenance(conn)
        payload["source_revision"] = provenance.source_revision
        payload["source_kind"] = provenance.source_kind
    if not payload["complete_within_scope"]:
        payload["status"] = "DEGRADED"
    _emit(_compact_graph_scope({**payload, "source_id": item.source_id, "artifact_identity": item.artifact_identity,
           "catalog_sha256": accepted_catalog_sha256(catalog)}))
    return 0


def _multicorpus_payload(args: argparse.Namespace, result, *, catalog: AcceptedCatalog | None = None) -> dict[str, object]:
    identities = {item.source_id: item.artifact_identity for item in catalog.corpora} if catalog is not None else {}
    return {
        "status": _multicorpus_status(result, mode=args.mode),
        "query": args.query,
        "query_mode": args.mode,
        "catalog_sha256": result.catalog_sha256,
        "searched_corpora": result.searched_corpora,
        **({"source_filter": args.source_id, "backend": "turso"} if catalog is not None else {}),
        "unavailable_corpora": len(result.unavailable_corpora),
        "unavailable": [asdict(item) for item in result.unavailable_corpora],
        "hits": [
            {
                "candidate_id": item.candidate_id,
                "source_id": item.source_id,
                "local_rank": item.local_rank,
                **({"artifact_identity": identities[item.source_id]} if catalog is not None else {}),
                **_search_hit_dict(item.hit),
            }
            for item in result.hits
        ],
    }


def _emit_multicorpus_result(args: argparse.Namespace, result, *, catalog: AcceptedCatalog | None = None) -> int:
    _emit(_multicorpus_payload(args, result, catalog=catalog))
    return 0


def _agent_kusto_client() -> KustoClient:
    cluster = os.environ.get("REPO_SEARCH_KUSTO_CLUSTER")
    database = os.environ.get("REPO_SEARCH_KUSTO_DATABASE")
    if not cluster or not database:
        raise RepoSearchError("BLOCKED_QUERY_PROFILE", "configure Kusto cluster and database")
    return KustoClient(cluster, database)


def _cmd_agent_query(args: argparse.Namespace) -> int:
    catalog_path = args.catalog or os.environ.get("REPO_SEARCH_CATALOG")
    if not catalog_path:
        raise RepoSearchError("BLOCKED_QUERY_PROFILE", "set REPO_SEARCH_CATALOG or --catalog")
    args.catalog = Path(catalog_path)
    try:
        catalog = load_accepted_catalog(args.catalog)
    except (OSError, TypeError, ValueError) as exc:
        raise RepoSearchError("BLOCKED_ACCEPTED_CATALOG", "accepted corpus catalog invalid") from exc
    if args.operation == "corpora":
        _emit({"status": "FOUND", "availability": "NOT_CHECKED",
               "catalog_sha256": accepted_catalog_sha256(catalog),
               "release_repository": catalog.release_repository,
               "corpora": [corpus_payload(item) for item in catalog.corpora]})
        return 0
    if args.operation in ("reach", "reach-all"):
        item = next((c for c in catalog.corpora if c.source_id == getattr(args, "source_id", None)), None)
        if args.operation == "reach" and item is None:
            raise RepoSearchError("BLOCKED_ACCEPTED_CATALOG", "source not in accepted catalog")
        client = _agent_kusto_client()
        if item is None:
            payload = federated_reach(catalog, client, args.name, depth=args.depth, limit=args.limit, reverse=args.reverse)
        else:
            payload = reach(item, client, args.name, depth=args.depth, limit=args.limit, reverse=args.reverse)
        _emit({**payload, "catalog_sha256": accepted_catalog_sha256(catalog)})
        return 0
    args.organization = args.organization or os.environ.get("REPO_SEARCH_TURSO_ORGANIZATION")
    if not args.organization:
        raise RepoSearchError("BLOCKED_QUERY_PROFILE", "set REPO_SEARCH_TURSO_ORGANIZATION or --organization")
    args.platform_base_url = args.platform_base_url or os.environ.get(
        "REPO_SEARCH_TURSO_PLATFORM_URL", "https://api.turso.tech"
    )
    args.platform_token_env = args.platform_token_env or os.environ.get("REPO_SEARCH_TURSO_TOKEN_ENV")
    if args.operation == "overlap":
        _emit(catalog_overlap(catalog, _turso_client(args), limit=args.limit,
                              occurrence_limit=args.occurrences, raw=args.raw))
        return 0
    if args.operation in ("search", "graph-search"):
        if args.operation == "graph-search" and (not 1 <= args.limit <= 10 or not 1 <= args.depth <= 3 or not 1 <= args.graph_limit <= 50):
            raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "graph-search: limit 1..10, depth 1..3, graph-limit 1..50")
        result = search_turso_catalog(catalog, _turso_client(args), query=args.query,
                                      limit=args.limit, mode=args.mode, source_id=args.source_id)
        if args.operation == "graph-search":
            payload = _multicorpus_payload(args, result, catalog=catalog)
            # Preserve lexical evidence even when the graph route is unavailable.
            if not result.hits:
                graph = {"graph_status": "NOT_RUN", "graph_seeds": 0, "graphs": [],
                         "graph_unavailable": []}
            else:
                try:
                    graph = expand_search_hits(catalog, _agent_kusto_client(), result.hits,
                                               depth=args.depth, limit=args.graph_limit, reverse=args.reverse)
                except RepoSearchError as exc:
                    graph = {"graph_status": "UNAVAILABLE", "graphs": [],
                             "graph_unavailable": [{"code": exc.code, "message": str(exc)}]}
            lexical_status = payload["status"]
            graph_status = graph["graph_status"]
            if result.hits and graph_status != "FOUND":
                payload["status"] = "DEGRADED"
            _emit({**payload, **graph, "lexical_status": lexical_status, "backend": "turso+kusto"})
            return 0
        return _emit_multicorpus_result(args, result, catalog=catalog)
    return _cmd_turso_graph(args, catalog=catalog)


def _add_agent_query(subparsers) -> None:
    parser = subparsers.add_parser("query", help="unified read-only accepted-corpus interface")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--organization")
    parser.add_argument("--platform-base-url")
    parser.add_argument("--platform-token-env")
    parser.set_defaults(func=_cmd_agent_query)
    ops = parser.add_subparsers(dest="operation", required=True)
    ops.add_parser("corpora", help="list catalog identities (not live availability)")
    overlap = ops.add_parser("overlap", help="cross-corpus short-name candidates with noise filtering")
    overlap.add_argument("--limit", type=int, default=20)
    overlap.add_argument("--occurrences", type=int, default=10)
    overlap.add_argument("--raw", action="store_true")
    for operation in ("search", "graph-search"):
        search = ops.add_parser(operation)
        search.add_argument("query")
        search.add_argument("--source", dest="source_id")
        search.add_argument("--limit", type=int, default=10 if operation == "search" else 5)
        search.add_argument("--mode", choices=("discovery", "evidence"), default="discovery")
        if operation == "graph-search":
            search.add_argument("--depth", type=int, default=2)
            search.add_argument("--graph-limit", type=int, default=30)
            search.add_argument("--reverse", action="store_true")
    for operation in ("reach", "reach-all"):
        graph_reach = ops.add_parser(operation, help="Kusto bounded graph reachability")
        if operation == "reach":
            graph_reach.add_argument("source_id")
        graph_reach.add_argument("name")
        graph_reach.add_argument("--depth", type=int, default=3)
        graph_reach.add_argument("--limit", type=int, default=100)
        graph_reach.add_argument("--reverse", action="store_true")
    for op in ("deps", "rdeps", "path", "context"):
        graph = ops.add_parser(op)
        graph.add_argument("source_id")
        graph.add_argument("name")
        if op == "path":
            graph.add_argument("target")
        else:
            graph.set_defaults(target=None)
        graph.add_argument("--depth", type=int, default=5 if op == "path" else 1)
        graph.add_argument("--max-edges", type=int, default=200)
        if op == "context":
            graph.add_argument("--token-budget", type=int, default=4000)
        else:
            graph.set_defaults(token_budget=4000)


def _cmd_search(args: argparse.Namespace) -> int:
    hits = search_repo(args.db, args.query, limit=args.limit, mode=args.mode)
    if not hits:
        status = "UNKNOWN"
    elif args.mode == "evidence" or all(hit.match_mode == "exact" for hit in hits):
        status = "FOUND"
    else:
        status = "CANDIDATE"
    _emit(
        {
            "status": status,
            "query": args.query,
            "query_mode": args.mode,
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
    _emit(_compact_graph_scope({"status": "FOUND" if payload["complete_within_scope"] else "DEGRADED", **payload}))
    return 0


def _add_build_artifact(subparsers) -> None:
    parser = subparsers.add_parser("build-artifact")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-subdir", required=True)
    parser.add_argument("--root-module", action="append", required=True)
    parser.add_argument("--exclude-source-prefix", action="append", default=[])
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


def _add_build_source_artifact(subparsers) -> None:
    parser = subparsers.add_parser("build-source-artifact")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-container", type=Path, required=True)
    parser.add_argument("--materialization-receipt", type=Path)
    parser.add_argument("--member-manifest", type=Path)
    parser.add_argument("--raw-depgraph", type=Path, required=True)
    parser.add_argument("--raw-depgraph-receipt", type=Path, required=True)
    parser.add_argument("--producer-kind", required=True)
    parser.add_argument("--producer-tool-repo", required=True)
    parser.add_argument("--producer-tool-commit", required=True)
    parser.add_argument("--producer-tool-hash", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.set_defaults(func=_cmd_build_source_artifact)


def _add_build_archive_artifact(subparsers) -> None:
    parser = subparsers.add_parser("build-archive-artifact")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--materialization-root", type=Path, required=True)
    parser.add_argument("--materialization-receipt", type=Path, required=True)
    parser.add_argument("--member-manifest", type=Path, required=True)
    parser.add_argument("--raw-depgraph", type=Path, required=True)
    parser.add_argument("--raw-depgraph-receipt", type=Path, required=True)
    parser.add_argument("--producer-kind", required=True)
    parser.add_argument("--producer-tool-repo", required=True)
    parser.add_argument("--producer-tool-commit", required=True)
    parser.add_argument("--producer-tool-hash", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.set_defaults(func=_cmd_build_archive_artifact)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-search")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_agent_query(subparsers)
    _add_build_artifact(subparsers)
    _add_build_source_artifact(subparsers)
    _add_build_archive_artifact(subparsers)

    verify = subparsers.add_parser("verify-artifact")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.set_defaults(func=_cmd_verify_artifact)

    index = subparsers.add_parser("build-index")
    index.add_argument("--artifact", type=Path, required=True)
    index.add_argument("--db", type=Path, required=True)
    index.set_defaults(func=_cmd_build_index)

    turso = subparsers.add_parser("materialize-turso")
    turso.add_argument("--catalog", type=Path, required=True)
    turso.add_argument("--cache-dir", type=Path, required=True)
    turso.add_argument("--source-id", required=True)
    turso.add_argument("--organization", required=True)
    turso.add_argument("--group", required=True)
    turso.add_argument("--database")
    turso.add_argument("--size-limit")
    turso.add_argument("--token-expiration", default="15m")
    turso.add_argument(
        "--platform-base-url",
        default="https://api.turso.tech",
    )
    turso.add_argument(
        "--platform-token-env",
        help="environment variable containing the Turso Platform API token",
    )
    turso.set_defaults(func=_cmd_materialize_turso)

    turso_sync = subparsers.add_parser("sync-turso-catalog")
    turso_sync.add_argument("--catalog", type=Path, required=True)
    turso_sync.add_argument("--cache-dir", type=Path, required=True)
    turso_sync.add_argument("--organization", required=True)
    turso_sync.add_argument("--group", required=True)
    turso_sync.add_argument("--size-limit")
    turso_sync.add_argument("--token-expiration", default="15m")
    turso_sync.add_argument(
        "--platform-base-url",
        default="https://api.turso.tech",
    )
    turso_sync.add_argument(
        "--platform-token-env",
        help="environment variable containing the Turso Platform API token",
    )
    turso_sync.set_defaults(func=_cmd_sync_turso_catalog)

    multicorpus_search = subparsers.add_parser("multicorpus-search")
    multicorpus_search.add_argument("--catalog", type=Path, required=True)
    multicorpus_search.add_argument("--cache-dir", type=Path, required=True)
    multicorpus_search.add_argument("--query", required=True)
    multicorpus_search.add_argument("--limit", type=int, default=20)
    multicorpus_search.add_argument(
        "--mode", choices=("discovery", "evidence"), default="discovery"
    )
    multicorpus_search.set_defaults(func=_cmd_multicorpus_search)

    turso_search = subparsers.add_parser("turso-search")
    turso_search.add_argument("--catalog", type=Path, required=True)
    turso_search.add_argument("--organization", required=True)
    turso_search.add_argument("--platform-base-url", default="https://api.turso.tech")
    turso_search.add_argument("--platform-token-env")
    turso_search.add_argument("--query", required=True)
    turso_search.add_argument("--limit", type=int, default=20)
    turso_search.add_argument("--mode", choices=("discovery", "evidence"), default="discovery")
    turso_search.set_defaults(func=_cmd_turso_search)

    turso_graph = subparsers.add_parser("turso-graph")
    turso_graph.add_argument("--catalog", type=Path, required=True)
    turso_graph.add_argument("--organization", required=True)
    turso_graph.add_argument("--platform-base-url", default="https://api.turso.tech")
    turso_graph.add_argument("--platform-token-env")
    turso_graph.add_argument("--source-id", required=True)
    turso_graph.add_argument("--operation", choices=("deps", "rdeps", "path", "context"), required=True)
    turso_graph.add_argument("--name", required=True)
    turso_graph.add_argument("--target")
    turso_graph.add_argument("--depth", type=int, default=1)
    turso_graph.add_argument("--max-edges", type=int, default=200)
    turso_graph.add_argument("--token-budget", type=int, default=4000)
    turso_graph.set_defaults(func=_cmd_turso_graph)

    search = subparsers.add_parser("search")
    search.add_argument("--db", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--mode", choices=("discovery", "evidence"), default="discovery")
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
