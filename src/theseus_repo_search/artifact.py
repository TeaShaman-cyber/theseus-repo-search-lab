from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Sequence

from .errors import RepoSearchError
from .model import (
    ArtifactManifest,
    ArtifactScope,
    Edge,
    EvidenceGrade,
    Node,
    ProducerPin,
    SourceChunk,
)


SCHEMA = "theseus.repo-index.v1"


def _json_line(data: dict[str, object]) -> bytes:
    return (
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _canonical_json(data: dict[str, object]) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return sha256(data).hexdigest()


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> str:
    data = b"".join(_json_line(row) for row in rows)
    path.write_bytes(data)
    return _sha256(data)


def artifact_identity(manifest: ArtifactManifest) -> str:
    payload: dict[str, object] = {
        "schema": manifest.schema,
        "source": {
            "repo": manifest.source_repo,
            "commit": manifest.source_commit,
            "subdir": manifest.source_subdir,
        },
        "producer": {
            "kind": manifest.producer.kind,
            "tool_repo": manifest.producer.tool_repo,
            "tool_commit": manifest.producer.tool_commit,
            "tool_hash": manifest.producer.tool_hash,
        },
        "scope": {
            "root_modules": list(manifest.scope.root_modules),
            "dependency_boundary": manifest.scope.dependency_boundary,
        },
        "members": {
            "nodes": {"sha256": manifest.nodes_sha256},
            "edges": {"sha256": manifest.edges_sha256},
            "sources": {"sha256": manifest.sources_sha256},
        },
    }
    return _sha256(_canonical_json(payload))


def write_artifact(
    out_dir: Path,
    *,
    nodes: Sequence[Node],
    edges: Sequence[Edge],
    sources: Sequence[SourceChunk] | None,
    source_repo: str,
    source_commit: str,
    source_subdir: str,
    producer: ProducerPin,
    scope: ArtifactScope,
    created_from_authoritative_commit: bool,
) -> ArtifactManifest:
    out_dir.mkdir(parents=True, exist_ok=True)

    sorted_nodes = sorted(nodes, key=lambda node: node.id)
    sorted_edges = sorted(
        edges,
        key=lambda edge: (
            edge.source_id,
            edge.target_id,
            edge.relation,
            edge.producer,
        ),
    )
    sorted_sources = None
    if sources is not None:
        sorted_sources = sorted(
            sources,
            key=lambda chunk: (chunk.source_path, chunk.source_start_line, chunk.id),
        )

    nodes_sha256 = _write_jsonl(
        out_dir / "nodes.jsonl", (node.to_dict() for node in sorted_nodes)
    )
    edges_sha256 = _write_jsonl(
        out_dir / "edges.jsonl", (edge.to_dict() for edge in sorted_edges)
    )

    sources_path = out_dir / "sources.jsonl"
    if sorted_sources is None:
        if sources_path.exists():
            sources_path.unlink()
        sources_sha256 = None
    else:
        sources_sha256 = _write_jsonl(
            sources_path, (chunk.to_dict() for chunk in sorted_sources)
        )

    manifest = ArtifactManifest(
        schema=SCHEMA,
        source_repo=source_repo,
        source_commit=source_commit,
        source_subdir=source_subdir,
        producer=producer,
        scope=scope,
        nodes_sha256=nodes_sha256,
        edges_sha256=edges_sha256,
        sources_sha256=sources_sha256,
        nodes_count=len(sorted_nodes),
        edges_count=len(sorted_edges),
        created_from_authoritative_commit=created_from_authoritative_commit,
    )
    (out_dir / "manifest.json").write_bytes(_json_line(manifest.to_dict()))
    return manifest


def _integrity(message: str) -> RepoSearchError:
    return RepoSearchError("BLOCKED_ARTIFACT_INTEGRITY", message)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("JSONL row is not an object")
            rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _integrity(f"invalid artifact member {path.name}: {exc}") from exc
    return rows


def _node_from_dict(data: dict[str, object]) -> Node:
    return Node(
        id=str(data["id"]),
        name=str(data["name"]),
        kind=str(data["kind"]),
        module=str(data["module"]),
        source_path=(None if data["source_path"] is None else str(data["source_path"])),
        source_start_line=(
            None if data["source_start_line"] is None else int(data["source_start_line"])
        ),
        source_end_line=(
            None if data["source_end_line"] is None else int(data["source_end_line"])
        ),
        source_commit=str(data["source_commit"]),
    )


def _edge_from_dict(data: dict[str, object]) -> Edge:
    return Edge(
        source_id=str(data["source_id"]),
        target_id=str(data["target_id"]),
        relation=str(data["relation"]),
        evidence_grade=EvidenceGrade(str(data["evidence_grade"])),
        producer=str(data["producer"]),
    )


def _source_from_dict(data: dict[str, object]) -> SourceChunk:
    return SourceChunk(
        id=str(data["id"]),
        source_commit=str(data["source_commit"]),
        source_path=str(data["source_path"]),
        source_start_line=int(data["source_start_line"]),
        source_end_line=int(data["source_end_line"]),
        declaration_hint=(
            None if data["declaration_hint"] is None else str(data["declaration_hint"])
        ),
        text=str(data["text"]),
        content_sha256=str(data["content_sha256"]),
    )


def load_artifact(
    path: Path,
) -> tuple[ArtifactManifest, list[Node], list[Edge], list[SourceChunk]]:
    try:
        manifest_data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest_data, dict):
            raise ValueError("manifest is not an object")
        manifest = ArtifactManifest.from_dict(manifest_data)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, AssertionError) as exc:
        raise _integrity(f"invalid manifest: {exc}") from exc

    if manifest.schema != SCHEMA:
        raise _integrity(f"unsupported schema: {manifest.schema}")
    if manifest.scope.dependency_boundary != "internal_only":
        raise _integrity(
            f"unsupported dependency boundary: {manifest.scope.dependency_boundary}"
        )

    members = (
        ("nodes.jsonl", manifest.nodes_sha256),
        ("edges.jsonl", manifest.edges_sha256),
    )
    for filename, expected_hash in members:
        member_path = path / filename
        try:
            actual_hash = _sha256(member_path.read_bytes())
        except OSError as exc:
            raise _integrity(f"missing artifact member: {filename}") from exc
        if actual_hash != expected_hash:
            raise _integrity(f"hash mismatch for {filename}")

    sources_path = path / "sources.jsonl"
    if manifest.sources_sha256 is None:
        if sources_path.exists():
            raise _integrity("sources.jsonl present but manifest marks it absent")
    else:
        try:
            actual_sources_hash = _sha256(sources_path.read_bytes())
        except OSError as exc:
            raise _integrity("missing artifact member: sources.jsonl") from exc
        if actual_sources_hash != manifest.sources_sha256:
            raise _integrity("hash mismatch for sources.jsonl")

    try:
        nodes = [_node_from_dict(row) for row in _read_jsonl(path / "nodes.jsonl")]
        edges = [_edge_from_dict(row) for row in _read_jsonl(path / "edges.jsonl")]
        sources = [] if manifest.sources_sha256 is None else [
            _source_from_dict(row) for row in _read_jsonl(sources_path)
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise _integrity(f"invalid artifact record: {exc}") from exc

    if len(nodes) != manifest.nodes_count:
        raise _integrity("node count mismatch")
    if len(edges) != manifest.edges_count:
        raise _integrity("edge count mismatch")
    if any(node.source_commit != manifest.source_commit for node in nodes):
        raise _integrity("node source commit mismatch")

    node_ids = {node.id for node in nodes}
    for edge in edges:
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            raise _integrity("edge endpoint outside node set")

    return manifest, nodes, edges, sources
