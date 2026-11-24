#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict, cast

FACT_SCHEMA = "theseus.lean-semantic-fact.v1"
FACT_MARKER = "THESEUS_FACT\t"
_MORE_LEAN_ARGS = re.compile(r"(?m)^(moreLeanArgs\s*=\s*)\[(.*)\](\s*)$")


class SemanticFact(TypedDict):
    schema: str
    module: str
    full_name: str
    kind: str
    type_dependencies: list[str]
    value_dependencies: list[str]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_overlay(canonical: str, plugin_path: str) -> str:
    if not plugin_path or "\n" in plugin_path or "\r" in plugin_path:
        raise ValueError("plugin path must be a non-empty single line")
    matches = list(_MORE_LEAN_ARGS.finditer(canonical))
    if len(matches) != 1:
        raise ValueError("expected exactly one package moreLeanArgs assignment")

    match = matches[0]
    inner = match.group(2).strip()
    plugin_arg = json.dumps(f"--plugin={plugin_path}")
    if "--plugin=" in inner:
        raise ValueError("canonical moreLeanArgs already contains a plugin")
    replacement_inner = f"{inner}, {plugin_arg}" if inner else plugin_arg
    replacement = f"{match.group(1)}[{replacement_inner}]{match.group(3)}"
    return canonical[: match.start()] + replacement + canonical[match.end() :]


def _require_str(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"semantic fact {key} must be a non-empty string")
    return value


def _require_names(row: dict[str, object], key: str) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list):
        raise TypeError(f"semantic fact {key} must be an array of non-empty strings")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise TypeError(f"semantic fact {key} must be an array of non-empty strings")
        names.append(item)
    return sorted(set(names))


def _canonical_fact(value: object) -> SemanticFact:
    if not isinstance(value, dict):
        raise TypeError("semantic fact must be a JSON object")
    row = cast(dict[str, object], value)
    if row.get("schema") != FACT_SCHEMA:
        raise ValueError(f"unsupported semantic fact schema: {row.get('schema')!r}")
    return {
        "schema": FACT_SCHEMA,
        "module": _require_str(row, "module"),
        "full_name": _require_str(row, "full_name"),
        "kind": _require_str(row, "kind"),
        "type_dependencies": _require_names(row, "type_dependencies"),
        "value_dependencies": _require_names(row, "value_dependencies"),
    }


def parse_fact_log(text: str) -> list[SemanticFact]:
    by_name: dict[str, SemanticFact] = {}
    for line in text.splitlines():
        marker = line.find(FACT_MARKER)
        if marker < 0:
            continue
        payload = line[marker + len(FACT_MARKER) :].strip()
        fact = _canonical_fact(json.loads(payload))
        name = fact["full_name"]
        previous = by_name.get(name)
        if previous is not None and previous != fact:
            raise ValueError(f"conflicting semantic fact for declaration: {name}")
        by_name[name] = fact
    if not by_name:
        raise ValueError("semantic fact log contains no facts")
    return sorted(by_name.values(), key=lambda row: (row["module"], row["full_name"]))


def load_fact_jsonl(path: Path) -> list[SemanticFact]:
    facts: list[SemanticFact] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            facts.append(_canonical_fact(json.loads(line)))
    if not facts:
        raise ValueError("semantic fact jsonl contains no facts")
    names = [row["full_name"] for row in facts]
    if len(set(names)) != len(names):
        raise ValueError("semantic fact jsonl contains duplicate declarations")
    return sorted(facts, key=lambda row: (row["module"], row["full_name"]))


