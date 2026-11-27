"""Build or independently verify split projections from an accepted full fingerprint."""
import argparse
import json
from pathlib import Path

from theseus_repo_search.split_projection import (
    build_split_projection,
    verify_split_projection,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    build = sub.add_parser("build")
    build.add_argument("--full", type=Path, required=True)
    for child in (build, sub.add_parser("verify")):
        child.add_argument("--out", type=Path, required=True)
        child.add_argument("--full-fingerprint", required=True)
    args = parser.parse_args()
    if args.operation == "build":
        receipt = build_split_projection(args.full, args.out, args.full_fingerprint)
    else:
        receipt = verify_split_projection(args.out, args.full_fingerprint)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
