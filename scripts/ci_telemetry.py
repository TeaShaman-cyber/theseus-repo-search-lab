#!/usr/bin/env python3
"""Emit one bounded procfs-based CI telemetry snapshot.

The collector is intentionally one-shot and standard-library-only. The workflow owns
sampling cadence so QA can enforce a conservative interval and prevent accidental
busy-loop instrumentation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path


def _read_fields(path: Path, separator: str | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        if separator is None:
            parts = line.split()
            if len(parts) >= 2:
                result[parts[0]] = parts[1]
        elif separator in line:
            key, value = line.split(separator, 1)
            result[key.strip()] = value.strip()
    return result


def _mem_available_kib(proc_root: Path) -> int:
    fields = _read_fields(proc_root / "meminfo", ":")
    raw = fields.get("MemAvailable", "0").split()[0]
    try:
        return int(raw)
    except ValueError:
        return 0


def _vmstat(proc_root: Path) -> tuple[int, int]:
    fields = _read_fields(proc_root / "vmstat")
    def number(key: str) -> int:
        try:
            return int(fields.get(key, "0"))
        except ValueError:
            return 0
    return number("pgmajfault"), number("workingset_refault_file")


def _psi_avg10(proc_root: Path) -> tuple[str, str]:
    try:
        lines = (proc_root / "pressure" / "memory").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return "NA", "NA"
    values = {"some": "NA", "full": "NA"}
    for line in lines:
        parts = line.split()
        if not parts or parts[0] not in values:
            continue
        for token in parts[1:]:
            if token.startswith("avg10="):
                values[parts[0]] = token.split("=", 1)[1]
                break
    return values["some"], values["full"]


def _lean_processes(proc_root: Path) -> tuple[int, int]:
    workers = 0
    rss_kib = 0
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return workers, rss_kib
    for entry in entries:
        if not entry.name.isdigit() or not entry.is_dir():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if comm != "lean":
            continue
        workers += 1
        fields = _read_fields(entry / "status", ":")
        raw = fields.get("VmRSS", "0").split()[0]
        try:
            rss_kib += int(raw)
        except ValueError:
            pass
    return workers, rss_kib


def _disk_free_kib(path: Path) -> int:
    try:
        stat = os.statvfs(path)
    except OSError:
        return 0
    return (stat.f_bavail * stat.f_frsize) // 1024


def snapshot(*, phase: str, elapsed_seconds: int, proc_root: Path, disk_path: Path) -> dict[str, str | int]:
    pgmajfault, refault = _vmstat(proc_root)
    psi_some, psi_full = _psi_avg10(proc_root)
    lean_workers, lean_rss_kib = _lean_processes(proc_root)
    return {
        "ts": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "phase": phase,
        "elapsed_seconds": elapsed_seconds,
        "mem_available_kib": _mem_available_kib(proc_root),
        "root_free_kib": _disk_free_kib(disk_path),
        "pgmajfault": pgmajfault,
        "workingset_refault_file": refault,
        "memory_psi_some_avg10": psi_some,
        "memory_psi_full_avg10": psi_full,
        "lean_workers": lean_workers,
        "lean_rss_kib": lean_rss_kib,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--phase", required=True)
    snap.add_argument("--elapsed-seconds", type=int, required=True)
    snap.add_argument("--proc-root", type=Path, default=Path("/proc"))
    snap.add_argument("--disk-path", type=Path, default=Path("/"))
    args = parser.parse_args()

    observed = snapshot(
        phase=args.phase,
        elapsed_seconds=args.elapsed_seconds,
        proc_root=args.proc_root,
        disk_path=args.disk_path,
    )
    print("telemetry " + " ".join(f"{key}={value}" for key, value in observed.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
