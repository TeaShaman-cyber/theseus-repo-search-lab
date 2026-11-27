"""Selection-time coverage, distinct from immutable artifact integrity."""
from collections import Counter


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def coverage_report(raw, nodes, edges, sources, plan, producer_kind):
    if set(plan) != {"purpose", "producer_kind", "excluded_raw_modules",
                     "lexical_boundary", "required_modules", "witnesses", "excluded_raw_declarations"}:
        raise ValueError("coverage plan fields mismatch")
    _text(plan["purpose"], "purpose")
    if plan["producer_kind"] != producer_kind:
        raise ValueError("coverage producer kind mismatch")
    excluded = plan["excluded_raw_modules"]
    excluded_declarations = plan["excluded_raw_declarations"]
    required = plan["required_modules"]
    witnesses = plan["witnesses"]
    if not isinstance(excluded, dict) or not isinstance(excluded_declarations, dict) or not isinstance(required, list) or not required:
        raise ValueError("explicit raw exclusions and required modules needed")
    if not isinstance(witnesses, list) or not witnesses:
        raise ValueError("at least one graph witness needed")
    for m, reason in excluded.items():
        _text(m, "excluded module")
        _text(reason, "exclusion reason")
    if len(set(required)) != len(required):
        raise ValueError("duplicate required modules")
    for m in required:
        _text(m, "required module")
    raw_ids = {"lean:" + x["fullName"]: x["module"] for x in raw["nodes"]}
    by_id = {n.id: n for n in nodes}
    if len(raw_ids) != len(raw["nodes"]) or len(by_id) != len(nodes):
        raise ValueError("duplicate declaration identity")
    raw_counts = Counter(raw_ids.values())
    graph_counts = Counter(n.module for n in nodes)
    if set(required) - set(graph_counts):
        raise ValueError("required module absent from graph")
    if set(by_id) - set(raw_ids) or any(raw_ids[i] != n.module for i, n in by_id.items()):
        raise ValueError("graph declaration differs from raw")
    if set(excluded) != set(raw_counts) - set(graph_counts):
        raise ValueError("unexplained raw module exclusions or stale exclusions")
    expected = {i for i,m in raw_ids.items() if m not in excluded}
    for key, reason in excluded_declarations.items():
        _text(key, "excluded declaration")
        _text(reason, "exclusion reason")
    if set(excluded_declarations) != expected - set(by_id):
        raise ValueError("unexplained declaration loss or stale exclusion within retained module")
    lexical_paths = {s.source_path for s in sources}
    graph_paths = {n.source_path for n in nodes if n.source_path is not None}
    lexical_only = sorted(lexical_paths - graph_paths)
    if lexical_only:
        _text(plan["lexical_boundary"], "lexical-only boundary")
    if graph_paths - lexical_paths:
        raise ValueError("graph source paths absent from lexical index")
    outgoing = {(e.source_id,e.target_id) for e in edges}
    seen = set()
    for w in witnesses:
        if not isinstance(w, dict) or set(w) != {"declaration","source_path","direction","neighbor"}:
            raise ValueError("witness fields mismatch")
        key = "lean:" + _text(w["declaration"], "witness declaration")
        if key in seen:
            raise ValueError("duplicate witness")
        seen.add(key)
        n = by_id.get(key)
        if n is None or n.source_path != _text(w["source_path"], "witness source path"):
            raise ValueError(f"witness not bound to expected source: {key}")
        direction = w["direction"]
        if direction not in ("forward", "reverse"):
            raise ValueError("witness direction invalid")
        neighbor = w["neighbor"]
        if neighbor is not None:
            other = "lean:" + _text(neighbor,"witness neighbor")
            pair = (key,other) if direction == "forward" else (other,key)
            if other == key or pair not in outgoing:
                raise ValueError(f"witness direct dependency absent: {pair}")
        elif any(key == pair[1 if direction == "reverse" else 0] for pair in outgoing):
            raise ValueError("isolated witness has indexed neighbors; specify one")
    if edges and not any(w["neighbor"] is not None for w in witnesses):
        raise ValueError("graph with edges requires a dependency witness")
    return {"raw_nodes":len(raw_ids), "raw_edges":len(raw["edges"]),
            "raw_modules":len(raw_counts), "graph_nodes":len(nodes),
            "graph_edges":len(edges), "graph_modules":len(graph_counts),
            "excluded_raw_nodes":len(raw_ids)-len(nodes),
            "excluded_raw_modules":dict(sorted(excluded.items())),
            "excluded_raw_declarations":dict(sorted(excluded_declarations.items())),
            "lexical_paths":len(lexical_paths), "graph_source_paths":len(graph_paths),
            "lexical_only_paths":lexical_only}


def validate_live_witness(result, witness, source_id, identity, nodes, edges):
    if (result.get("backend") != "turso+kusto" or result.get("graph_status") not in ("FOUND", "DEGRADED")
            or result.get("searched_corpora") != 1
            or result.get("unavailable_corpora") != 0 or result.get("graph_unavailable") != []):
        raise ValueError("live witness requires both backends without unavailable seeds")
    root = "lean:" + witness["declaration"]
    def bound(row):
        return row.get("source_id") == source_id and row.get("artifact_identity") == identity
    hits = result.get("hits", [])
    graphs = result.get("graphs", [])
    if len(hits) != 1 or not bound(hits[0]) or hits[0].get("declaration_id") != root or hits[0].get("source_path") != witness["source_path"]:
        raise ValueError("live witness lexical identity/source mismatch")
    if len(graphs) != 1:
        raise ValueError("live witness graph missing")
    g = graphs[0]
    if (not bound(g) or g.get("backend") != "kusto" or g.get("root") != root
            or g.get("found") is not True or g.get("status") not in ("FOUND", "DEGRADED")
            or g.get("direction") != witness["direction"]
            or g.get("depth") != 1 or (g.get("projection_nodes"),g.get("projection_edges")) != (nodes,edges)):
        raise ValueError("live witness graph identity/count mismatch")
    neighbor = witness["neighbor"]
    observed = {row["id"] for row in g["nodes"] if row["depth"] == 1}
    if neighbor is None:
        if observed:
            raise ValueError("live isolated witness has neighbors")
    elif "lean:" + neighbor not in observed:
        raise ValueError("live witness dependency absent")