def load_fact_sidecar_dir(
    root: Path,
) -> tuple[list[SemanticFact], list[dict[str, object]]]:
    files = sorted(path for path in root.glob("*.jsonl") if path.is_file())
    if not files:
        raise ValueError("semantic sidecar directory contains no jsonl fragments")

    facts: list[SemanticFact] = []
    fragments: list[dict[str, object]] = []
    seen_names: set[str] = set()
    for path in files:
        module = path.name.removesuffix(".jsonl")
        data = path.read_bytes()
        rows = [
            _canonical_fact(json.loads(line))
            for line in data.decode("utf-8").splitlines()
            if line.strip()
        ]
        if not rows:
            raise ValueError(f"semantic sidecar fragment is empty: {path.name}")
        rows = sorted(rows, key=lambda row: (row["module"], row["full_name"]))
        canonical = "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ).encode("utf-8")
        if data != canonical:
            path.write_bytes(canonical)
        for row in rows:
            if row["module"] != module:
                raise ValueError(
                    f"sidecar module mismatch: file={module} fact={row['module']}"
                )
            name = row["full_name"]
            if name in seen_names:
                raise ValueError(
                    f"duplicate declaration across sidecars: {name}"
                )
            seen_names.add(name)
            facts.append(row)
        fragments.append(
            {
                "module": module,
                "path": path.name,
                "sha256": _sha256(canonical),
                "facts": len(rows),
            }
        )

    return (
        sorted(facts, key=lambda row: (row["module"], row["full_name"])),
        fragments,
    )


def _plugin_canonical_id(fact: SemanticFact) -> str:
    raw_name = fact["full_name"]
    module = fact["module"]
    if "." in raw_name or module == "_unknown_" or not module:
        return raw_name
    return f"{module}.{raw_name}"


def _plugin_identity_map(facts: Iterable[SemanticFact]) -> dict[str, str]:
    identities: dict[str, str] = {}
    canonical_names: set[str] = set()
    for fact in facts:
        kernel_name = fact["full_name"]
        canonical_name = _plugin_canonical_id(fact)
        if canonical_name in canonical_names:
            raise ValueError(f"duplicate canonical plugin identity: {canonical_name}")
        identities[kernel_name] = canonical_name
        canonical_names.add(canonical_name)
    return identities


def _plugin_dependency_id(
    fact: SemanticFact,
    dependency: str,
    identities: dict[str, str],
) -> str:
    if dependency in identities:
        return identities[dependency]
    if "." in dependency:
        return dependency
    module = fact["module"]
    if module and module != "_unknown_":
        return f"{module}.{dependency}"
    return dependency


def _plugin_edges(
    facts: Iterable[SemanticFact],
    *,
    control_names: set[str] | None = None,
) -> set[tuple[str, str, str]]:
    rows = list(facts)
    identities = _plugin_identity_map(rows)
    edges: set[tuple[str, str, str]] = set()
    for row in rows:
        dependent = identities[row["full_name"]]
        if control_names is not None and dependent not in control_names:
            continue
        for dependency in row["type_dependencies"]:
            target = _plugin_dependency_id(row, dependency, identities)
            if control_names is None or target in control_names:
                edges.add((dependent, target, "type_dependency"))
        for dependency in row["value_dependencies"]:
            target = _plugin_dependency_id(row, dependency, identities)
            if control_names is None or target in control_names:
                edges.add((dependent, target, "value_dependency"))
    return edges


def _leandepviz_resolver(raw_nodes: list[object]):
    modules_by_full: dict[str, str] = {}
    by_short: dict[str, set[str]] = {}
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            raise TypeError(f"LeanDepViz node[{index}] must be an object")
        row = cast(dict[str, object], item)
        full = row.get("fullName")
        short = row.get("name")
        module = row.get("module")
        if not isinstance(full, str) or not full:
            raise TypeError(f"LeanDepViz node[{index}].fullName must be a non-empty string")
        if not isinstance(short, str) or not short:
            raise TypeError(f"LeanDepViz node[{index}].name must be a non-empty string")
        if not isinstance(module, str) or not module:
            raise TypeError(f"LeanDepViz node[{index}].module must be a non-empty string")
        if full in modules_by_full:
            raise ValueError(f"duplicate LeanDepViz fullName: {full}")
        modules_by_full[full] = module
        by_short.setdefault(short, set()).add(full)

    def resolve(endpoint: object) -> str | None:
        if not isinstance(endpoint, str) or not endpoint:
            raise TypeError("LeanDepViz edge endpoint must be a non-empty string")
        if endpoint in modules_by_full:
            return endpoint
        candidates = by_short.get(endpoint, set())
        if not candidates:
            return None
        if len(candidates) == 1:
            return next(iter(candidates))
        synthetic = {
            candidate
            for candidate in candidates
            if candidate == f"{modules_by_full[candidate]}.{endpoint}"
        }
        if len(synthetic) == 1:
            return next(iter(synthetic))
        raise ValueError(f"ambiguous LeanDepViz edge endpoint: {endpoint}")

    return resolve


