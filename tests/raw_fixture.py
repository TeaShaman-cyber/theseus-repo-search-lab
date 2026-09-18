import json
from hashlib import sha256

from theseus_repo_search.model import Edge, Node


def raw_depgraph_bytes(nodes: list[Node], edges: list[Edge]) -> bytes:
    relation_kind = {
        "value_dependency": "value",
        "type_dependency": "type",
    }
    payload = {
        "nodes": [
            {
                "name": node.name,
                "module": node.module,
                "kind": node.kind,
                "fullName": node.full_name,
            }
            for node in nodes
        ],
        "edges": [
            {
                "source": edge.target_id.removeprefix("lean:"),
                "target": edge.source_id.removeprefix("lean:"),
                "kind": relation_kind[edge.relation],
            }
            for edge in edges
        ],
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def receipt_bytes(source, producer, raw: bytes) -> bytes:
    payload = {
        "schema": "theseus.raw-depgraph-receipt.v2",
        "source": {
            "repo": source.source_repo,
            "commit": source.source_commit,
            "subdir": source.source_subdir,
        },
        "scope": {"root_modules": list(source.root_modules)},
        "producer": {
            "kind": producer.kind,
            "tool_repo": producer.tool_repo,
            "tool_commit": producer.tool_commit,
            "tool_hash": producer.tool_hash,
        },
        "observed": {"lean_toolchain": "leanprover/lean4:test"},
        "raw_depgraph": {"sha256": sha256(raw).hexdigest()},
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
