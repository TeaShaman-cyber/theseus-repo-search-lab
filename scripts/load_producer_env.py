from __future__ import annotations

import argparse
from pathlib import Path

from theseus_repo_search.producer_config import (
    LeanGitSource,
    RunnerPins,
    load_lean_git_source,
    load_runner_pins,
)


def environment_mapping(source: LeanGitSource, runner: RunnerPins) -> dict[str, str]:
    return {
        "SOURCE_ID": source.source_id,
        "SOURCE_REPO": source.source_repo,
        "SOURCE_COMMIT": source.source_commit,
        "SOURCE_SUBDIR": source.source_subdir,
        "ROOT_MODULES_CSV": ",".join(source.root_modules),
        "EXCLUDE_SOURCE_PREFIXES_CSV": ",".join(source.exclude_source_prefixes),
        "BUILD_TARGET": source.build_target,
        "EXTRACTOR_REPO": runner.extractor_repo,
        "EXTRACTOR_COMMIT": runner.extractor_commit,
        "EXTRACTOR_MAIN_SHA256": runner.extractor_main_sha256,
        "ELAN_VERSION": runner.elan_version,
        "ELAN_SHA256": runner.elan_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--checkout-root", type=Path)
    parser.add_argument("--github-env", type=Path, required=True)
    args = parser.parse_args(argv)
    source = load_lean_git_source(args.source)
    runner = load_runner_pins(args.runner)
    if args.checkout_root is None:
        mapping = environment_mapping(source, runner)
    else:
        source_root = source.resolve_source_root(args.checkout_root)
        mapping = {"SOURCE_ROOT": str(source_root)}
    with args.github_env.open("a", encoding="utf-8") as fh:
        for key in sorted(mapping):
            fh.write(f"{key}={mapping[key]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
