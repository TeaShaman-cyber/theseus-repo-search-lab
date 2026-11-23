#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

FACT_SCHEMA = "theseus.lean-semantic-fact.v1"
FACT_MARKER = "THESEUS_FACT\t"
_MORE_LEAN_ARGS = re.compile(r"(?m)^(moreLeanArgs\s*=\s*)\[(.*)\](\s*)$")


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
        raise ValueError(f"semantic fact {key} must be a non-empty string")
    return value


def _require_names(row: dict[str, object], key: str) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"semantic fact {key} must be an array of non-empty strings")
    return sorted(set(value))


def _canonical_fact(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("semantic fact must be a JSON object")
    if value.get("schema") != FACT_SCHEMA:
        raise ValueError(f"unsupported semantic fact schema: {value.get('schema')!r}")
    return {
        "schema": FACT_SCHEMA,
        "module": _require_str(value, "module"),
        "full_name": _require_str(value, "full_name"),
        "kind": _require_str(value, "kind"),
        "type_dependencies": _require_names(value, "type_dependencies"),
        "value_dependencies": _require_names(value, "value_dependencies"),
    }


def parse_fact_log(text: str) -> list[dict[str, object]]:
    by_name: dict[str, dict[str, object]] = {}
    for line in text.splitlines():
        marker = line.find(FACT_MARKER)
        if marker < 0:
            continue
        payload = line[marker + len(FACT_MARKER) :].strip()
        fact = _canonical_fact(json.loads(payload))
        name = str(fact["full_name"])
        previous = by_name.get(name)
        if previous is not None and previous != fact:
            raise ValueError(f"conflicting semantic fact for declaration: {name}")
        by_name[name] = fact
    if not by_name:
        raise ValueError("semantic fact log contains no facts")
    return sorted(by_name.values(), key=lambda row: (str(row["module"]), str(row["full_name"])))


def load_fact_jsonl(path: Path) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            facts.append(_canonical_fact(json.loads(line)))
    if not facts:
        raise ValueError("semantic fact jsonl contains no facts")
    names = [str(row["full_name"]) for row in facts]
    if len(set(names)) != len(names):
        raise ValueError("semantic fact jsonl contains duplicate declarations")
    return sorted(facts, key=lambda row: (str(row["module"]), str(row["full_name"])))


def _plugin_edges(facts: Iterable[dict[str, object]]) -> set[tuple[str, str, str]]:
    rows = list(facts)
    local_names = {str(row["full_name"]) for row in rows}
    edges: set[tuple[str, str, str]] = set()
    for row in rows:
        dependent = str(row["full_name"])
        for dependency in row["type_dependencies"]:
            if dependency in local_names:
                edges.add((dependent, dependency, "type_dependency"))
        for dependency in row["value_dependencies"]:
            if dependency in local_names:
                edges.add((dependent, dependency, "value_dependency"))
    return edges


def _leandepviz_resolver(raw_nodes: list[object]):
    modules_by_full: dict[str, str] = {}
    by_short: dict[str, set[str]] = {}
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            raise ValueError(f"LeanDepViz node[{index}] must be an object")
        full = item.get("fullName")
        short = item.get("name")
        module = item.get("module")
        if not all(isinstance(value, str) and value for value in (full, short, module)):
            raise ValueError(f"LeanDepViz node[{index}] has invalid identity fields")
        if full in modules_by_full:
            raise ValueError(f"duplicate LeanDepViz fullName: {full}")
        modules_by_full[full] = module
        by_short.setdefault(short, set()).add(full)

    def resolve(endpoint: object) -> str | None:
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("LeanDepViz edge endpoint must be a non-empty string")
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


def _leandepviz_edges(
    raw: dict[str, object], local_names: set[str]
) -> set[tuple[str, str, str]]:
    raw_nodes = raw.get("nodes")
    raw_edges = raw.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise ValueError("LeanDepViz raw graph must contain nodes and edges arrays")
    resolve = _leandepviz_resolver(raw_nodes)
    edges: set[tuple[str, str, str]] = set()
    relation = {"type": "type_dependency", "value": "value_dependency"}
    for index, item in enumerate(raw_edges):
        if not isinstance(item, dict):
            raise ValueError(f"LeanDepViz edge[{index}] must be an object")
        kind = item.get("kind")
        if kind not in relation:
            continue
        dependency = resolve(item.get("source"))
        dependent = resolve(item.get("target"))
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
    facts: list[dict[str, object]],
    raw: dict[str, object],
    *,
    source_commit: str,
    root_modules: tuple[str, ...],
) -> dict[str, object]:
    if not source_commit:
        raise ValueError("source commit must be non-empty")
    if not root_modules:
        raise ValueError("root modules must be non-empty")
    plugin_edges = _plugin_edges(facts)
    local_names = {str(row["full_name"]) for row in facts}
    leandepviz_edges = _leandepviz_edges(raw, local_names)
    common = plugin_edges & leandepviz_edges
    plugin_only = plugin_edges - leandepviz_edges
    leandepviz_only = leandepviz_edges - plugin_edges
    return {
        "schema": "theseus.riemann-semantic-plugin-comparison.v1",
        "source_commit": source_commit,
        "root_modules": list(root_modules),
        "plugin_facts": len(facts),
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
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in facts),
        encoding="utf-8",
    )
    print(json.dumps({"semantic_facts": len(facts)}, sort_keys=True))
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    facts = load_fact_jsonl(args.facts)
    raw = json.loads(args.leandepviz.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("LeanDepViz raw graph must be a JSON object")
    result = compare_plugin_facts_to_leandepviz(
        facts,
        raw,
        source_commit=args.source_commit,
        root_modules=tuple(args.root_module),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded Riemann semantic-plugin pilot helpers.")
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

    compare = sub.add_parser("compare")
    compare.add_argument("--facts", type=Path, required=True)
    compare.add_argument("--leandepviz", type=Path, required=True)
    compare.add_argument("--source-commit", required=True)
    compare.add_argument("--root-module", action="append", required=True)
    compare.add_argument("--out", type=Path, required=True)
    compare.set_defaults(func=_cmd_compare)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"RIEMANN_SEMANTIC_PLUGIN_PILOT_BLOCKED: {exc}", file=__import__("sys").stderr)
        raise SystemExit(2)
