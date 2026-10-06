from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable

from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.producer_config import LeanArchiveSource, load_lean_archive_source


RECEIPT_SCHEMA = "theseus.archive-materialization-receipt.v1"


def _blocked(message: str) -> RepoSearchError:
    return RepoSearchError("BLOCKED_SOURCE_BINDING", message)


def _mismatch(message: str) -> RepoSearchError:
    return RepoSearchError("BLOCKED_SOURCE_MISMATCH", message)


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    seen: set[str] = set()
    for member in members:
        name = member.filename
        if not name or "\\" in name or "\x00" in name:
            raise _blocked(f"unsafe archive member name: {name!r}")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise _blocked(f"archive member escapes extraction root: {name}")
        normalized = path.as_posix().rstrip("/")
        if normalized in seen:
            raise _blocked(f"duplicate archive member path: {name}")
        if normalized:
            seen.add(normalized)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise _blocked(f"unsupported archive member type: {name}")
    return members


def _extract_zip(archive_path: Path, dest: Path) -> tuple[int, str]:
    file_hashes: list[tuple[str, str]] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_members(archive)
            for member in members:
                rel = PurePosixPath(member.filename)
                target = dest.joinpath(*rel.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(member)
                target.write_bytes(data)
                file_hashes.append((rel.as_posix(), sha256(data).hexdigest()))
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, RepoSearchError):
            raise
        raise _blocked(f"cannot extract verified zip archive: {exc}") from exc
    payload = json.dumps(sorted(file_hashes), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return len(file_hashes), sha256(payload).hexdigest()


def _download(url: str, out: Path, opener: Callable[..., BinaryIO] | None = None) -> str:
    open_url = opener or urllib.request.urlopen
    digest = sha256()
    try:
        with open_url(url, timeout=60) as response, out.open("wb") as fh:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                fh.write(chunk)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise _blocked(f"cannot download archive source: {exc}") from exc
    return digest.hexdigest()


def materialize_archive_source(
    source: LeanArchiveSource,
    *,
    dest: Path,
    receipt_path: Path,
    opener: Callable[..., BinaryIO] | None = None,
) -> dict[str, object]:
    dest = dest.resolve()
    receipt_path = receipt_path.resolve()
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise _blocked(f"materialization destination is not an empty directory: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = dest.parent
    stage = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=stage_parent))
    archive_path = stage / "source.zip"
    extracted = stage / "extracted"
    extracted.mkdir()
    try:
        actual_sha256 = _download(source.archive_url, archive_path, opener=opener)
        if actual_sha256 != source.archive_sha256:
            raise _mismatch(
                f"archive SHA-256 mismatch: expected {source.archive_sha256}, observed {actual_sha256}"
            )
        file_count, tree_sha256 = _extract_zip(archive_path, extracted)
        source_root = source.resolve_source_root(extracted)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "source": {
                "kind": "archive",
                "url": source.archive_url,
                "sha256": source.archive_sha256,
                "format": source.archive_format,
                "subdir": source.source_subdir,
            },
            "materialized": {
                "file_count": file_count,
                "tree_sha256": tree_sha256,
                "source_root_relative": source_root.relative_to(extracted).as_posix() or ".",
            },
        }
        receipt_bytes = (
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        if dest.exists():
            dest.rmdir()
        os.replace(extracted, dest)
        temp_receipt = receipt_path.with_name(f".{receipt_path.name}.tmp-{os.getpid()}")
        temp_receipt.write_bytes(receipt_bytes)
        os.replace(temp_receipt, receipt_path)
        return receipt
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    source = load_lean_archive_source(args.source)
    materialize_archive_source(source, dest=args.dest, receipt_path=args.receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
