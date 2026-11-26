"""Conservative format adapter for existing upstream deps output.

Preview only: the current artifact identity is one node per name. Preserve the
raw occurrences outside the projection and omit ambiguous names, never choose
an arbitrary owner or classify combined references as type/proof edges.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from .errors import RepoSearchError
from .model import Edge, EvidenceGrade, Node, SourceChunk
from .sources import lean_module_source_path


def normalize_olean_references(
    rows: Iterable[dict[str, object]],
    *,
    source_commit: str,
    modules: tuple[str, ...],
    producer_ref: str,
) -> tuple[list[Node], list[Edge], dict[str, object]]:
    scope = set(modules)
    occurrences: dict[str, list[tuple[str, str, set[str]]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    outside = 0
    for row in rows:
        module, name, kind, uses = (
            row.get(k) for k in ("module", "name", "kind", "uses")
        )
        if (
            not all(isinstance(x, str) and x for x in (module, name, kind))
            or not isinstance(uses, list)
            or not all(isinstance(x, str) and x for x in uses)
        ):
            raise RepoSearchError(
                "BLOCKED_ARTIFACT_INTEGRITY", "invalid olean-export deps row"
            )
        assert isinstance(module, str)
        assert isinstance(name, str)
        assert isinstance(kind, str)
        if (module, name) in seen:
            raise RepoSearchError(
                "BLOCKED_ARTIFACT_INTEGRITY", "repeated module/name occurrence"
            )
        seen.add((module, name))
        if module not in scope:
            outside += 1
        occurrences[name].append((module, kind, set(uses)))
    nodes = []
    edges = []
    ambiguous = {
        name: sorted(m for m, _, _ in entries)
        for name, entries in occurrences.items()
        if len(entries) > 1 and any(m in scope for m, _, _ in entries)
    }
    unique = {
        name: entries[0]
        for name, entries in occurrences.items()
        if len(entries) == 1 and entries[0][0] in scope
    }
    ambiguous_refs = unresolved_refs = external_refs = 0
    for name, (module, kind, uses) in sorted(unique.items()):
        nodes.append(
            Node.from_lean(
                full_name=name,
                name=name.rsplit(".", 1)[-1],
                kind=kind,
                module=module,
                source_commit=source_commit,
            )
        )
        for ref in sorted(uses):
            if ref in unique:
                edges.append(
                    Edge(
                        f"lean:{name}",
                        f"lean:{ref}",
                        "compiled_reference",
                        EvidenceGrade.COMPILED_REFERENCE,
                        producer_ref,
                    )
                )
            elif len(occurrences.get(ref, ())) > 1:
                ambiguous_refs += 1
            elif ref in occurrences:
                external_refs += 1
            else:
                unresolved_refs += 1
    return (
        nodes,
        edges,
        {
            "scope_modules": len(scope),
            "raw_rows": len(seen),
            "outside_rows": outside,
            "indexed_nodes": len(nodes),
            "indexed_edges": len(edges),
            "ambiguous_names": ambiguous,
            "omitted_ambiguous_occurrences": sum(
                m in scope for entries in ambiguous.values() for m in entries
            ),
            "omitted_ambiguous_references": ambiguous_refs,
            "omitted_external_references": external_refs,
            "omitted_unresolved_references": unresolved_refs,
            "complete_within_scope": False,
        },
    )


def bind_preview_node_sources(
    nodes: list[Node], sources: list[SourceChunk], report: dict[str, object]
) -> list[Node]:
    """Attach only reciprocal unique lexical candidates, not elaborated ranges.

    Consider every raw in-module name, including graph-omitted occurrences.
    A qualified hint and a namespace-local hint can compete for one node.
    Neither an ambiguous name nor an ambiguous source chunk is resolved by
    choosing the shortest spelling or by traversal order.
    """
    names_by_path: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        names_by_path[lean_module_source_path(node.module)].add(node.full_name)
    ambiguous = report.get("ambiguous_names")
    if not isinstance(ambiguous, dict):
        raise RepoSearchError(
            "BLOCKED_ARTIFACT_INTEGRITY", "missing raw name ambiguity report"
        )
    for name, modules in ambiguous.items():
        for module in modules:
            names_by_path[lean_module_source_path(module)].add(name)

    # Index all suffixes so large modules do not need a quadratic join.
    by_hint: dict[tuple[str, str], set[str]] = defaultdict(set)
    for source_path, names in names_by_path.items():
        for name in names:
            parts = [name] if "«" in name or "»" in name else name.split(".")
            for i in range(len(parts)):
                by_hint[(source_path, ".".join(parts[i:]))].add(name)
    chunks_by_candidate: dict[tuple[str, str], list[SourceChunk]] = defaultdict(list)
    unique_chunk_ids: set[str] = set()
    for chunk in sources:
        if chunk.declaration_hint is None:
            continue
        candidates = by_hint.get((chunk.source_path, chunk.declaration_hint), set())
        for name in candidates:
            chunks_by_candidate[(chunk.source_path, name)].append(chunk)
        if len(candidates) == 1:
            unique_chunk_ids.add(chunk.id)

    result = []
    for node in nodes:
        chunk_candidates = chunks_by_candidate.get(
            (lean_module_source_path(node.module), node.full_name), []
        )
        if len(chunk_candidates) != 1 or chunk_candidates[0].id not in unique_chunk_ids:
            result.append(node)
            continue
        chunk = chunk_candidates[0]
        if (
            chunk.source_revision != node.source_revision
            or chunk.declaration_hint is None
        ):
            raise RepoSearchError(
                "BLOCKED_ARTIFACT_INTEGRITY", "preview source identity mismatch"
            )
        result.append(
            replace(
                node,
                name=chunk.declaration_hint,
                source_path=chunk.source_path,
                source_start_line=chunk.source_start_line,
                source_end_line=chunk.source_end_line,
            )
        )
    return result
