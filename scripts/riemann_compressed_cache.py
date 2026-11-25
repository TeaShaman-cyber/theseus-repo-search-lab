#!/usr/bin/env python3
"""Restore verified RH archives in bounded batches into read-only SquashFS roots.

Run the pinned upstream reader while roots are mounted. All batch build/cache
data and images are disposable; project build and Mathlib packages stay untouched.
No new graph semantics, recompile, or artifact publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from scripts.restore_riemann_ci_cache_subset import (
    _load_upstream,
    _read_modules,
    download_subset,
    restore_environment,
    subset_mapping_bytes,
    validated_selection,
)

SUFFIXES = (".olean", ".olean.server", ".olean.private")


def batch_rows(rows: list[list[str]], size: int) -> Iterator[list[list[str]]]:
    if size < 1:
        raise ValueError("batch size must be positive")
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def ensure_space(path: Path, reserve_bytes: int, reserve_inodes: int) -> None:
    state = os.statvfs(path)
    if state.f_bavail * state.f_frsize < reserve_bytes:
        raise ValueError("disk reserve breached")
    if state.f_favail < reserve_inodes:
        raise ValueError("inode reserve breached")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def part_hashes(root: Path, modules: Sequence[str]) -> dict[str, str]:
    expected: set[str] = set()
    for module in modules:
        components = module.split(".")
        if any(not x or x in (".", "..") or "/" in x or "\\" in x for x in components):
            raise ValueError("unsafe module path")
        base = "/".join(components)
        for suffix in SUFFIXES:
            expected.add(base + suffix)
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink in restored tree: {path}")
        if path.is_file() and path.name.endswith(SUFFIXES):
            result[path.relative_to(root).as_posix()] = digest(path)
    required = {p for p in expected if p.endswith(".olean")}
    missing = sorted(required - result.keys())
    if missing:
        raise ValueError(f"missing olean files: {missing[:5]}")
    extra = sorted(result.keys() - expected)
    if extra:
        raise ValueError(f"unexpected olean parts: {extra[:5]}")
    # Legacy modules have no sidecars; split modules must have both.
    # The pinned upstream reader independently checks the format header.
    for main in required:
        if (main + ".server" in result) != (main + ".private" in result):
            raise ValueError(f"missing paired olean sidecar: {main}")
    return result


class SpaceGuard:
    def __init__(self, path: Path, reserve_bytes: int, reserve_inodes: int):
        self.path, self.reserve_bytes, self.reserve_inodes = (
            path,
            reserve_bytes,
            reserve_inodes,
        )
        self.minimum_free = shutil.disk_usage(path).free

    def check(self) -> None:
        ensure_space(self.path, self.reserve_bytes, self.reserve_inodes)
        self.minimum_free = min(self.minimum_free, shutil.disk_usage(self.path).free)

    def run(self, argv: list[str], *, input: str | None = None, **kwargs: Any) -> None:
        """Terminate the owned child process group before consuming the reserve."""
        self.check()
        kwargs.pop("check", None)
        with subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input is not None else None,
            start_new_session=True,
            text=True,
            **kwargs,
        ) as process:
            try:
                try:
                    process.communicate(input=input, timeout=1)
                except subprocess.TimeoutExpired:
                    while True:
                        self.check()
                        try:
                            process.communicate(timeout=1)
                            break
                        except subprocess.TimeoutExpired:
                            continue
                self.check()
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, argv)
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                raise


def restore_batch(
    ci,
    identity,
    project: Path,
    cache: Path,
    build: Path,
    rows: list[list[str]],
    contents: bytes,
    guard: SpaceGuard,
    workers: int,
    lean: Path,
) -> dict[str, object]:
    env = restore_environment(project, cache, identity)
    env["RUST_MIN_STACK"] = str(max(int(env.get("RUST_MIN_STACK", "0")), 8388608))
    start = time.monotonic()
    config = download_subset(
        project,
        cache,
        subset_mapping_bytes(contents, rows),
        ci.REPOSITORY,
        env,
        lake=str(lean.with_name("lake")),
        run=guard.run,
    )
    download_s = time.monotonic() - start
    jobs = ci.restore.restore_jobs(project, cache, rows)
    # Preserve the upstream validated source/archive association and redirect
    # only its two extraction bases into an owned, empty batch directory.
    build.mkdir()
    for job in jobs:
        job["base"] = [
            str(build / Path(base).relative_to(project / ".lake/build"))
            for base in job["base"]
        ]
        for base in job["base"]:
            Path(base).mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    guard.run(
        [str(lean.with_name("leantar")), "--jobs", str(workers), "-f", "-x", "-j", "-"],
        input=json.dumps(jobs),
        cwd=project,
        env=env,
    )
    return {
        "download_s": download_s,
        "unpack_s": time.monotonic() - start,
        "transport_config_sha256": config,
        "archive_bytes": sum(
            p.stat().st_size for p in (cache / "artifacts").glob("*.ltar")
        ),
    }


def compress_batch(
    tree: Path,
    modules: list[str],
    work: Path,
    index: int,
    guard: SpaceGuard,
    workers: int,
    mounted: list[Path],
) -> tuple[Path, dict[str, object]]:
    hashes = part_hashes(tree, modules)
    stage = work / f"stage-{index:04d}"
    stage.mkdir()
    logical = 0
    for relative in sorted(hashes):
        source = tree / relative
        dest = stage / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, dest)
        logical += source.stat().st_size
    image = work / f"part-{index:04d}.sqfs"
    mount = work / f"mount-{index:04d}"
    mount.mkdir()
    start = time.monotonic()
    guard.run(
        [
            "mksquashfs",
            str(stage),
            str(image),
            "-noappend",
            "-comp",
            "zstd",
            "-Xcompression-level",
            "3",
            "-processors",
            str(workers),
            "-no-progress",
            "-quiet",
            "-no-xattrs",
        ]
    )
    compress_s = time.monotonic() - start
    subprocess.run(
        [
            "sudo",
            "-n",
            "mount",
            "-t",
            "squashfs",
            "-o",
            "loop,ro,nodev,nosuid,noexec",
            str(image),
            str(mount),
        ],
        check=True,
    )
    mounted.append(mount)
    start = time.monotonic()
    if part_hashes(mount, modules) != hashes:
        raise ValueError("mounted olean byte or coverage mismatch")
    verify_s = time.monotonic() - start
    shutil.rmtree(stage)
    return mount, {
        "batch": index,
        "modules": len(modules),
        "parts": len(hashes),
        "olean_bytes": logical,
        "image_bytes": image.stat().st_size,
        "image_sha256": digest(image),
        "parts_sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "compress_s": compress_s,
        "verify_s": verify_s,
    }


def reader_command(
    args, roots: list[str], mounted: list[Path], output: Path
) -> list[str]:
    command = [str(args.reader), "-j", str(args.jobs)]
    for mount in mounted:
        command.extend(["-L", str(mount)])
    command.extend(["deps", *roots, "--trust", args.trust, "-o", str(output)])
    if args.constant is not None:
        command.extend(["-c", args.constant])
    return command


def canonical_graph_hash(path: Path) -> tuple[int, str]:
    # Control only, bounded 1740-module corpus. Ignore ordering, not occurrences.
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            row["uses"] = sorted(row["uses"])
            rows.append(
                json.dumps(
                    row, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
            )
    h = hashlib.sha256()
    for row in sorted(rows):
        h.update(row.encode() + b"\n")
    return len(rows), h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--modules-file", type=Path, required=True)
    p.add_argument("--roots-file", type=Path, required=True)
    p.add_argument("--reader", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--metrics-out", type=Path, required=True)
    p.add_argument("--work-dir", type=Path, required=True)
    p.add_argument("--trust", required=True)
    p.add_argument("--constant")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--batch-size", type=int, default=4096)
    p.add_argument("--reserve-gib", type=int, default=5)
    p.add_argument("--compare-plain", action="store_true")
    p.add_argument("--native-reader", type=Path)
    args = p.parse_args()
    if args.jobs < 1 or args.batch_size < 1 or args.reserve_gib < 1:
        raise ValueError("positive workers, batch size and reserve required")
    project = args.project.resolve()
    args.reader = args.reader.resolve()
    args.output = args.output.resolve()
    args.metrics_out = args.metrics_out.resolve()
    args.work_dir = args.work_dir.resolve()
    if args.native_reader is not None:
        args.native_reader = args.native_reader.resolve()
    ci = _load_upstream(project)
    modules = _read_modules(args.modules_file, ci.restore.MODULE)
    roots = args.roots_file.read_text().splitlines()
    if not roots or not set(roots) <= modules or len(roots) != len(set(roots)):
        raise ValueError("invalid roots for verified closure")
    if args.compare_plain and len(modules) > 4096:
        raise ValueError("plain parity control must be bounded")
    if args.native_reader is not None and roots != ["Main"]:
        raise ValueError("native reader control requires Main root")
    if args.output.exists() or args.metrics_out.exists():
        raise ValueError("output or receipt already exists")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    build = project / ".lake/build"
    ci.restore.local_path(project, Path(".lake/build"), set())
    if build.exists() and any(p.is_file() or p.is_symlink() for p in build.rglob("*")):
        raise ValueError("project build must not shadow verified compressed roots")
    project.joinpath(".lake").mkdir(exist_ok=True)
    if (
        len(
            {
                os.stat(p).st_dev
                for p in (
                    args.work_dir,
                    project,
                    args.output.parent,
                    args.metrics_out.parent,
                )
            }
        )
        != 1
    ):
        raise ValueError("work, project and evidence must share filesystem")
    guard = SpaceGuard(project, args.reserve_gib * 1024**3, 50000)
    guard.check()
    selection = validated_selection(project, modules, 100)
    if selection is None:
        raise ValueError("no equivalent verified cache record")
    ci, identity, record, contents, all_rows, selected = selection
    lean = Path(
        subprocess.check_output(
            ["elan", "which", "lean"], cwd=project, text=True
        ).strip()
    )
    reader_env = os.environ.copy()  # caller uses pinned lake env
    if not reader_env.get("LEAN_PATH"):
        raise ValueError("run under lake env for dependency search paths")
    progress = args.metrics_out.with_suffix(".batches.jsonl")
    receipt: dict[str, Any] = {
        "schema": "theseus.rh-compressed-cache.v1",
        "candidate_head": record["candidate_head"],
        "map_sha256": record["map_sha256"],
        "module_manifest_sha256": record["module_manifest_sha256"],
        "selected_project_modules": len(selected),
        "validated_manifest_modules": len(all_rows),
        "toolchain": identity["toolchain"],
        "source_identity": identity,
        "reader_sha256": digest(args.reader),
        "roots": roots,
        "batch_size": args.batch_size,
        "free_before": shutil.disk_usage(project).free,
        "status": "INCOMPLETE",
        "batches": [],
    }
    start = time.monotonic()
    with tempfile.TemporaryDirectory(
        prefix="compressed-", dir=args.work_dir
    ) as temporary:
        work = Path(temporary)
        build = work / "batch-build"
        mounted: list[Path] = []
        try:
            if args.compare_plain:
                cache = work / "plain-cache"
                restore_batch(
                    ci,
                    identity,
                    project,
                    cache,
                    build,
                    selected,
                    contents,
                    guard,
                    args.jobs,
                    lean,
                )
                plain = work / "plain.jsonl"
                t = time.monotonic()
                guard.run(
                    reader_command(args, roots, [build / "lib/lean"], plain),
                    cwd=project,
                    env=reader_env,
                )
                receipt["plain_reader_s"] = time.monotonic() - t
                receipt["plain_graph"] = canonical_graph_hash(plain)
                shutil.rmtree(build)
                shutil.rmtree(cache)
                plain.unlink()
            for index, batch in enumerate(batch_rows(selected, args.batch_size)):
                cache = work / f"cache-{index:04d}"
                metrics = restore_batch(
                    ci,
                    identity,
                    project,
                    cache,
                    build,
                    batch,
                    contents,
                    guard,
                    args.jobs,
                    lean,
                )
                _, image_metrics = compress_batch(
                    build / "lib/lean",
                    [r[0] for r in batch],
                    work,
                    index,
                    guard,
                    args.jobs,
                    mounted,
                )
                shutil.rmtree(build)
                shutil.rmtree(cache)
                metrics.update(image_metrics)
                metrics["free_after_cleanup"] = shutil.disk_usage(project).free
                receipt["batches"].append(metrics)
                with progress.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(metrics, sort_keys=True) + "\n")
                print(json.dumps(metrics, sort_keys=True), flush=True)
            if ci.current_identity(project) != identity:
                raise ValueError("project inputs changed during batch restoration")
            if sum(b["modules"] for b in receipt["batches"]) != len(selected):
                raise ValueError("batch coverage mismatch")
            t = time.monotonic()
            guard.run(
                reader_command(args, roots, mounted, args.output),
                cwd=project,
                env=reader_env,
            )
            receipt["compressed_reader_s"] = time.monotonic() - t
            receipt["graph_sha256"] = digest(args.output)
            if args.compare_plain:
                receipt["compressed_graph"] = canonical_graph_hash(args.output)
                if receipt["compressed_graph"] != receipt["plain_graph"]:
                    raise ValueError("compressed graph differs from plain graph")
            if args.native_reader is not None:
                paths = [
                    m / "Main.olean" for m in mounted if (m / "Main.olean").is_file()
                ]
                if len(paths) != 1:
                    raise ValueError("missing or ambiguous Main module")
                env = reader_env.copy()
                env.pop("LEAN_PATH", None)
                with (args.output.parent / "Main.jsonl").open("w") as out:
                    guard.run(
                        [
                            str(lean),
                            "--run",
                            str(args.native_reader),
                            "Main",
                            str(paths[0]),
                            "--metadata",
                            str(args.output.parent / "Main.json"),
                        ],
                        cwd=project,
                        env=env,
                        stdout=out,
                    )
            receipt["status"] = "GRAPH_COMPLETE"
        finally:
            for mount in reversed(mounted):
                subprocess.run(["sudo", "-n", "umount", str(mount)], check=True)
                if (
                    subprocess.run(
                        ["findmnt", "-n", "--mountpoint", str(mount)],
                        stdout=subprocess.DEVNULL,
                        check=False,
                    ).returncode
                    == 0
                ):
                    raise ValueError("mount cleanup failed")
            if build.exists():
                shutil.rmtree(build)
            if receipt["status"] == "GRAPH_COMPLETE":
                receipt["status"] = "PASS"
            receipt["mount_cleanup_verified"] = True
            receipt["minimum_free_bytes"] = guard.minimum_free
            receipt["wall_s"] = time.monotonic() - start
            receipt["image_bytes"] = sum(b["image_bytes"] for b in receipt["batches"])
            receipt["olean_bytes"] = sum(b["olean_bytes"] for b in receipt["batches"])
            args.metrics_out.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            )
    print(
        json.dumps(
            {k: v for k, v in receipt.items() if k != "batches"}, sort_keys=True
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
