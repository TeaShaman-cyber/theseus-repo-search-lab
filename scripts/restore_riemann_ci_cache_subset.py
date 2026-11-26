#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from hashlib import sha256
from pathlib import Path


def select_rows(rows: list[list[str]], modules: set[str]) -> list[list[str]]:
    if not modules:
        raise ValueError("project closure must contain at least one module")
    by_name = {row[0]: row for row in rows}
    missing = sorted(modules - set(by_name))
    if missing:
        raise ValueError("missing validated cache rows: " + ", ".join(missing))
    return [row for row in rows if row[0] in modules]


def subset_mapping_bytes(contents: bytes, selected_rows: Iterable[list[str]]) -> bytes:
    lines = contents.splitlines()
    if not lines:
        raise ValueError("validated Lake map is empty")

    selected_hashes = {row[1] for row in selected_rows}
    kept = [lines[0]]
    found: set[str] = set()
    for line in lines[1:]:
        row = json.loads(line)
        if isinstance(row, list) and len(row) == 2 and row[0] in selected_hashes:
            kept.append(line)
            found.add(row[0])

    missing = sorted(selected_hashes - found)
    if missing:
        raise ValueError("missing selected input hashes: " + ", ".join(missing))
    return b"\n".join(kept) + b"\n"


def transport_config(source: str) -> str:
    """Project only the RH S3 transport settings, not its build dependencies.

    The verified source workspace still owns input identity and archive restore.
    Reject unfamiliar service layouts rather than silently dropping settings.
    """
    config = tomllib.loads(source)
    name = config.get("name")
    platform = config.get("platformIndependent", False)
    cache = config.get("cache")
    if not isinstance(name, str) or not name or type(platform) is not bool:
        raise ValueError("invalid transport package configuration")
    if not isinstance(cache, dict) or set(cache) != {"defaultService", "service"}:
        raise ValueError("unsupported cache configuration")
    services = cache["service"]
    if not isinstance(services, list) or len(services) != 1:
        raise ValueError("expected a single cache service")
    service = services[0]
    keys = ("name", "kind", "artifactEndpoint", "revisionEndpoint")
    if (not isinstance(service, dict) or set(service) != set(keys)
            or any(not isinstance(service[k], str) or not service[k] for k in keys)
            or service["name"] != cache["defaultService"] or service["kind"] != "s3"
            or not all(service[k].startswith("https://") for k in keys[2:])):
        raise ValueError("unsupported S3 cache service configuration")
    result = (f"name = {json.dumps(name, ensure_ascii=False)}\n"
              f"platformIndependent = {str(platform).lower()}\n\n"
              f"[cache]\ndefaultService = {json.dumps(cache['defaultService'])}\n\n"
              "[[cache.service]]\n" + "".join(f"{k} = {json.dumps(service[k], ensure_ascii=False)}\n" for k in keys))
    if tomllib.loads(result) != {"name": name, "platformIndependent": platform, "cache": cache}:
        raise ValueError("transport configuration projection mismatch")
    return result


def download_subset(project: Path, cache: Path, subset: bytes, repository: str,
                    env: dict[str, str], *, lake: str = "lake",
                    run: Callable[..., object] | None = None) -> str:
    """Download already validated mappings without materializing packages."""
    config = transport_config((project / "lakefile.toml").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="rh-cache-transport-") as temporary:
        workspace = Path(temporary)
        (workspace / "lakefile.toml").write_text(config, encoding="utf-8")
        (workspace / "lean-toolchain").write_text(env["ELAN_TOOLCHAIN"] + "\n", encoding="utf-8")
        mapping = workspace / "outputs.jsonl"
        mapping.write_bytes(subset)
        transport_env = dict(env, LAKE_CONFIG=str(workspace / "lakefile.toml"),
                             LAKE_CACHE_DIR=str(cache.resolve()))
        (run or subprocess.run)([lake, "-q", "cache", "get", str(mapping), "--repo", repository],
                       cwd=workspace, env=transport_env, check=True)
        packages = workspace / ".lake/packages"
        if packages.exists() and any(packages.iterdir()):
            raise ValueError("transport unexpectedly materialized packages")
    return sha256(config.encode()).hexdigest()


