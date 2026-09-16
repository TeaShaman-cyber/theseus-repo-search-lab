from __future__ import annotations

from .errors import RepoSearchError
from .model import Edge, EvidenceGrade, Node


RELATION_BY_KIND = {
    "type": ("type_dependency", EvidenceGrade.ELABORATED_TYPE_DEPENDENCY),
    "value": ("value_dependency", EvidenceGrade.ELABORATED_VALUE_DEPENDENCY),
}


def _integrity(message: str) -> RepoSearchError:
    return RepoSearchError("BLOCKED_ARTIFACT_INTEGRITY", message)


def _require_list(raw: dict[str, object], key: str) -> list[object]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise _integrity(f"LeanDepViz {key} must be an array")
    return value


def _require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _integrity(f"LeanDepViz {label} must be an object")
    return value


def _require_string(row: dict[str, object], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise _integrity(f"LeanDepViz {label}.{key} must be a non-empty string")
    return value


def _module_in_scope(module: str, root_modules: tuple[str, ...]) -> bool:
    return any(module == root or module.startswith(f"{root}.") for root in root_modules)


def normalize_leandepviz(
    raw: dict[str, object],
    *,
    source_commit: str,
    root_modules: tuple[str, ...],
    producer_ref: str,
) -> tuple[list[Node], list[Edge]]:
    raw_nodes = _require_list(raw, "nodes")
    raw_edges = _require_list(raw, "edges")

    nodes_by_full_name: dict[str, Node] = {}
    seen_full_names: set[str] = set()
    for index, item in enumerate(raw_nodes):
        row = _require_object(item, f"node[{index}]")
        module = _require_string(row, "module", f"node[{index}]")
        full_name = _require_string(row, "fullName", f"node[{index}]")
        name = _require_string(row, "name", f"node[{index}]")
        kind = _require_string(row, "kind", f"node[{index}]")
        if full_name in seen_full_names:
            raise _integrity(f"duplicate LeanDepViz fullName: {full_name}")
        seen_full_names.add(full_name)
        if not _module_in_scope(module, root_modules):
            continue
        nodes_by_full_name[full_name] = Node.from_lean(
            full_name=full_name,
            name=name,
            kind=kind,
            module=module,
            source_commit=source_commit,
        )

    edges: set[Edge] = set()
    for index, item in enumerate(raw_edges):
        row = _require_object(item, f"edge[{index}]")
        kind = _require_string(row, "kind", f"edge[{index}]")
        dependency = _require_string(row, "source", f"edge[{index}]")
        dependent = _require_string(row, "target", f"edge[{index}]")
        if kind not in RELATION_BY_KIND:
            raise _integrity(f"unknown LeanDepViz edge kind: {kind}")
        if dependency not in nodes_by_full_name or dependent not in nodes_by_full_name:
            continue

        relation, evidence_grade = RELATION_BY_KIND[kind]
        edges.add(
            Edge(
                source_id=f"lean:{dependent}",
                target_id=f"lean:{dependency}",
                relation=relation,
                evidence_grade=evidence_grade,
                producer=producer_ref,
            )
        )

    nodes = sorted(nodes_by_full_name.values(), key=lambda node: node.id)
    normalized_edges = sorted(
        edges,
        key=lambda edge: (
            edge.source_id,
            edge.target_id,
            edge.relation,
            edge.producer,
        ),
    )
    return nodes, normalized_edges
