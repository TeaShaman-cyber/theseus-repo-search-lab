"""Conservative format adapter for existing upstream deps output.

Preview only: the current artifact identity is one node per name. Preserve the
raw occurrences outside the projection and omit ambiguous names, never choose
an arbitrary owner or classify combined references as type/proof edges.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .errors import RepoSearchError
from .model import Edge, EvidenceGrade, Node


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
        assert (
            isinstance(module, str) and isinstance(name, str) and isinstance(kind, str)
        )
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
