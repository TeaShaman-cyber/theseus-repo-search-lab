from __future__ import annotations

import json
import re
from itertools import pairwise

from .errors import RepoSearchError

_META_PREFIX = "window.FLT_META="
_EDGES_RE = re.compile(
    r"^window\.FLT_EDGES=\{off:(\[[0-9,]*\]),dst:(\[[0-9,]*\])\};\s*$"
)
_EXPECTED_ROOT = "fermat_last_theorem"


def _integrity(message: str) -> RepoSearchError:
    return RepoSearchError("BLOCKED_ARTIFACT_INTEGRITY", message)


def _parse_meta(text: str) -> dict[str, object]:
    stripped = text.strip()
    if not stripped.startswith(_META_PREFIX) or not stripped.endswith(";"):
        raise _integrity("invalid Anthropic FLT meta.js wrapper")
    try:
        value = json.loads(stripped[len(_META_PREFIX) : -1])
    except json.JSONDecodeError as exc:
        raise _integrity(f"invalid Anthropic FLT meta.js JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise _integrity("Anthropic FLT meta.js payload must be an object")
    return value


def _parse_edges(text: str) -> tuple[list[int], list[int]]:
    match = _EDGES_RE.fullmatch(text.strip())
    if match is None:
        raise _integrity("invalid Anthropic FLT edges.js wrapper")
    try:
        off = json.loads(match.group(1))
        dst = json.loads(match.group(2))
    except json.JSONDecodeError as exc:
        raise _integrity(f"invalid Anthropic FLT edges.js arrays: {exc}") from exc
    if not isinstance(off, list) or not isinstance(dst, list):
        raise _integrity("Anthropic FLT edges arrays must be lists")
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in off + dst):
        raise _integrity("Anthropic FLT edges arrays must contain integers")
    return off, dst


def _theorem_module(full_name: str) -> str:
    if not full_name or any(ch in full_name for ch in ("/", "\\", "\x00")):
        raise _integrity(f"unsafe Anthropic FLT theorem name: {full_name!r}")
    return "Theorems.Thm_" + full_name.replace(".", "_")


def parse_static_export(meta_text: str, edges_text: str) -> dict[str, object]:
    meta = _parse_meta(meta_text)
    if meta.get("v") != 1:
        raise _integrity("unsupported Anthropic FLT static export version")

    names = meta.get("names")
    root = meta.get("root")
    if not isinstance(names, list) or not names:
        raise _integrity("Anthropic FLT meta.names must be a non-empty array")
    if not all(isinstance(name, str) and name for name in names):
        raise _integrity("Anthropic FLT meta.names must contain non-empty strings")
    if len(set(names)) != len(names):
        raise _integrity("Anthropic FLT meta.names contains duplicates")
    if isinstance(root, bool) or not isinstance(root, int) or not 0 <= root < len(names):
        raise _integrity("Anthropic FLT meta.root is out of range")
    if names[root] != _EXPECTED_ROOT:
        raise _integrity(
            f"Anthropic FLT root mismatch: expected {_EXPECTED_ROOT}, observed {names[root]}"
        )

    off, dst = _parse_edges(edges_text)
    if len(off) != len(names) + 1:
        raise _integrity("Anthropic FLT edge offsets length does not match theorem count")
    if not off or off[0] != 0:
        raise _integrity("Anthropic FLT edge offsets must start at zero")
    if any(value < 0 for value in off) or any(a > b for a, b in pairwise(off)):
        raise _integrity("Anthropic FLT edge offsets must be non-negative and monotone")
    if off[-1] != len(dst):
        raise _integrity("Anthropic FLT final edge offset does not match destination count")
    if any(index < 0 or index >= len(names) for index in dst):
        raise _integrity("Anthropic FLT dependency index is out of range")

    nodes = [
        {
            "module": _theorem_module(full_name),
            "fullName": full_name,
            "name": full_name.rsplit(".", 1)[-1],
            "kind": "theorem",
        }
        for full_name in names
    ]
    edges: list[dict[str, str]] = []
    for dependent_index, dependent in enumerate(names):
        for dependency_index in dst[off[dependent_index] : off[dependent_index + 1]]:
            edges.append(
                {
                    "source": names[dependency_index],
                    "target": dependent,
                    "kind": "static",
                }
            )

    return {"nodes": nodes, "edges": edges}
