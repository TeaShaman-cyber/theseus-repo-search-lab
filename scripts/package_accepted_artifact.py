#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.model import ArtifactManifestV2
from theseus_repo_search.producer_config import load_lean_git_source, load_runner_pins

RECEIPT_SCHEMA = "theseus.repo-search-accepted-artifact-release-receipt.v1"
FINGERPRINT_SCHEMA = "theseus.repo-search-accepted-artifact-fingerprint.v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_line(data: dict[str, object]) -> bytes:
    return (
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _load_json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{label} JSON root must be an object")
    return value


def _package_member_bytes(
    *,
    artifact: Path,
    replay: bytes,
    source_descriptor: bytes,
    runner_config: bytes,
) -> list[tuple[str, bytes]]:
    members: list[tuple[str, bytes]] = []
    for path in sorted(artifact.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"artifact package refuses symlink: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"artifact package member is not a regular file: {path}")
        relative = path.relative_to(artifact).as_posix()
        members.append((f"artifact/{relative}", path.read_bytes()))
    members.extend(
        [
            ("replay.json", replay),
            ("runner.json", runner_config),
            ("source-descriptor.json", source_descriptor),
        ]
    )
    return sorted(members, key=lambda item: item[0])


def _deterministic_tar_gz(members: list[tuple[str, bytes]]) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tar.addfile(info, io.BytesIO(data))

    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        compresslevel=9,
        fileobj=compressed,
        mtime=0,
    ) as gz:
        gz.write(tar_buffer.getvalue())
    return compressed.getvalue()


def build_release_package(
    *,
    artifact: Path,
    replay: Path,
    source_descriptor: Path,
    runner_config: Path,
    archive: Path,
    receipt: Path,
) -> dict[str, object]:
    manifest, *_ = load_artifact(artifact)
    if isinstance(manifest, ArtifactManifestV2):
        raise TypeError("release package pilot currently supports git-backed repo-index.v1 only")

    source = load_lean_git_source(source_descriptor)
    runner = load_runner_pins(runner_config)
    if (
        manifest.source_repo != source.source_repo
        or manifest.source_commit != source.source_commit
        or manifest.source_subdir != source.source_subdir
        or tuple(manifest.scope.root_modules) != tuple(source.root_modules)
    ):
        raise ValueError("source descriptor does not match accepted artifact provenance/scope")
    if (
        manifest.producer.tool_repo != runner.extractor_repo
        or manifest.producer.tool_commit != runner.extractor_commit
        or manifest.producer.tool_hash != runner.extractor_main_sha256
    ):
        raise ValueError("runner config does not match accepted artifact producer pin")

    identity = artifact_identity(manifest)
    replay_bytes = replay.read_bytes()
    replay_payload = _load_json_object(replay_bytes, "replay")
    if replay_payload.get("status") != "PASS":
        raise ValueError("release package requires replay status PASS")
    if replay_payload.get("artifact_identity") != identity:
        raise ValueError("replay artifact identity does not match accepted artifact")

    authority_path = artifact / "authority-receipt.json"
    authority_bytes = authority_path.read_bytes()
    authority = _load_json_object(authority_bytes, "authority receipt")
    observed = authority.get("observed")
    if not isinstance(observed, dict) or not isinstance(observed.get("lean_toolchain"), str):
        raise TypeError("authority receipt must contain observed lean_toolchain")

    descriptor_bytes = source_descriptor.read_bytes()
    runner_bytes = runner_config.read_bytes()
    manifest_bytes = (artifact / "manifest.json").read_bytes()

    fingerprint_payload: dict[str, object] = {
        "schema": FINGERPRINT_SCHEMA,
        "source_descriptor_sha256": _sha256(descriptor_bytes),
        "runner_config_sha256": _sha256(runner_bytes),
        "authority_receipt_sha256": _sha256(authority_bytes),
        "artifact_manifest_sha256": _sha256(manifest_bytes),
        "artifact_identity": identity,
        "replay_sha256": _sha256(replay_bytes),
    }
    fingerprint_sha256 = _sha256(_json_line(fingerprint_payload))

    members = _package_member_bytes(
        artifact=artifact,
        replay=replay_bytes,
        source_descriptor=descriptor_bytes,
        runner_config=runner_bytes,
    )
    package_bytes = _deterministic_tar_gz(members)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(package_bytes)

    receipt_payload: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "source_descriptor": {
            "sha256": _sha256(descriptor_bytes),
            "source_id": source.source_id,
            "repo": source.source_repo,
            "commit": source.source_commit,
            "subdir": source.source_subdir,
        },
        "runner_config": {"sha256": _sha256(runner_bytes)},
        "producer": {
            "kind": manifest.producer.kind,
            "tool_repo": manifest.producer.tool_repo,
            "tool_commit": manifest.producer.tool_commit,
            "tool_hash": manifest.producer.tool_hash,
            "lean_toolchain": observed["lean_toolchain"],
        },
        "artifact": {
            "schema": manifest.schema,
            "identity": identity,
            "manifest_sha256": _sha256(manifest_bytes),
            "authority_receipt_sha256": _sha256(authority_bytes),
        },
        "replay": {
            "status": "PASS",
            "artifact_identity": identity,
            "sha256": _sha256(replay_bytes),
        },
        "fingerprint": {
            "schema": FINGERPRINT_SCHEMA,
            "sha256": fingerprint_sha256,
        },
        "package": {
            "format": "tar.gz",
            "sha256": _sha256(package_bytes),
            "size_bytes": len(package_bytes),
            "members": [
                {"path": name, "sha256": _sha256(data), "size_bytes": len(data)}
                for name, data in members
            ],
        },
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_bytes(_json_line(receipt_payload))
    return receipt_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic accepted-artifact release package")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--source-descriptor", type=Path, required=True)
    parser.add_argument("--runner-config", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    payload = build_release_package(
        artifact=args.artifact,
        replay=args.replay,
        source_descriptor=args.source_descriptor,
        runner_config=args.runner_config,
        archive=args.archive,
        receipt=args.receipt,
    )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
