"""Conservative name-overlap candidates, not mathematical equivalence."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from .accepted_catalog import AcceptedCatalog, accepted_catalog_sha256
from .errors import RepoSearchError
from .turso_materializer import TursoHttpClient
from .turso_query import connect_corpus

# Suppress only when all three signals agree: known generated name,
# compatible parent declaration, and no recorded source location.
_GENERATED_PARENT_KINDS = {
    "inj": {"ctor", "inductive"},
    "injEq": {"ctor", "inductive"},
    "sizeOf_spec": {"ctor"},
    "congr_simp": {"ctor", "def", "inductive", "thm"},
    "eq_def": {"def"},
    "noConfusion": {"inductive"},
    "noConfusionType": {"inductive"},
    "brecOn": {"inductive"},
}


@dataclass(frozen=True)
class Occurrence:
    source_id: str
    artifact_identity: str
    declaration_id: str
    kind: str
    parent_kind: str | None
    source_path: str | None

    @property
    def leaf(self) -> str:
        return self.declaration_id.removeprefix("lean:").rsplit(".", 1)[-1]


def suppression_reason(row: Occurrence) -> str | None:
    if row.kind != "thm":
        return "non_theorem"
    if row.source_path is None and row.parent_kind in _GENERATED_PARENT_KINDS.get(row.leaf, set()):
        return "generated_lean_theorem"
    return None


def _shared(rows: Iterable[Occurrence]) -> dict[str, list[Occurrence]]:
    groups: dict[str, list[Occurrence]] = defaultdict(list)
    for row in rows:
        groups[row.leaf].append(row)
    return {name: group for name, group in groups.items()
            if len({r.source_id for r in group}) > 1}


def overlap_groups(rows: Iterable[Occurrence], *, limit: int = 20,
                   occurrence_limit: int = 10, raw: bool = False) -> dict[str, object]:
    if not 1 <= limit <= 100 or not 1 <= occurrence_limit <= 50:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "overlap: limit 1..100, occurrences 1..50")
    original = list(rows)
    counts = Counter(reason for row in original if (reason := suppression_reason(row)) is not None)
    all_groups = _shared(original)
    selected = all_groups if raw else _shared(row for row in original if suppression_reason(row) is None)
    ordered = sorted(selected.items(), key=lambda item: (-len({r.source_id for r in item[1]}), item[0]))
    result = []
    for name, group in ordered[:limit]:
        members = sorted(group, key=lambda r: (r.source_id, r.declaration_id))
        result.append({"name": name, "corpora": sorted({r.source_id for r in group}),
                       "occurrence_count": len(group), "truncated": len(group) > occurrence_limit,
                       "possible_generated_name": name in _GENERATED_PARENT_KINDS,
                       "occurrences": [asdict(r) for r in members[:occurrence_limit]]})
    return {"filter": "raw" if raw else "theorem_candidates_v1",
            "evidence": "shared_short_name_only", "scanned_nodes": len(original),
            "raw_shared_names": len(all_groups),
            "raw_shared_occurrences": sum(map(len, all_groups.values())),
            "shared_names": len(selected),
            "shared_occurrences": sum(map(len, selected.values())),
            "suppressed": {} if raw else dict(counts),
            "groups_truncated": len(ordered) > limit, "groups": result}


def catalog_overlap(catalog: AcceptedCatalog, client: TursoHttpClient, *,
                    limit: int = 20, occurrence_limit: int = 10, raw: bool = False) -> dict[str, object]:
    if not 1 <= limit <= 100 or not 1 <= occurrence_limit <= 50:
        raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "overlap: limit 1..100, occurrences 1..50")
    rows: list[Occurrence] = []
    unavailable = []
    searched = []
    for item in catalog.corpora:
        try:
            conn = connect_corpus(item, client)
            total = conn.execute("SELECT count(*) FROM nodes").fetchone()
            if total is None or type(total[0]) is not int or not 0 <= total[0] <= 100000:
                raise RepoSearchError("BLOCKED_QUERY_BOUNDS", "overlap corpus scan bound exceeded")
            corpus_rows: list[Occurrence] = []
            for offset in range(0, total[0], 2000):
                page = conn.execute(
                    "SELECT n.id,n.kind,p.kind,n.source_path FROM nodes n "
                    "LEFT JOIN nodes p ON p.id=substr(n.id,1,length(n.id)-length(n.name)-1) "
                    "AND substr(n.id,-length(n.name)-1)='.'||n.name "
                    "ORDER BY n.id LIMIT 2000 OFFSET ?", (offset,)).fetchall()
                if len(page) != min(2000, total[0]-offset):
                    raise RepoSearchError("DEGRADED_OVERLAP_SCAN", "incomplete page")
                corpus_rows.extend(Occurrence(item.source_id, item.artifact_identity,
                    str(r[0]), str(r[1]), None if r[2] is None else str(r[2]),
                    None if r[3] is None else str(r[3])) for r in page)
            if len({r.declaration_id for r in corpus_rows}) != total[0]:
                raise RepoSearchError("DEGRADED_OVERLAP_SCAN", "duplicate declaration scan")
            rows.extend(corpus_rows)
            searched.append(item.source_id)
        except RepoSearchError as exc:
            unavailable.append({"source_id": item.source_id, "code": exc.code, "message": str(exc)})
    result = overlap_groups(rows, limit=limit, occurrence_limit=occurrence_limit, raw=raw)
    status = ("UNAVAILABLE" if not searched else "DEGRADED" if unavailable else
              "CANDIDATE" if result["shared_names"] else "UNKNOWN")
    return {**result, "status": status, "backend": "turso",
            "catalog_sha256": accepted_catalog_sha256(catalog),
            "searched_corpora": len(searched), "searched_sources": searched,
            "unavailable_corpora": len(unavailable), "unavailable": unavailable,
            "coverage": "accepted_internal_node_scopes"}
