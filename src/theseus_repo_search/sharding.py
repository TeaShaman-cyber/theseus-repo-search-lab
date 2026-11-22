from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypedDict


class RawDepgraph(TypedDict):
    nodes: list[dict[str, object]]
    edges: list[dict[str, object]]


@dataclass(frozen=True, order=True)
class ModuleWeight:
    module: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.module:
            raise ValueError("module must be non-empty")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")


@dataclass(frozen=True)
class ModuleShard:
    index: int
    modules: tuple[ModuleWeight, ...]
    total_bytes: int


def partition_modules(modules: Iterable[ModuleWeight], shard_count: int) -> tuple[ModuleShard, ...]:
    items = list(modules)
    if shard_count <= 0 or shard_count > len(items):
        raise ValueError("shard_count must be between 1 and the number of modules")

    bins: list[list[ModuleWeight]] = [[] for _ in range(shard_count)]
    totals = [0] * shard_count

    for item in sorted(items, key=lambda x: (-x.size_bytes, x.module)):
        index = min(range(shard_count), key=lambda i: (totals[i], i))
        bins[index].append(item)
        totals[index] += item.size_bytes

    return tuple(
        ModuleShard(
            index=index,
            modules=tuple(sorted(bucket, key=lambda x: x.module)),
            total_bytes=totals[index],
        )
        for index, bucket in enumerate(bins)
    )


def _require_rows(raw: dict[str, object], key: str) -> list[dict[str, object]]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise TypeError(f"raw dependency graph {key} must be a list")
    rows: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise TypeError(f"raw dependency graph {key}[{index}] must be an object")
        rows.append(item)
    return rows


def merge_raw_depgraph_shards(shards: Iterable[dict[str, object]]) -> RawDepgraph:
    nodes_by_name: dict[str, dict[str, object]] = {}
    edges_by_key: dict[tuple[str, str, str], dict[str, object]] = {}

    for shard in shards:
        for row in _require_rows(shard, "nodes"):
            full_name = row.get("fullName")
            if not isinstance(full_name, str) or not full_name:
                raise ValueError("raw dependency graph node.fullName must be non-empty")
            existing = nodes_by_name.get(full_name)
            if existing is not None and existing != row:
                existing_without_module = {
                    key: value for key, value in existing.items() if key != "module"
                }
                row_without_module = {
                    key: value for key, value in row.items() if key != "module"
                }
                existing_module = existing.get("module")
                row_module = row.get("module")
                if (
                    existing_without_module != row_without_module
                    or not isinstance(existing_module, str)
                    or not existing_module
                    or not isinstance(row_module, str)
                    or not row_module
                ):
                    raise ValueError(f"conflicting duplicate node: {full_name}")
                chosen = min(existing_module, row_module)
                if chosen != existing_module:
                    canonical = dict(row)
                    canonical["module"] = chosen
                    nodes_by_name[full_name] = canonical
                continue
            nodes_by_name[full_name] = dict(row)

        for row in _require_rows(shard, "edges"):
            source = row.get("source")
            target = row.get("target")
            kind = row.get("kind")
            if (
                not isinstance(source, str)
                or not source
                or not isinstance(target, str)
                or not target
                or not isinstance(kind, str)
                or not kind
            ):
                raise ValueError("raw dependency graph edge source/target/kind must be non-empty")
            key = (source, target, kind)
            existing = edges_by_key.get(key)
            if existing is not None and existing != row:
                raise ValueError(f"conflicting duplicate edge: {key}")
            edges_by_key[key] = dict(row)

    return {
        "nodes": [nodes_by_name[key] for key in sorted(nodes_by_name)],
        "edges": [
            edges_by_key[key]
            for key in sorted(edges_by_key, key=lambda x: (x[0], x[1], x[2]))
        ],
    }
