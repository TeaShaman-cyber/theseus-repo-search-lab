"""Build or independently verify split projections from an accepted full fingerprint."""
import argparse
import json
from pathlib import Path

from theseus_repo_search.accepted_catalog import load_accepted_catalog
from theseus_repo_search.local_graph_cache import (
    prepare_catalog_graph,
    prepare_local_graph,
)
from theseus_repo_search.split_projection import (
    build_split_projection,
    verify_split_projection,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    build = sub.add_parser("build")
    build.add_argument("--full", type=Path, required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--full", type=Path, required=True)
    prepare.add_argument("--duckdb", default="duckdb")
    for child in (build, prepare, sub.add_parser("verify")):
        child.add_argument("--out", type=Path, required=True)
        child.add_argument("--full-fingerprint", required=True)
    corpus = sub.add_parser("prepare-corpus")
    corpus.add_argument("--catalog", type=Path, required=True)
    corpus.add_argument("--cache-dir", type=Path, required=True)
    corpus.add_argument("--source-id", required=True)
    corpus.add_argument("--duckdb", default="duckdb")
    args = parser.parse_args()
    if args.operation == "prepare-corpus":
        receipt = prepare_catalog_graph(
            load_accepted_catalog(args.catalog), args.source_id,
            repository_root=args.catalog.resolve().parent.parent,
            cache_root=args.cache_dir, executable=args.duckdb,
        )
    elif args.operation == "prepare":
        receipt = prepare_local_graph(args.full, args.out, args.full_fingerprint, executable=args.duckdb)
    elif args.operation == "build":
        receipt = build_split_projection(args.full, args.out, args.full_fingerprint)
    else:
        receipt = verify_split_projection(args.out, args.full_fingerprint)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