def _load_upstream(project: Path):
    path = project / "scripts" / "ci-cache-get.py"
    if not path.is_file():
        raise ValueError(f"missing upstream cache helper: {path}")
    spec = importlib.util.spec_from_file_location("rh_ci_cache_get", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load upstream cache helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_modules(path: Path, module_pattern) -> set[str]:
    modules: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if not name:
            continue
        if not module_pattern.fullmatch(name):
            raise ValueError(f"invalid project module in closure: {name!r}")
        if name in modules:
            raise ValueError(f"duplicate project module in closure: {name}")
        modules.add(name)
    if not modules:
        raise ValueError("project closure file is empty")
    return modules


def validated_selection(project: Path, modules: set[str], ancestor_limit: int):
    ci = _load_upstream(project)
    identity = ci.current_identity(project)
    record = ci.find_record(project, identity, ancestor_limit)
    cached = {}
    if record is None:
        record, cached = ci.baseline_record(identity)
    if record is None:
        return None

    def fetcher(key, maximum=ci.MAX_JSON):
        if key in cached:
            return cached[key]
        return ci.fetch(key, maximum)

    contents, rows = ci.validated_blobs(record, identity, fetcher)
    selected_rows = select_rows(rows, modules)

    if ci.current_identity(project) != identity:
        raise ValueError("project inputs changed during targeted cache lookup")

    return ci, identity, record, contents, rows, selected_rows


def restore_environment(project: Path, cache: Path, identity) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if (
            key.startswith(("AWS_", "CLOUDFLARE_", "CF_"))
            or key
            in (
                "LAKE_CACHE_KEY",
                "LAKE_CACHE_ARTIFACT_ENDPOINT",
                "LAKE_CACHE_REVISION_ENDPOINT",
                "LAKE_CACHE_SERVICE",
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "HF_TOKEN",
                "LEAN_PATH",
            )
        ):
            env.pop(key)
    env.update(
        LAKE_CONFIG=str(project / "lakefile.toml"),
        LAKE_CACHE_DIR=str(cache),
        LAKE_ARTIFACT_CACHE="true",
        LAKE_RESTORE_ARTIFACTS="true",
        ELAN_TOOLCHAIN=identity["toolchain"],
    )

    return env


def _restore(project: Path, modules: set[str], cache: Path, workers: int, ancestor_limit: int):
    selection = validated_selection(project, modules, ancestor_limit)
    if selection is None:
        return 3, None
    ci, identity, record, contents, rows, selected_rows = selection
    subset = subset_mapping_bytes(contents, selected_rows)
    ci.restore.local_path(project, Path(".lake"), set()).mkdir(exist_ok=True)
    env = restore_environment(project, cache, identity)

    config_sha = download_subset(project, cache, subset, ci.REPOSITORY, env)

    jobs = ci.restore.restore_jobs(project, cache, selected_rows)
    if ci.current_identity(project) != identity:
        raise ValueError("project inputs changed before targeted cache restoration")
    for job in jobs:
        for base in job["base"]:
            Path(base).mkdir(parents=True, exist_ok=True)

    env["RUST_MIN_STACK"] = str(max(int(env.get("RUST_MIN_STACK", "0")), 8388608))
    lean = Path(
        subprocess.check_output(
            ["elan", "which", "lean"], cwd=project, env=env, text=True
        ).strip()
    )
    subprocess.run(
        [str(lean.with_name("leantar")), "--jobs", str(workers), "-f", "-x", "-j", "-"],
        input=json.dumps(jobs),
        text=True,
        cwd=project,
        env=env,
        check=True,
    )

    metrics = {
        "schema": "theseus.riemann-targeted-cache-restore.v1",
        "candidate_head": record["candidate_head"],
        "validated_manifest_modules": len(rows),
        "selected_project_modules": len(selected_rows),
        "selected_mapping_rows": len(subset.splitlines()) - 1,
        "toolchain": identity["toolchain"],
        "transport_config_sha256": config_sha,
    }
    return 0, metrics


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore only a verified RH CI-cache subset for a project-module closure."
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--modules-file", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--jobs", type=int, default=24)
    parser.add_argument("--ancestor-limit", type=int, default=100)
    parser.add_argument("--metrics-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.jobs < 1 or not 1 <= args.ancestor_limit <= 1000:
        raise ValueError("jobs must be positive; ancestor-limit must be 1..1000")

    project = args.project.resolve()
    ci = _load_upstream(project)
    modules = _read_modules(args.modules_file, ci.restore.MODULE)
    cache = (
        args.cache_dir
        or Path(os.environ.get("LAKE_CACHE_DIR", str(project / ".lake/cache")))
    ).resolve()

    code, metrics = _restore(project, modules, cache, args.jobs, args.ancestor_limit)
    if code == 3:
        print("No equivalent successful cache found for the current build inputs.", flush=True)
        return 3

    assert metrics is not None
    payload = json.dumps(metrics, sort_keys=True)
    print(payload, flush=True)
    if args.metrics_out is not None:
        args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_out.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, EOFError, subprocess.CalledProcessError) as exc:
        print(f"TARGETED_RIEMANN_CACHE_RESTORE_BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
