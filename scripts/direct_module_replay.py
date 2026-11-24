"""Bounded, diagnostic replay of a complete simple Lean 4.36 import closure.

Not an accepted artifact producer. The caller binds input records to verified
module files and a successfully importable environment. Equality of observed
facts is not kernel/proof equality. Unsupported cases fail closed.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.riemann_semantic_plugin_pilot import (
    SemanticFact,
    _canonical_fact,
    _plugin_canonical_id,
)
from theseus_repo_search.sharding import RawDepgraph


@dataclass(frozen=True)
class Replay:
    module_order: list[str]
    occurrences: dict[str, list[str]]
    graph: RawDepgraph
    selected_modules: dict[str, str | None]
    name_attribution: dict[str, str]
    omitted_dependencies: list[tuple[str, str, str, str]]


KINDS = {
    'axiom': 'axiom', 'definition': 'def', 'theorem': 'thm', 'opaque': 'opaque',
    'inductive': 'inductive', 'constructor': 'ctor', 'recursor': 'rec', 'quotient': 'quot',
}


def _names(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(n, str) or not n for n in value):
        raise ValueError(f'invalid {field}')
    return list(value)


def theorem_verifier(command: Sequence[str], module_files: Mapping[str, Path]) -> Callable[[SemanticFact, SemanticFact], None]:
    """Bind the isolated native result to both supplied, verified fact rows."""
    def verify(previous: SemanticFact, incoming: SemanticFact) -> None:
        env = os.environ.copy()
        env.pop('LEAN_PATH', None)
        proc = subprocess.run([
            *command, '--resolve-theorem', previous['full_name'],
            previous['module'], str(module_files[previous['module']].resolve()),
            incoming['module'], str(module_files[incoming['module']].resolve()),
        ], env=env, check=True, capture_output=True, text=True, timeout=60)
        result = json.loads(proc.stdout)
        if (not isinstance(result, dict) or result.get('schema') != 'theseus.lean-theorem-resolution.v1'
                or _canonical_fact(result.get('previous')) != previous
                or _canonical_fact(result.get('selected')) != incoming):
            raise ValueError('theorem resolution does not bind supplied facts')
    return verify


def replay_closed_modules(
    modules: Iterable[tuple[object, Iterable[object]]], roots: Sequence[str],
    *, verify_theorem: Callable[[SemanticFact, SemanticFact], None] | None = None,
    private_split: bool = False,
) -> Replay:
    """Replay complete private closures, with optional explicit name inventory.

    In private mode Lean ignores import visibility annotations, includes all
    transitive module data and uses the full IR name inventory for split modules.
    Internal kernel names are classified by the reader, not guessed from missing
    fact rows. We retain first name attribution and record omitted visible-to-
    hidden/IR-only dependencies; we do not claim their transitive proof graph.

    First visited materialization determines observed attribution in this
    restricted scope. Equal dependency signatures permit a diagnostic graph;
    differing signatures require actual Lean constant-info selection evidence.
    Explicit private split mode requires IR inventory and verification of every duplicate.
    Without a verifier, equal-signature duplicates have unknown selected proof
    module (None), even though their diagnostic edges agree.
    No assertion of proof identity or full RH applicability is made.
    """
    imports: dict[str, list[str]] = {}
    facts: dict[str, list[SemanticFact]] = {}
    inventories: dict[str, tuple[list[str], list[str], list[str]]] = {}
    quoted_names: dict[str, str] = {}
    for meta, values in modules:
        if not isinstance(meta, dict) or meta.get('schema') != 'theseus.lean-module-metadata.v1':
            raise ValueError('invalid module metadata schema')
        module = meta.get('module')
        if not isinstance(module, str) or not module:
            raise ValueError('invalid module name')
        if module in imports:
            raise ValueError(f'duplicate module: {module}')
        if meta.get('is_module') is not False:
            if meta.get('is_module') is not True or not private_split:
                raise ValueError(f'unsupported split module or missing flag: {module}')
            if meta.get('ir_extra_const_names') is None:
                raise ValueError(f'missing IR inventory: {module}')
        inventory_mode = private_split and 'internal_constant_names' in meta
        olean_extra = _names(meta.get('olean_extra_const_names'), 'extra constant names')
        ir_extra = (_names(meta['ir_extra_const_names'], 'IR extra constant names')
                    if meta.get('ir_extra_const_names') is not None else [])
        if not inventory_mode:
            if ir_extra:
                raise ValueError(f'unsupported IR extra constant names: {module}')
            if olean_extra:
                raise ValueError(f'unsupported extra constant names: {module}')
        # Lean ImportedModule.irData? uses full IR for split modules in
        # private mode, and main module data for legacy modules.
        extra = ir_extra if meta['is_module'] else olean_extra
        raw_imports = meta.get('imports')
        if not isinstance(raw_imports, list):
            raise TypeError(f'invalid imports: {module}')
        direct: list[str] = []
        for item in raw_imports:
            if (not isinstance(item, dict) or not isinstance(item.get('module'), str)
                    or not item['module']
                    or any(type(item.get(k)) is not bool for k in ('is_exported', 'import_all', 'is_meta'))
                    or (not inventory_mode and (item['is_exported'] is not True
                        or item['import_all'] is not False or item['is_meta'] is not False))):
                raise ValueError(f'unsupported import metadata: {module}')
            direct.append(item['module'])
        rows = [_canonical_fact(v) for v in values]
        if type(meta.get('fact_count')) is not int or meta['fact_count'] != len(rows):
            raise ValueError(f'fact count mismatch: {module}')
        if any(r['module'] != module for r in rows):
            raise ValueError(f'fact module mismatch: {module}')
        names = [r['full_name'] for r in rows]
        if len(set(names)) != len(names):
            raise ValueError(f'duplicate fact: {module}')
        declared = _names(meta.get('constant_names'), 'constant names')
        internal = _names(meta['internal_constant_names'], 'internal constant names') if inventory_mode else []
        if (len(set(declared)) != len(declared) or len(set(internal)) != len(internal)
                or set(internal) & set(names) or set(declared) != set(names) | set(internal)):
            raise ValueError(f'unrepresented constants or inconsistent inventory: {module}')
        expected_quoted = {n for n in names if any(c in n for c in '«»')}
        quoted = meta.get('quoted_short_names', {}) if inventory_mode else {}
        if (not isinstance(quoted, dict) or set(quoted) != expected_quoted
                or any(not isinstance(v, str) or not v for v in quoted.values())):
            raise ValueError(f'unsupported or incomplete quoted name metadata: {module}')
        for name, short in quoted.items():
            if quoted_names.setdefault(name, short) != short:
                raise ValueError(f'conflicting quoted name metadata: {name}')
        for row in rows:
            if row['kind'] not in KINDS:
                raise ValueError(f'unsupported constant kind: {row["full_name"]}')
        imports[module] = direct
        facts[module] = rows
        inventories[module] = (declared, internal, extra)

    if not roots or len(set(roots)) != len(roots):
        raise ValueError('expected nonempty unique roots')
    order: list[str] = []
    done: set[str] = set()
    visiting: set[str] = set()
    # Postorder, matching the private legacy import traversal. Iterative to
    # avoid recursion limits; preserve each serialized import order.
    stack = [(root, False) for root in reversed(roots)]
    while stack:
        module, finish = stack.pop()
        if module in done:
            continue
        if finish:
            visiting.remove(module)
            done.add(module)
            order.append(module)
            continue
        if module in visiting:
            raise ValueError(f'import cycle: {module}')
        if module not in imports:
            raise ValueError(f'missing module: {module}')
        visiting.add(module)
        stack.append((module, True))
        stack.extend((dep, False) for dep in reversed(imports[module]))
    if done != set(imports):
        raise ValueError('unreachable input modules')

    selected: dict[str, SemanticFact] = {}
    occurrences: dict[str, list[str]] = {}
    attribution: dict[str, str] = {}
    hidden: set[str] = set()
    kernel: set[str] = set()
    for module in order:
        declared, internal, extra = inventories[module]
        hidden.update(internal)
        kernel.update(declared)
        for name in (*declared, *extra):
            attribution.setdefault(name, module)
        for row in facts[module]:
            name = row['full_name']
            occurrences.setdefault(name, []).append(module)
            previous = selected.get(name)
            if previous is None:
                selected[name] = row
                continue
            if verify_theorem is not None:
                if previous['kind'] != 'theorem' or row['kind'] != 'theorem':
                    raise ValueError(f'unsupported duplicate constant kind: {name}')
                verify_theorem(previous, row)
                selected[name] = row
                continue
            if private_split:
                raise ValueError(f'duplicate requires theorem verification: {name}')
            if (previous['kind'], previous['type_dependencies'], previous['value_dependencies']) != (
                row['kind'], row['type_dependencies'], row['value_dependencies'],
            ):
                raise ValueError(f'conflicting observed dependency signatures: {name}')
    if hidden & selected.keys():
        raise ValueError('conflicting internal classification')
    identities = {name: _plugin_canonical_id(_canonical_fact(dict(row, module=attribution[name]))) for name, row in selected.items()}
    if len(set(identities.values())) != len(identities):
        raise ValueError('colliding graph identities')
    nodes: list[dict[str, object]] = []
    edges: set[tuple[str, str, str]] = set()
    omitted: set[tuple[str, str, str, str]] = set()
    for name in sorted(selected, key=identities.__getitem__):
        row = selected[name]
        target = identities[name]
        nodes.append({'fullName': target, 'name': quoted_names.get(name, name.rsplit('.', 1)[-1]),
                      'module': attribution[name], 'kind': KINDS[row['kind']]})
        for field, kind in (('type_dependencies', 'type'), ('value_dependencies', 'value')):
            dependencies = row['type_dependencies'] if field == 'type_dependencies' else row['value_dependencies']
            for dep in dependencies:
                if dep not in identities:
                    if dep in hidden:
                        omitted.add((name, dep, kind, 'internal'))
                        continue
                    if dep in attribution and dep not in kernel:
                        omitted.add((name, dep, kind, 'ir_only'))
                        continue
                    raise ValueError(f'unknown dependency: {dep}')
                edges.add((identities[dep], target, kind))
    return Replay(order, occurrences, {'nodes': nodes, 'edges': [
        {'source': a, 'target': b, 'kind': k} for a, b, k in sorted(edges)
    ]}, {name: row['module'] if verify_theorem is not None or len(occurrences[name]) == 1 else None
         for name, row in selected.items()}, attribution, sorted(omitted))
