from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.producer_config import (
    LeanArchiveSource,
    load_lean_archive_source,
)

RECEIPT_SCHEMA = "theseus.archive-materialization-receipt.v1"
MEMBER_MANIFEST_SCHEMA = "theseus.archive-member-manifest.v1"


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
        if file_type == stat.S_IFREG and member.is_dir():
            raise _blocked(f"archive member type conflicts with directory marker: {name}")
        if file_type == stat.S_IFDIR and not member.is_dir():
            raise _blocked(f"archive member type conflicts with directory marker: {name}")
    return members


def _extract_zip(archive_path: Path, dest: Path) -> tuple[list[tuple[str, str]], str]:
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
    file_hashes.sort()
    payload = json.dumps(file_hashes, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return file_hashes, sha256(payload).hexdigest()


def member_manifest_path_for_receipt(receipt_path: Path) -> Path:
    receipt_path = receipt_path.resolve()
    stem = receipt_path.name.removesuffix(receipt_path.suffix) if receipt_path.suffix else receipt_path.name
    return receipt_path.with_name(f"{stem}.members.json")


def _source_identity(source: LeanArchiveSource) -> dict[str, object]:
    return {
        "kind": "archive",
        "url": source.archive_url,
        "sha256": source.archive_sha256,
        "format": source.archive_format,
        "subdir": source.source_subdir,
    }


def _member_manifest_bytes(
    source: LeanArchiveSource,
    *,
    file_hashes: list[tuple[str, str]],
    source_root_relative: str,
) -> bytes:
    source_prefix = PurePosixPath(source_root_relative) if source_root_relative != "." else PurePosixPath()
    members: list[dict[str, object]] = []
    for path, digest in file_hashes:
        archive_path = PurePosixPath(path)
        source_path: str | None = None
        try:
            relative = archive_path.relative_to(source_prefix) if source_prefix.parts else archive_path
        except ValueError:
            pass
        else:
            source_path = relative.as_posix()
        members.append({"path": path, "source_path": source_path, "sha256": digest})
    payload: dict[str, object] = {
        "schema": MEMBER_MANIFEST_SCHEMA,
        "source": _source_identity(source),
        "source_root_relative": source_root_relative,
        "members": members,
    }
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _read_json_object(path: Path, label: str) -> tuple[dict[str, object], bytes]:
    try:
        data = path.read_bytes()
        value = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _blocked(f"cannot load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise _blocked(f"{label} must be a JSON object")
    return value, data


def verify_materialized_archive_members(
    source: LeanArchiveSource,
    *,
    dest: Path,
    receipt_path: Path,
) -> dict[str, object]:
    dest = dest.resolve()
    receipt_path = receipt_path.resolve()
    member_manifest_path = member_manifest_path_for_receipt(receipt_path)

    receipt, _ = _read_json_object(receipt_path, "archive materialization receipt")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise _blocked("unsupported archive materialization receipt schema")
    if receipt.get("source") != _source_identity(source):
        raise _mismatch("materialization receipt source does not match archive descriptor")
    materialized = receipt.get("materialized")
    if not isinstance(materialized, dict):
        raise _blocked("materialization receipt materialized section must be an object")

    source_root_relative = materialized.get("source_root_relative")
    file_count = materialized.get("file_count")
    tree_sha256 = materialized.get("tree_sha256")
    member_manifest_sha256 = materialized.get("member_manifest_sha256")
    if not isinstance(source_root_relative, str) or not source_root_relative:
        raise _blocked("materialization receipt source_root_relative must be a string")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 0:
        raise _blocked("materialization receipt file_count must be non-negative")
    for label, digest in (
        ("tree_sha256", tree_sha256),
        ("member_manifest_sha256", member_manifest_sha256),
    ):
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise _blocked(f"materialization receipt {label} must be SHA-256 hex")

    source_root = source.resolve_source_root(dest)
    observed_source_root = source_root.relative_to(dest).as_posix() or "."
    if observed_source_root != source_root_relative:
        raise _mismatch(
            "materialization source root mismatch: "
            f"expected {source_root_relative}, observed {observed_source_root}"
        )

    member_manifest, member_manifest_bytes = _read_json_object(
        member_manifest_path, "archive member manifest"
    )
    if sha256(member_manifest_bytes).hexdigest() != member_manifest_sha256:
        raise _mismatch("archive member manifest digest mismatch")
    if member_manifest.get("schema") != MEMBER_MANIFEST_SCHEMA:
        raise _blocked("unsupported archive member manifest schema")
    if member_manifest.get("source") != _source_identity(source):
        raise _mismatch("archive member manifest source does not match archive descriptor")
    if member_manifest.get("source_root_relative") != source_root_relative:
        raise _mismatch("archive member manifest source root mismatch")

    members = member_manifest.get("members")
    if not isinstance(members, list):
        raise _blocked("archive member manifest members must be an array")

    file_hashes: list[tuple[str, str]] = []
    seen: set[str] = set()
    source_prefix = (
        PurePosixPath(source_root_relative)
        if source_root_relative != "."
        else PurePosixPath()
    )
    for item in members:
        if not isinstance(item, dict) or set(item) != {"path", "source_path", "sha256"}:
            raise _blocked("archive member manifest entry has invalid fields")
        path = item.get("path")
        source_path = item.get("source_path")
        digest = item.get("sha256")
        if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
            raise _blocked("archive member manifest path is invalid")
        rel = PurePosixPath(path)
        if rel.is_absolute() or ".." in rel.parts or rel.as_posix() != path:
            raise _blocked(f"archive member manifest path is unsafe: {path}")
        if path in seen:
            raise _blocked(f"duplicate archive member manifest path: {path}")
        seen.add(path)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise _blocked(f"archive member manifest hash is invalid: {path}")

        expected_source_path: str | None = None
        try:
            source_relative = rel.relative_to(source_prefix) if source_prefix.parts else rel
        except ValueError:
            pass
        else:
            expected_source_path = source_relative.as_posix()
        if source_path != expected_source_path:
            raise _mismatch(f"archive member source-root mapping mismatch: {path}")

        target = dest.joinpath(*rel.parts)
        try:
            mode = target.lstat().st_mode
        except OSError as exc:
            raise _mismatch(f"authoritative archive member missing: {path}") from exc
        if not stat.S_ISREG(mode):
            raise _mismatch(f"authoritative archive member type changed: {path}")
        try:
            actual_digest = sha256(target.read_bytes()).hexdigest()
        except OSError as exc:
            raise _mismatch(f"cannot read authoritative archive member: {path}") from exc
        if actual_digest != digest:
            raise _mismatch(f"authoritative archive member hash mismatch: {path}")
        file_hashes.append((path, digest))

    if file_hashes != sorted(file_hashes):
        raise _blocked("archive member manifest paths must use deterministic sorted order")
    if len(file_hashes) != file_count:
        raise _mismatch("archive member manifest file count mismatch")
    tree_payload = json.dumps(
        file_hashes, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if sha256(tree_payload).hexdigest() != tree_sha256:
        raise _mismatch("archive member manifest tree digest mismatch")
    expected_manifest_bytes = _member_manifest_bytes(
        source,
        file_hashes=file_hashes,
        source_root_relative=source_root_relative,
    )
    if expected_manifest_bytes != member_manifest_bytes:
        raise _mismatch("archive member manifest is not canonical for verified members")
    return member_manifest


def _stage_metadata_file(target: Path, data: bytes) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


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
    member_manifest_path = member_manifest_path_for_receipt(receipt_path)
    publication_paths = (receipt_path, member_manifest_path)
    for publication_path in publication_paths:
        if (
            publication_path == dest
            or publication_path.is_relative_to(dest)
            or dest.is_relative_to(publication_path)
        ):
            raise _blocked(
                "materialization metadata and destination must be path-disjoint: "
                f"{publication_path} vs {dest}"
            )
        if publication_path.exists():
            raise _blocked(
                f"materialization metadata target already exists: {publication_path}"
            )
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
        file_hashes, tree_sha256 = _extract_zip(archive_path, extracted)
        source_root = source.resolve_source_root(extracted)
        source_root_relative = source_root.relative_to(extracted).as_posix() or "."
        member_manifest_bytes = _member_manifest_bytes(
            source,
            file_hashes=file_hashes,
            source_root_relative=source_root_relative,
        )
        member_manifest_sha256 = sha256(member_manifest_bytes).hexdigest()
        receipt: dict[str, object] = {
            "schema": RECEIPT_SCHEMA,
            "source": _source_identity(source),
            "materialized": {
                "file_count": len(file_hashes),
                "tree_sha256": tree_sha256,
                "member_manifest_sha256": member_manifest_sha256,
                "source_root_relative": source_root_relative,
            },
        }
        receipt_bytes = (
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        temp_receipt = _stage_metadata_file(receipt_path, receipt_bytes)
        try:
            temp_member_manifest = _stage_metadata_file(
                member_manifest_path, member_manifest_bytes
            )
        except OSError as exc:
            temp_receipt.unlink(missing_ok=True)
            raise _blocked(
                f"cannot stage archive materialization metadata: {exc}"
            ) from exc
        published_member_manifest = False
        try:
            if dest.exists():
                dest.rmdir()
            os.replace(extracted, dest)
            try:
                os.link(temp_member_manifest, member_manifest_path)
                published_member_manifest = True
                os.link(temp_receipt, receipt_path)
            except OSError:
                shutil.rmtree(dest, ignore_errors=True)
                if published_member_manifest:
                    member_manifest_path.unlink(missing_ok=True)
                raise
            for temp_path in (temp_receipt, temp_member_manifest):
                try:
                    temp_path.unlink()
                except OSError:
                    pass
        except OSError as exc:
            temp_receipt.unlink(missing_ok=True)
            temp_member_manifest.unlink(missing_ok=True)
            raise _blocked(f"cannot publish archive materialization atomically: {exc}") from exc
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