def _leandepviz_node_names(raw: dict[str, object]) -> set[str]:
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list):
        raise TypeError("LeanDepViz raw graph must contain a nodes array")
    names: set[str] = set()
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            raise TypeError(f"LeanDepViz node[{index}] must be an object")
        full = item.get("fullName")
        if not isinstance(full, str) or not full:
            raise TypeError(
                f"LeanDepViz node[{index}].fullName must be a non-empty string"
            )
        if full in names:
            raise ValueError(f"duplicate LeanDepViz fullName: {full}")
        names.add(full)
    return names


def _leandepviz_edges(
    raw: dict[str, object], local_names: set[str]
) -> set[tuple[str, str, str]]:
    raw_nodes = raw.get("nodes")
    raw_edges = raw.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise TypeError("LeanDepViz raw graph must contain nodes and edges arrays")
    resolve = _leandepviz_resolver(raw_nodes)
    edges: set[tuple[str, str, str]] = set()
    relation = {"type": "type_dependency", "value": "value_dependency"}
    for index, item in enumerate(raw_edges):
        if not isinstance(item, dict):
            raise TypeError(f"LeanDepViz edge[{index}] must be an object")
        row = cast(dict[str, object], item)
        kind = row.get("kind")
        if not isinstance(kind, str) or kind not in relation:
            continue
        dependency = resolve(row.get("source"))
        dependent = resolve(row.get("target"))
        if dependency is None or dependent is None:
            continue
        if dependency in local_names and dependent in local_names:
            edges.add((dependent, dependency, relation[kind]))
    return edges


def _edge_rows(edges: Iterable[tuple[str, str, str]]) -> list[dict[str, str]]:
    return [
        {"dependent": dependent, "dependency": dependency, "relation": relation}
        for dependent, dependency, relation in sorted(edges)
    ]


def compare_plugin_facts_to_leandepviz(
    facts: list[SemanticFact],
    raw: dict[str, object],
    *,
    source_commit: str,
    root_modules: tuple[str, ...],
) -> dict[str, object]:
    if not source_commit:
        raise ValueError("source commit must be non-empty")
    if not root_modules:
        raise ValueError("root modules must be non-empty")
    identities = _plugin_identity_map(facts)
    plugin_names = set(identities.values())
    leandepviz_names = _leandepviz_node_names(raw)
    common_nodes = plugin_names & leandepviz_names
    plugin_only_nodes = plugin_names - leandepviz_names
    leandepviz_only_nodes = leandepviz_names - plugin_names

    plugin_edges = _plugin_edges(facts, control_names=leandepviz_names)
    leandepviz_edges = _leandepviz_edges(raw, leandepviz_names)
    common = plugin_edges & leandepviz_edges
    plugin_only = plugin_edges - leandepviz_edges
    leandepviz_only = leandepviz_edges - plugin_edges
    exact = not (
        plugin_only_nodes
        or leandepviz_only_nodes
        or plugin_only
        or leandepviz_only
    )
    return {
        "schema": "theseus.riemann-semantic-plugin-comparison.v2",
        "source_commit": source_commit,
        "root_modules": list(root_modules),
        "exact": exact,
        "plugin_facts": len(facts),
        "plugin_nodes": len(plugin_names),
        "leandepviz_nodes": len(leandepviz_names),
        "common_nodes": len(common_nodes),
        "plugin_only_nodes": sorted(plugin_only_nodes),
        "leandepviz_only_nodes": sorted(leandepviz_only_nodes),
        "plugin_internal_edges": len(plugin_edges),
        "leandepviz_internal_edges": len(leandepviz_edges),
        "common_edges": len(common),
        "plugin_only_edges": _edge_rows(plugin_only),
        "leandepviz_only_edges": _edge_rows(leandepviz_only),
    }


