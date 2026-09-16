from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceGrade(str, Enum):
    ELABORATED_VALUE_DEPENDENCY = "ELABORATED_VALUE_DEPENDENCY"
    ELABORATED_TYPE_DEPENDENCY = "ELABORATED_TYPE_DEPENDENCY"
    STATIC_REFERENCE = "STATIC_REFERENCE"
    LEXICAL_HIT = "LEXICAL_HIT"


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    kind: str
    module: str
    source_path: str | None
    source_start_line: int | None
    source_end_line: int | None
    source_commit: str

    @classmethod
    def from_lean(
        cls,
        *,
        full_name: str,
        name: str,
        kind: str,
        module: str,
        source_commit: str,
    ) -> "Node":
        return cls(
            id=f"lean:{full_name}",
            name=name,
            kind=kind,
            module=module,
            source_path=None,
            source_start_line=None,
            source_end_line=None,
            source_commit=source_commit,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "module": self.module,
            "source_path": self.source_path,
            "source_start_line": self.source_start_line,
            "source_end_line": self.source_end_line,
            "source_commit": self.source_commit,
        }


@dataclass(frozen=True)
class Edge:
    source_id: str
    target_id: str
    relation: str
    evidence_grade: EvidenceGrade
    producer: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "evidence_grade": self.evidence_grade.value,
            "producer": self.producer,
        }


@dataclass(frozen=True)
class SourceChunk:
    id: str
    source_commit: str
    source_path: str
    source_start_line: int
    source_end_line: int
    declaration_hint: str | None
    text: str
    content_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_commit": self.source_commit,
            "source_path": self.source_path,
            "source_start_line": self.source_start_line,
            "source_end_line": self.source_end_line,
            "declaration_hint": self.declaration_hint,
            "text": self.text,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class ArtifactScope:
    root_modules: tuple[str, ...]
    dependency_boundary: str


@dataclass(frozen=True)
class ProducerPin:
    kind: str
    tool_repo: str
    tool_commit: str
    tool_hash: str


@dataclass(frozen=True)
class ArtifactManifest:
    schema: str
    source_repo: str
    source_commit: str
    source_subdir: str
    producer: ProducerPin
    scope: ArtifactScope
    nodes_sha256: str
    edges_sha256: str
    sources_sha256: str | None
    nodes_count: int
    edges_count: int
    created_from_authoritative_commit: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "source": {
                "repo": self.source_repo,
                "commit": self.source_commit,
                "subdir": self.source_subdir,
            },
            "producer": {
                "kind": self.producer.kind,
                "tool_repo": self.producer.tool_repo,
                "tool_commit": self.producer.tool_commit,
                "tool_hash": self.producer.tool_hash,
            },
            "scope": {
                "root_modules": list(self.scope.root_modules),
                "dependency_boundary": self.scope.dependency_boundary,
            },
            "members": {
                "nodes": {"sha256": self.nodes_sha256},
                "edges": {"sha256": self.edges_sha256},
                "sources": {"sha256": self.sources_sha256},
            },
            "counts": {
                "nodes": self.nodes_count,
                "edges": self.edges_count,
            },
            "created_from_authoritative_commit": self.created_from_authoritative_commit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ArtifactManifest":
        source = data["source"]
        producer = data["producer"]
        scope = data["scope"]
        members = data["members"]
        counts = data["counts"]
        assert isinstance(source, dict)
        assert isinstance(producer, dict)
        assert isinstance(scope, dict)
        assert isinstance(members, dict)
        assert isinstance(counts, dict)
        nodes_member = members["nodes"]
        edges_member = members["edges"]
        sources_member = members["sources"]
        assert isinstance(nodes_member, dict)
        assert isinstance(edges_member, dict)
        assert isinstance(sources_member, dict)
        roots = scope["root_modules"]
        assert isinstance(roots, list)
        return cls(
            schema=str(data["schema"]),
            source_repo=str(source["repo"]),
            source_commit=str(source["commit"]),
            source_subdir=str(source["subdir"]),
            producer=ProducerPin(
                kind=str(producer["kind"]),
                tool_repo=str(producer["tool_repo"]),
                tool_commit=str(producer["tool_commit"]),
                tool_hash=str(producer["tool_hash"]),
            ),
            scope=ArtifactScope(
                root_modules=tuple(str(item) for item in roots),
                dependency_boundary=str(scope["dependency_boundary"]),
            ),
            nodes_sha256=str(nodes_member["sha256"]),
            edges_sha256=str(edges_member["sha256"]),
            sources_sha256=(
                None
                if sources_member["sha256"] is None
                else str(sources_member["sha256"])
            ),
            nodes_count=int(counts["nodes"]),
            edges_count=int(counts["edges"]),
            created_from_authoritative_commit=bool(
                data["created_from_authoritative_commit"]
            ),
        )
