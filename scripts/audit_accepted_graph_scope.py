"""Read-only accepted-artifact raw/normalized scope inventory for issue #46."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from scripts.consume_accepted_release import verify_and_extract_release
from theseus_repo_search.accepted_catalog import load_accepted_catalog
from theseus_repo_search.artifact import load_artifact
from theseus_repo_search.json_contract import strict_json_loads


def inspect_one(item, repository):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        args = ["--repo", repository]
        subprocess.run(["gh", "release", "verify", item.release_tag, *args], check=True)
        subprocess.run(["gh", "release", "download", item.release_tag, *args,
                        "--pattern", item.package_asset, "--pattern", item.receipt_asset,
                        "--dir", str(p)], check=True)
        for name in (item.package_asset, item.receipt_asset):
            subprocess.run(["gh", "release", "verify-asset", item.release_tag,
                            str(p/name), *args], check=True)
        verification = verify_and_extract_release(
            archive=p/item.package_asset, receipt=p/item.receipt_asset,
            source_descriptor=Path(item.source_descriptor),
            runner_config=Path(item.runner_config or "producer/runner.json"),
            dest=p/"verified")
        assert verification["artifact_identity"] == item.artifact_identity
        assert verification["fingerprint_sha256"] == item.fingerprint_sha256
        a = p/"verified/artifact"
        manifest, nodes, edges, sources = load_artifact(a)
        raw = strict_json_loads((a/"raw-depgraph.json").read_text())
        ids = {n.id for n in nodes}
        rc = Counter(n["module"] for n in raw["nodes"])
        gc = Counter(n.module for n in nodes)
        missing = [n for n in raw["nodes"] if "lean:"+n["fullName"] not in ids]
        lexical = {x.source_path for x in sources}
        bound = {n.source_path for n in nodes if n.source_path}
        return {
            "status": "PASS", "source_id": item.source_id, "identity": item.artifact_identity,
            "producer": manifest.producer.kind, "evidence": verification["evidence"],
            "roots": list(manifest.scope.root_modules),
            "raw_nodes": len(raw["nodes"]), "raw_edges": len(raw["edges"]),
            "graph_nodes": len(nodes), "graph_edges": len(edges),
            "raw_modules": len(rc), "graph_modules": len(gc),
            "excluded_modules": {m: rc[m] for m in sorted(set(rc)-set(gc))},
            "missing_within_retained_modules": [
                {"name": n["fullName"], "module": n["module"], "kind": n["kind"]}
                for n in missing if n["module"] in gc],
            "unbound_graph_nodes": sum(n.source_path is None for n in nodes),
            "lexical_paths": len(lexical), "graph_source_paths": len(bound),
            "intersection_paths": len(lexical & bound),
        }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, default=6)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    assert 0 <= a.shard < a.shards
    c = load_accepted_catalog(Path("producer/accepted-corpora.json"))
    items = c.corpora[a.shard::a.shards]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in items:
        try:
            row = inspect_one(item, c.release_repository)
        except Exception as exc:
            row = {"status": "UNKNOWN", "source_id": item.source_id,
                   "identity": item.artifact_identity,
                   "reason": f"{type(exc).__name__}: {exc}"[:500]}
        rows.append(row)
        a.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2)+"\n")
        print(json.dumps({k:v for k,v in row.items() if k not in
                         ("excluded_modules","missing_within_retained_modules")}), flush=True)
    return int(any(r["status"] != "PASS" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
