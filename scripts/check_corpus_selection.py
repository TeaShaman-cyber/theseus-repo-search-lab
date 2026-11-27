"""Check changed catalog entries against immutable evidence and live witness receipts."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path

from scripts.consume_accepted_release import verify_and_extract_release
from theseus_repo_search.accepted_catalog import (
    accepted_catalog_sha256,
    load_accepted_catalog,
)
from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.corpus_coverage import coverage_report, validate_live_witness
from theseus_repo_search.json_contract import strict_json_loads

SCHEMA = "theseus.corpus-selection-coverage.v1"
CATALOG = Path("producer/accepted-corpora.json")
PROFILE = Path.home() / ".local/bin/repo-search"


def read_json(path):
    return strict_json_loads(path.read_text(encoding="utf-8"))


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def changed_corpora(base, candidate):
    if base.release_repository != candidate.release_repository:
        raise ValueError("release repository change requires separate authority review")
    old = {x.source_id: x for x in base.corpora}
    return [x for x in candidate.corpora if old.get(x.source_id) != x]


def run_json(args):
    return strict_json_loads(subprocess.check_output(args, text=True))


def validate_context(context, witness, item):
    root = "lean:" + witness["declaration"]
    if (context.get("source_id") != item.source_id
            or context.get("artifact_identity") != item.artifact_identity
            or context.get("status") not in ("FOUND", "DEGRADED")
            or context.get("target_id") != root
            or not any(x.get("declaration_id") == root
                       and x.get("source_path") == witness["source_path"]
                       for x in context.get("chunks", []))):
        raise ValueError("live context did not recover expected source")


def capture_live(catalog_path, item, plan):
    catalog = load_accepted_catalog(catalog_path)
    receipt = {"schema": SCHEMA, "source_id": item.source_id,
               "artifact_identity": item.artifact_identity,
               "fingerprint_sha256": item.fingerprint_sha256,
               "catalog_sha256": accepted_catalog_sha256(catalog),
               "plan_sha256": digest(plan), "plan": plan, "witnesses": []}
    for w in plan["witnesses"]:
        root = "lean:" + w["declaration"]
        args = [str(PROFILE), "query", "--catalog", str(catalog_path.resolve())]
        result = run_json([*args, "graph-search", root, "--source", item.source_id,
                           "--limit", "1", "--mode", "evidence", "--depth", "1",
                           "--graph-limit", "50", *(["--reverse"] if w["direction"] == "reverse" else [])])
        graph = result["graphs"][0] if result.get("graphs") else {}
        validate_live_witness(result, w, item.source_id, item.artifact_identity,
                              graph.get("projection_nodes"), graph.get("projection_edges"))
        context = run_json([*args, "context", item.source_id, root,
                                   "--depth", "1", "--max-edges", "50", "--token-budget", "4000"])
        validate_context(context, w, item)
        # Retain identifiers and observed topology, not full source text.
        for hit in result["hits"]:
            hit.pop("text", None)
        for chunk in context["chunks"]:
            chunk.pop("text", None)
        receipt["witnesses"].append({"graph_search": result, "context": context})
    return receipt


def validate_receipt(receipt, item, catalog, report):
    expected = {"schema": SCHEMA, "source_id": item.source_id,
                "artifact_identity": item.artifact_identity,
                "fingerprint_sha256": item.fingerprint_sha256,
                "catalog_sha256": accepted_catalog_sha256(catalog),
                "plan_sha256": digest(receipt["plan"])}
    if any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError("coverage receipt identity or plan mismatch")
    witnesses = receipt["plan"]["witnesses"]
    if len(receipt["witnesses"]) != len(witnesses):
        raise ValueError("coverage receipt missing live witnesses")
    for w, captured in zip(witnesses, receipt["witnesses"], strict=True):
        result = captured["graph_search"]
        context = captured["context"]
        if any(x.get("catalog_sha256") != expected["catalog_sha256"] for x in (result, context)):
            raise ValueError("live witness catalog mismatch")
        validate_live_witness(result, w, item.source_id, item.artifact_identity,
                              report["graph_nodes"], report["graph_edges"])
        validate_context(context, w, item)


def validate_source_revision(row, expected):
    observed = [row[key] for key in ("source_revision", "source_commit") if key in row]
    if not observed or any(value != expected for value in observed):
        raise ValueError("live witness source revision mismatch")


def verify_selection(item, catalog, receipt, artifact_path):
    manifest, nodes, edges, sources = load_artifact(artifact_path)
    if artifact_identity(manifest) != item.artifact_identity:
        raise ValueError("selection artifact identity mismatch")
    raw = read_json(artifact_path / "raw-depgraph.json")
    report = coverage_report(raw, nodes, edges, sources, receipt["plan"], manifest.producer.kind)
    validate_receipt(receipt, item, catalog, report)
    for captured in receipt["witnesses"]:
        result, context = captured["graph_search"], captured["context"]
        graph = result["graphs"][0]
        hit = result["hits"][0]
        for row in (graph, hit, context):
            validate_source_revision(row, manifest.source_revision)
        if graph.get("producer_kind") != manifest.producer.kind:
            raise ValueError("live witness producer kind mismatch")
    return report


def validate_release_tag(item):
    match = re.fullmatch(r"accepted-artifact/[A-Za-z0-9_-]+/([0-9a-f]{16,64})", item.release_tag)
    if match is None or not item.artifact_identity.startswith(match.group(1)):
        raise ValueError("unsafe or mismatched release identity prefix")


def check_pr(base_sha):
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("base must be an exact commit")
    candidate = load_accepted_catalog(CATALOG)
    with tempfile.TemporaryDirectory() as tmp:
        base_path = Path(tmp) / "base.json"
        base_path.write_bytes(subprocess.check_output(["git", "show", f"{base_sha}:{CATALOG}"]))
        base = load_accepted_catalog(base_path)
        selected = changed_corpora(base, candidate)
        # An unchanged catalog row cannot silently point at new producer inputs.
        changed_ids = {item.source_id for item in selected}
        for item in candidate.corpora:
            if item.source_id in changed_ids:
                continue
            for pin in (item.source_descriptor, item.runner_config or "producer/runner.json"):
                original = subprocess.check_output(["git","show",f"{base_sha}:{pin}"])
                if original != Path(pin).read_bytes():
                    raise ValueError(f"selected producer pin changed without replacement: {item.source_id}: {pin}")
        # Resolve every required receipt before network or heavy work.
        for item in selected:
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", item.source_id):
                raise ValueError("unsafe source id")
            if not (Path("producer/coverage") / (item.source_id+".json")).is_file():
                raise ValueError(f"missing corpus coverage receipt: {item.source_id}")
        for item in selected:
            receipt = read_json(Path("producer/coverage") / (item.source_id+".json"))
            validate_release_tag(item)
            for asset in (item.package_asset, item.receipt_asset):
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", asset) or asset in (".",".."):
                    raise ValueError("unsafe release asset basename")
            with tempfile.TemporaryDirectory() as work_dir:
                work = Path(work_dir)
                repo = ["--repo", candidate.release_repository]
                subprocess.run(["gh","release","verify",item.release_tag,*repo], check=True)
                subprocess.run(["gh","release","download",item.release_tag,*repo,
                                "--pattern",item.package_asset,"--pattern",item.receipt_asset,
                                "--dir",str(work)], check=True)
                for asset in (item.package_asset,item.receipt_asset):
                    subprocess.run(["gh","release","verify-asset",item.release_tag,str(work/asset),*repo], check=True)
                tag = run_json(["gh","api",f"repos/{candidate.release_repository}/git/ref/tags/{item.release_tag}"])
                if tag["object"]["sha"] != read_json(work/item.receipt_asset)["acceptance"]["repository_head"]:
                    raise ValueError("immutable release tag/acceptance mismatch")
                verified = verify_and_extract_release(
                    archive=work/item.package_asset, receipt=work/item.receipt_asset,
                    source_descriptor=Path(item.source_descriptor),
                    runner_config=Path(item.runner_config or "producer/runner.json"),
                    dest=work/"verified")
                if (verified["artifact_identity"],verified["fingerprint_sha256"]) != (item.artifact_identity,item.fingerprint_sha256):
                    raise ValueError("catalog fingerprint mismatch")
                report = verify_selection(item,candidate,receipt,work/"verified/artifact")
                compact = {key: value for key,value in report.items()
                           if key not in {"lexical_only_paths", "excluded_raw_modules", "excluded_raw_declarations"}}
                compact["lexical_only_path_count"] = len(report["lexical_only_paths"])
                compact["coverage_report_sha256"] = digest(report)
                print(json.dumps({"source_id":item.source_id,**compact},sort_keys=True),flush=True)
    print(f"CORPUS_SELECTION_COVERAGE_PASS changed={len(selected)}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command",required=True)
    c = sub.add_parser("check-pr")
    c.add_argument("--base",required=True)
    r = sub.add_parser("record-live")
    r.add_argument("--catalog",type=Path,default=CATALOG)
    r.add_argument("--source-id",required=True)
    r.add_argument("--plan",type=Path,required=True)
    r.add_argument("--out",type=Path,required=True)
    args = p.parse_args()
    if args.command == "check-pr":
        check_pr(args.base)
    else:
        catalog = load_accepted_catalog(args.catalog)
        item = next(x for x in catalog.corpora if x.source_id == args.source_id)
        receipt = capture_live(args.catalog,item,read_json(args.plan))
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(receipt,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
        print("LIVE_CORPUS_WITNESSES_CAPTURED",item.source_id)

if __name__ == "__main__":
    main()
