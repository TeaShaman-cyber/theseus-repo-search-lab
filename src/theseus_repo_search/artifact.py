from __future__ import annotations

import json
import os
import shutil
import tempfile
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

_DEPENDENCY_GRADE_BY_RELATION = {
    "type_dependency": EvidenceGrade.ELABORATED_TYPE_DEPENDENCY,
    "value_dependency": EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
}


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


def _write_artifact_contents(
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


def _publish_artifact_directory(staged: Path, out_dir: Path) -> None:
    if out_dir.exists():
        try:
            has_entries = any(out_dir.iterdir())
        except OSError as exc:
            raise _integrity(f"cannot inspect artifact output path: {out_dir}") from exc
        if has_entries:
            raise _integrity(
                "published artifacts are immutable; choose a new output path"
            )
    os.replace(staged, out_dir)


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
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=out_dir.parent)
    )
    try:
        manifest = _write_artifact_contents(
            staged,
            nodes=nodes,
            edges=edges,
            sources=sources,
            source_repo=source_repo,
            source_commit=source_commit,
            source_subdir=source_subdir,
            producer=producer,
            scope=scope,
            created_from_authoritative_commit=created_from_authoritative_commit,
        )
        load_artifact(staged)
        _publish_artifact_directory(staged, out_dir)
        return manifest
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)


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


def _require_str(data: dict[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _require_int(data: dict[str, object], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_str(data: dict[str, object], key: str) -> str | None:
    value = data[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value


def _optional_int(data: dict[str, object], key: str) -> int | None:
    value = data[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer or null")
    return value


def _node_from_dict(data: dict[str, object]) -> Node:
    return Node(
        id=_require_str(data, "id"),
        name=_require_str(data, "name"),
        kind=_require_str(data, "kind"),
        module=_require_str(data, "module"),
        source_path=_optional_str(data, "source_path"),
        source_start_line=_optional_int(data, "source_start_line"),
        source_end_line=_optional_int(data, "source_end_line"),
        source_commit=_require_str(data, "source_commit"),
    )


def _edge_from_dict(data: dict[str, object]) -> Edge:
    return Edge(
        source_id=_require_str(data, "source_id"),
        target_id=_require_str(data, "target_id"),
        relation=_require_str(data, "relation"),
        evidence_grade=EvidenceGrade(_require_str(data, "evidence_grade")),
        producer=_require_str(data, "producer"),
    )


def _source_from_dict(data: dict[str, object]) -> SourceChunk:
    return SourceChunk(
        id=_require_str(data, "id"),
        source_commit=_require_str(data, "source_commit"),
        source_path=_require_str(data, "source_path"),
        source_start_line=_require_int(data, "source_start_line"),
        source_end_line=_require_int(data, "source_end_line"),
        declaration_hint=_optional_str(data, "declaration_hint"),
        text=_require_str(data, "text"),
        content_sha256=_require_str(data, "content_sha256"),
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

    node_id_list = [node.id for node in nodes]
    if len(set(node_id_list)) != len(node_id_list):
        raise _integrity("duplicate node id")
    edge_keys = [
        (edge.source_id, edge.target_id, edge.relation, edge.producer)
        for edge in edges
    ]
    if len(set(edge_keys)) != len(edge_keys):
        raise _integrity("duplicate edge")
    source_ids = [chunk.id for chunk in sources]
    if len(set(source_ids)) != len(source_ids):
        raise _integrity("duplicate source id")

    if any(node.source_commit != manifest.source_commit for node in nodes):
        raise _integrity("node source commit mismatch")
    for node in nodes:
        if not any(
            node.module == root or node.module.startswith(f"{root}.")
            for root in manifest.scope.root_modules
        ):
            raise _integrity(
                f"node outside declared root-module scope: {node.id} ({node.module})"
            )
    if any(chunk.source_commit != manifest.source_commit for chunk in sources):
        raise _integrity("source chunk commit mismatch")
    for chunk in sources:
        if chunk.source_start_line < 1 or chunk.source_end_line < chunk.source_start_line:
            raise _integrity(f"invalid source range: {chunk.id}")
        if _sha256(chunk.text.encode("utf-8")) != chunk.content_sha256:
            raise _integrity(f"source chunk content hash mismatch: {chunk.id}")

    source_binding_counts: dict[tuple[str, int, int, str], int] = {}
    for chunk in sources:
        if chunk.declaration_hint is None:
            continue
        key = (
            chunk.source_path,
            chunk.source_start_line,
            chunk.source_end_line,
            chunk.declaration_hint,
        )
        source_binding_counts[key] = source_binding_counts.get(key, 0) + 1

    for node in nodes:
        location = (node.source_path, node.source_start_line, node.source_end_line)
        present = tuple(value is not None for value in location)
        if any(present) and not all(present):
            raise _integrity(f"partial node source location: {node.id}")
        if all(present):
            source_path = node.source_path
            source_start_line = node.source_start_line
            source_end_line = node.source_end_line
            if source_path is None or source_start_line is None or source_end_line is None:
                raise _integrity(f"partial node source location: {node.id}")
            if source_start_line < 1 or source_end_line < source_start_line:
                raise _integrity(f"invalid node source range: {node.id}")
            if sources:
                key = (
                    source_path,
                    source_start_line,
                    source_end_line,
                    node.name,
                )
                if source_binding_counts.get(key, 0) != 1:
                    raise _integrity(f"node source location mismatch: {node.id}")

    node_ids = set(node_id_list)
    for edge in edges:
        expected_grade = _DEPENDENCY_GRADE_BY_RELATION.get(edge.relation)
        if expected_grade is None:
            raise _integrity(f"unsupported dependency relation: {edge.relation}")
        if edge.evidence_grade is not expected_grade:
            raise _integrity(
                "relation/evidence mismatch: "
                f"{edge.relation} requires {expected_grade.value}, "
                f"observed {edge.evidence_grade.value}"
            )
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            raise _integrity("edge endpoint outside node set")

    return manifest, nodes, edges, sources
