"""Build or independently verify split projections from an accepted full fingerprint."""
import argparse
import json
import os
from pathlib import Path

from theseus_repo_search.accepted_catalog import load_accepted_catalog
from theseus_repo_search.local_graph_cache import (
    prepare_catalog_graph,
    prepare_local_graph,
)
from theseus_repo_search.split_intake import materialize_corpus
from theseus_repo_search.split_projection import (
    build_split_projection,
    verify_split_projection,
)
from theseus_repo_search.split_turso import split_projection_sha256
from theseus_repo_search.turso_materializer import TursoHttpClient


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
    for operation in ("prepare-corpus", "materialize-corpus"):
        corpus = sub.add_parser(operation)
        corpus.add_argument("--catalog", type=Path, required=True)
        corpus.add_argument("--cache-dir", type=Path, required=True)
        corpus.add_argument("--source-id", required=True)
        corpus.add_argument("--duckdb", default="duckdb")
        if operation == "materialize-corpus":
            corpus.add_argument("--organization", required=True)
            corpus.add_argument("--group", required=True)
            corpus.add_argument("--platform-base-url", default="https://api.turso.tech")
            corpus.add_argument("--platform-token-env")
            corpus.add_argument("--motherduck-launcher", type=Path, required=True)
            corpus.add_argument("--motherduck-db", required=True)
    args = parser.parse_args()
    if args.operation in ("prepare-corpus", "materialize-corpus"):
        catalog = load_accepted_catalog(args.catalog)
        kwargs = {"repository_root": args.catalog.resolve().parent.parent,
                  "cache_root": args.cache_dir, "executable": args.duckdb}
        if args.operation == "prepare-corpus":
            receipt = prepare_catalog_graph(catalog, args.source_id, **kwargs)
            split = receipt["split"]
            if not isinstance(split, dict):
                raise ValueError("invalid prepared split receipt")
            receipt["split_projection_sha256"] = split_projection_sha256(split)
        else:
            token = os.environ.get(args.platform_token_env) if args.platform_token_env else None
            if args.platform_token_env and not token:
                parser.error("configured Turso token environment variable is empty")
            if not args.motherduck_launcher.is_file():
                parser.error("configured MotherDuck launcher does not exist")
            client = TursoHttpClient(organization=args.organization,
                                     platform_base_url=args.platform_base_url,
                                     platform_token=token)
            receipt = materialize_corpus(
                catalog, args.source_id, **kwargs, client=client, group=args.group,
                launcher=args.motherduck_launcher, database=args.motherduck_db,
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