def _cmd_overlay(args: argparse.Namespace) -> int:
    canonical_path = args.canonical.resolve()
    out_path = args.out.resolve()
    canonical = canonical_path.read_text(encoding="utf-8")
    overlay = make_overlay(canonical, args.plugin)
    out_path.write_text(overlay, encoding="utf-8")
    metrics = {
        "schema": "theseus.riemann-semantic-plugin-overlay.v1",
        "canonical_sha256": _sha256(canonical.encode()),
        "overlay_sha256": _sha256(overlay.encode()),
        "plugin_argument": f"--plugin={args.plugin}",
    }
    payload = json.dumps(metrics, sort_keys=True)
    print(payload)
    if args.metrics_out:
        args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_out.write_text(payload + "\n", encoding="utf-8")
    return 0


def _cmd_facts(args: argparse.Namespace) -> int:
    facts = parse_fact_log(args.log.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in facts
        ),
        encoding="utf-8",
    )
    print(json.dumps({"semantic_facts": len(facts)}, sort_keys=True))
    return 0


def _cmd_sidecars(args: argparse.Namespace) -> int:
    facts, fragments = load_fact_sidecar_dir(args.dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    merged = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in facts
    ).encode("utf-8")
    args.out.write_bytes(merged)
    metrics = {
        "schema": "theseus.riemann-semantic-sidecar-transport.v1",
        "facts": len(facts),
        "fragments": fragments,
        "merged_sha256": _sha256(merged),
    }
    payload = json.dumps(metrics, sort_keys=True)
    print(payload)
    if args.metrics_out:
        args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_out.write_text(payload + "\n", encoding="utf-8")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    facts = load_fact_jsonl(args.facts)
    raw = json.loads(args.leandepviz.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("LeanDepViz raw graph must be a JSON object")
    result = compare_plugin_facts_to_leandepviz(
        facts,
        cast(dict[str, object], raw),
        source_commit=args.source_commit,
        root_modules=tuple(args.root_module),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, sort_keys=True))
    if args.require_exact and result["exact"] is not True:
        raise ValueError(
            "semantic comparison is not exact: "
            f"plugin_only_nodes={len(cast(list[object], result['plugin_only_nodes']))} "
            f"leandepviz_only_nodes={len(cast(list[object], result['leandepviz_only_nodes']))} "
            f"plugin_only_edges={len(cast(list[object], result['plugin_only_edges']))} "
            f"leandepviz_only_edges={len(cast(list[object], result['leandepviz_only_edges']))}"
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded Riemann semantic-plugin pilot helpers."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    overlay = sub.add_parser("overlay")
    overlay.add_argument("--canonical", type=Path, required=True)
    overlay.add_argument("--plugin", required=True)
    overlay.add_argument("--out", type=Path, required=True)
    overlay.add_argument("--metrics-out", type=Path)
    overlay.set_defaults(func=_cmd_overlay)

    facts = sub.add_parser("facts")
    facts.add_argument("--log", type=Path, required=True)
    facts.add_argument("--out", type=Path, required=True)
    facts.set_defaults(func=_cmd_facts)

    sidecars = sub.add_parser("sidecars")
    sidecars.add_argument("--dir", type=Path, required=True)
    sidecars.add_argument("--out", type=Path, required=True)
    sidecars.add_argument("--metrics-out", type=Path)
    sidecars.set_defaults(func=_cmd_sidecars)

    compare = sub.add_parser("compare")
    compare.add_argument("--facts", type=Path, required=True)
    compare.add_argument("--leandepviz", type=Path, required=True)
    compare.add_argument("--source-commit", required=True)
    compare.add_argument("--root-module", action="append", required=True)
    compare.add_argument("--out", type=Path, required=True)
    compare.add_argument("--require-exact", action="store_true")
    compare.set_defaults(func=_cmd_compare)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(
            f"RIEMANN_SEMANTIC_PLUGIN_PILOT_BLOCKED: {exc}",
            file=__import__("sys").stderr,
        )
        raise SystemExit(2)
