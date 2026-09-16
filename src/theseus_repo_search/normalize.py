from __future__ import annotations

from .errors import RepoSearchError
from .model import Edge, EvidenceGrade, Node


RELATION_BY_KIND = {
    "type": ("type_dependency", EvidenceGrade.ELABORATED_TYPE_DEPENDENCY),
    "value": ("value_dependency", EvidenceGrade.ELABORATED_VALUE_DEPENDENCY),
}


def _module_in_scope(module: str, root_modules: tuple[str, ...]) -> bool:
    return any(module == root or module.startswith(f"{root}.") for root in root_modules)


def normalize_leandepviz(
    raw: dict[str, object],
    *,
    source_commit: str,
    root_modules: tuple[str, ...],
    producer_ref: str,
) -> tuple[list[Node], list[Edge]]:
    raw_nodes = raw["nodes"]
    raw_edges = raw["edges"]
    assert isinstance(raw_nodes, list)
    assert isinstance(raw_edges, list)

    nodes_by_full_name: dict[str, Node] = {}
    for item in raw_nodes:
        assert isinstance(item, dict)
        module = str(item["module"])
        if not _module_in_scope(module, root_modules):
            continue
        full_name = str(item["fullName"])
        nodes_by_full_name[full_name] = Node.from_lean(
            full_name=full_name,
            name=str(item["name"]),
            kind=str(item["kind"]),
            module=module,
            source_commit=source_commit,
        )

    edges: set[Edge] = set()
    for item in raw_edges:
        assert isinstance(item, dict)
        kind = str(item["kind"])
        if kind not in RELATION_BY_KIND:
            raise RepoSearchError(
                "BLOCKED_ARTIFACT_INTEGRITY",
                f"unknown LeanDepViz edge kind: {kind}",
            )

        dependency = str(item["source"])
        dependent = str(item["target"])
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
