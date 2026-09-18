from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


def _require_dict(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be an object")
    return value


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _require_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _require_optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field)


class EvidenceGrade(str, Enum):
    ELABORATED_VALUE_DEPENDENCY = "ELABORATED_VALUE_DEPENDENCY"
    ELABORATED_TYPE_DEPENDENCY = "ELABORATED_TYPE_DEPENDENCY"
    STATIC_REFERENCE = "STATIC_REFERENCE"
    LEXICAL_HIT = "LEXICAL_HIT"


@dataclass(frozen=True)
class Node:
    id: str
    full_name: str
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
            full_name=full_name,
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
            "full_name": self.full_name,
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
    authority_receipt_sha256: str | None = None

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
                "authority_receipt": {"sha256": self.authority_receipt_sha256},
            },
            "counts": {
                "nodes": self.nodes_count,
                "edges": self.edges_count,
            },
            "created_from_authoritative_commit": self.created_from_authoritative_commit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ArtifactManifest":
        source = _require_dict(data["source"], "source")
        producer = _require_dict(data["producer"], "producer")
        scope = _require_dict(data["scope"], "scope")
        members = _require_dict(data["members"], "members")
        counts = _require_dict(data["counts"], "counts")
        nodes_member = _require_dict(members["nodes"], "members.nodes")
        edges_member = _require_dict(members["edges"], "members.edges")
        sources_member = _require_dict(members["sources"], "members.sources")
        authority_receipt_member = _require_dict(
            members.get("authority_receipt", {"sha256": None}),
            "members.authority_receipt",
        )
        roots = scope["root_modules"]
        if not isinstance(roots, list) or not all(isinstance(item, str) for item in roots):
            raise TypeError("scope.root_modules must be an array of strings")
        nodes_count = _require_int(counts["nodes"], "counts.nodes")
        edges_count = _require_int(counts["edges"], "counts.edges")
        if nodes_count < 0 or edges_count < 0:
            raise ValueError("artifact counts must be non-negative")
        return cls(
            schema=_require_str(data["schema"], "schema"),
            source_repo=_require_str(source["repo"], "source.repo"),
            source_commit=_require_str(source["commit"], "source.commit"),
            source_subdir=_require_str(source["subdir"], "source.subdir"),
            producer=ProducerPin(
                kind=_require_str(producer["kind"], "producer.kind"),
                tool_repo=_require_str(producer["tool_repo"], "producer.tool_repo"),
                tool_commit=_require_str(producer["tool_commit"], "producer.tool_commit"),
                tool_hash=_require_str(producer["tool_hash"], "producer.tool_hash"),
            ),
            scope=ArtifactScope(
                root_modules=tuple(roots),
                dependency_boundary=_require_str(
                    scope["dependency_boundary"], "scope.dependency_boundary"
                ),
            ),
            nodes_sha256=_require_str(nodes_member["sha256"], "members.nodes.sha256"),
            edges_sha256=_require_str(edges_member["sha256"], "members.edges.sha256"),
            sources_sha256=_require_optional_str(
                sources_member["sha256"], "members.sources.sha256"
            ),
            nodes_count=nodes_count,
            edges_count=edges_count,
            created_from_authoritative_commit=_require_bool(
                data["created_from_authoritative_commit"],
                "created_from_authoritative_commit",
            ),
            authority_receipt_sha256=_require_optional_str(
                authority_receipt_member["sha256"],
                "members.authority_receipt.sha256",
            ),
        )
