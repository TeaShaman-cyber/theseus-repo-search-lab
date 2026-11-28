"""Public, offline description of the existing read-only research interface."""
from __future__ import annotations

from .accepted_catalog import AcceptedCatalog, accepted_catalog_sha256

# Names are public CLI operations, not provider-specific commands.
_OPERATIONS = (
    ("startup", "Compact offline architecture, catalog and verification map", (), "query startup"),
    ("capabilities", "Describe research operations without connecting to any backend", (), "query capabilities"),
    ("corpora", "List accepted corpus identities (not live availability)", (), "query corpora"),
    ("search", "Find declarations or source text without graph expansion", ("turso",), "query search 'spectral measure' --limit 5"),
    ("graph-search", "Discover mathematical structure: search and expand dependency neighborhoods", ("turso", "kusto"), "query graph-search 'spectral measure' --limit 5 --depth 2"),
    ("reach", "Explore reachable declarations from a known root in one corpus", ("kusto",), "query reach zeta23 Zeta23.XiPrime.coeffMoments_atV --depth 3"),
    ("reach-all", "Compare known declarations across independent corpus graphs", ("kusto",), "query reach-all Zeta23.XiPrime.coeffMoments_atV --depth 2"),
    ("deps", "Typed dependency edges of a known declaration", ("turso", "local-duckdb"), "query deps zeta23 Zeta23.XiPrime.coeffMoments_atV"),
    ("rdeps", "Typed dependency edges in reverse: locate uses", ("turso", "local-duckdb"), "query rdeps zeta23 Zeta23.XiPrime.GzMoments"),
    ("path", "Find a bounded typed path between two declarations in one corpus", ("turso", "local-duckdb"), "query path zeta23 Zeta23.XiPrime.coeffMoments_atV Zeta23.XiPrime.CoeffMoments"),
    ("context", "Bounded source context with typed edges and provenance", ("turso", "local-duckdb"), "query context zeta23 Zeta23.XiPrime.coeffMoments_atV --token-budget 2000"),
    ("overlap", "Find short-name overlap candidates, not mathematical equivalence", ("turso",), "query overlap --limit 10"),
)

QUERY_DESCRIPTION = (
    "Read-only mathematical research. Start with graph-search for structural discovery; "
    "use reach/reach-all for known roots and context for source evidence. "
    "Use search alone for text retrieval. Run startup for the compact map, capabilities for details."
)


def operation_help(name: str) -> str:
    return next(description for op, description, _, _ in _OPERATIONS if op == name)


def query_capabilities() -> dict[str, object]:
    return {
        "schema": "theseus.repo-search.query-capabilities.v1",
        "read_only": True,
        "availability": "NOT_CHECKED",
        "description": QUERY_DESCRIPTION,
        "operations": [
            {"name": name, "description": description, "backends": list(backends),
             "example": "repo-search " + example}
            for name, description, backends, example in _OPERATIONS
        ],
        "research_routes": {
            "discover_structure": ["graph-search", "context"],
            "compare_known_declarations": ["reach-all", "context"],
            "inspect_typed_dependencies": ["deps", "rdeps", "path", "context"],
            "find_text": ["search"],
            "find_overlap_candidates": ["overlap", "context"],
        },
        "routing": {
            "graph-search": "Turso lexical Top-K followed by native Kusto reachability, at most 10 seeds, depth 1..3, graph-limit 1..50 per seed.",
            "reach": "Native Kusto reachability, depth 1..3, limit 1..500.",
            "reach-all": "Independent native Kusto reachability per corpus, depth 1..3, limit 1..500 per corpus.",
            "typed_graph": "Turso for full entries; Turso source metadata/text plus local DuckDB graph for selected split entries.",
        },
        "motherduck_role": "Verified graph storage for split intake, not a warm query backend.",
        "boundaries": [
            "Capabilities describe supported operations, not configured credentials or live availability.",
            "Inspect status, lexical_status, graph_status, unavailable counts, and completeness where returned; zero exit is not proof of completeness.",
            "No silent backend fallback. A graph-search failure preserves lexical evidence and reports degraded graph coverage.",
            "Reachability covers indexed graphs and selected depth, not all imported dependencies; no invented cross-corpus edges.",
            "Lexical hits, overlap and graph paths are research evidence, not proof certification or mathematical equivalence.",
        ],
    }


def query_startup(catalog: AcceptedCatalog | None) -> dict[str, object]:
    """Small local orientation receipt; never imply a live provider check."""
    components = {
        "turso": {
            "role": "Lexical search and source evidence; full-entry graph compatibility",
            "check": "query search CovByVAdd --mode evidence --limit 5; inspect unavailable_corpora",
        },
        "kusto": {
            "role": "Analytical graph reachability",
            "check": "query reach-all ConjectureOfMarton.theorem_1_2 --depth 2 --limit 20; inspect per-corpus status",
        },
        "local-duckdb": {
            "role": "Disposable split graph serving",
            "check": "query deps and context on a selected split corpus; verify graph receipt",
        },
        "motherduck": {
            "role": "Verified split graph storage, not warm query serving",
            "check": "Read-only table inventory and artifact/pin identity via governed launcher",
        },
        "immutable-releases": {
            "role": "Accepted artifact recovery authority",
            "check": "Verify release, assets and accepted fingerprint before recovery or selection",
        },
    }
    catalog_summary: dict[str, object] = {"status": "NOT_CONFIGURED"}
    if catalog is not None:
        split = sum(item.split_projection_sha256 is not None for item in catalog.corpora)
        catalog_summary = {
            "status": "VALIDATED",
            "accepted": len(catalog.corpora),
            "split": split,
            "full": len(catalog.corpora) - split,
            "sha256": accepted_catalog_sha256(catalog),
        }
    return {
        "schema": "theseus.repo-search.startup.v1",
        "availability": "NOT_CHECKED",
        "catalog": catalog_summary,
        "components": {
            name: {
                **info,
                "operations": [op for op, _, backends, _ in _OPERATIONS if name in backends],
            }
            for name, info in components.items()
        },
        "qa": "./tools/dev/check; exact-head CI/review; relevant live query and recovery checks",
        "boundary": "Search-only; no proof certification or silent fallback. Report unchecked components explicitly.",
        "details": "repo-search query capabilities",
    }
