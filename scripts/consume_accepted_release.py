#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path, PurePosixPath

from theseus_repo_search.artifact import (
    artifact_identity,
    canonical_artifact_member_names,
    load_artifact,
)
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.evidence_class import release_replay_evidence

RELEASE_RECEIPT_SCHEMA = "theseus.repo-search-accepted-artifact-release-receipt.v1"
FINGERPRINT_SCHEMA = "theseus.repo-search-accepted-artifact-fingerprint.v1"
CONSUMER_SCHEMA = "theseus.repo-search-release-consumer-verification.v1"
_METADATA_MEMBERS = {
    "consumer-receipt.json",
    "replay.json",
    "runner.json",
    "source-descriptor.json",
}


class RebuildRequired(ValueError):
    """Current producer inputs no longer match the accepted artifact fingerprint."""


class ReleaseEvidenceInvalid(ValueError):
    """Release evidence is missing, malformed, or unverifiable; reuse must fail closed."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{label} JSON root must be an object")
    return value


def _canonical_json_line(data: dict[str, object]) -> bytes:
    return (
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _member_map(receipt: dict[str, object]) -> dict[str, dict[str, object]]:
    package = receipt.get("package")
    if not isinstance(package, dict):
        raise TypeError("release receipt package must be an object")
    members = package.get("members")
    if not isinstance(members, list) or not members:
        raise TypeError("release receipt package members must be a non-empty list")

    result: dict[str, dict[str, object]] = {}
    for entry in members:
        if not isinstance(entry, dict):
            raise TypeError("release receipt member must be an object")
        if set(entry) != {"path", "sha256", "size_bytes"}:
            raise ValueError("release receipt member fields mismatch")
        name = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("size_bytes")
        if not isinstance(name, str) or not name:
            raise ValueError("release receipt member path must be non-empty text")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
            raise ValueError(f"unsafe release member path: {name}")
        if name in result:
            raise ValueError(f"duplicate release member path: {name}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise ValueError(f"invalid release member sha256: {name}")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"invalid release member size: {name}")
        result[name] = entry
    return result


def _verified_archive_members(
    archive_bytes: bytes, expected: dict[str, dict[str, object]]
) -> dict[str, bytes]:
    observed: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
                    raise ValueError(f"unsafe archive member path: {name}")
                if not member.isfile():
                    raise ValueError(f"release archive member is not a regular file: {name}")
                if name in observed:
                    raise ValueError(f"duplicate archive member path: {name}")
                fileobj = tar.extractfile(member)
                if fileobj is None:
                    raise ValueError(f"cannot read archive member: {name}")
                data = fileobj.read()
                observed[name] = data
    except tarfile.TarError as exc:
        raise ValueError(f"invalid accepted-artifact archive: {exc}") from exc

    if set(observed) != set(expected):
        extra = sorted(set(observed) - set(expected))
        missing = sorted(set(expected) - set(observed))
        raise ValueError(f"release archive member set mismatch: extra={extra} missing={missing}")

    for name, data in observed.items():
        entry = expected[name]
        if _sha256(data) != entry["sha256"] or len(data) != entry["size_bytes"]:
            raise ValueError(f"release archive member digest/size mismatch: {name}")
    return observed


def _require_section(receipt: dict[str, object], name: str) -> dict[str, object]:
    section = receipt.get(name)
    if not isinstance(section, dict):
        raise TypeError(f"release receipt {name} must be an object")
    return section


def _verify_and_extract_release(
    *,
    archive: Path,
    receipt: Path,
    source_descriptor: Path,
    runner_config: Path,
    dest: Path,
) -> dict[str, object]:
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("release extraction destination must be empty")

    receipt_bytes = receipt.read_bytes()
    payload = _load_json_object(receipt_bytes, "release receipt")
    if payload.get("schema") != RELEASE_RECEIPT_SCHEMA:
        raise ValueError("unsupported release receipt schema")

    package = _require_section(payload, "package")
    if package.get("format") != "tar.gz":
        raise ValueError("unsupported release package format")
    archive_bytes = archive.read_bytes()
    if package.get("sha256") != _sha256(archive_bytes):
        raise ValueError("release package sha256 mismatch")
    if package.get("size_bytes") != len(archive_bytes):
        raise ValueError("release package size mismatch")

    source = _require_section(payload, "source_descriptor")
    runner = _require_section(payload, "runner_config")
    source_bytes = source_descriptor.read_bytes()
    runner_bytes = runner_config.read_bytes()
    if source.get("sha256") != _sha256(source_bytes):
        raise RebuildRequired("REBUILD_REQUIRED: current source descriptor differs from accepted producer fingerprint")
    if runner.get("sha256") != _sha256(runner_bytes):
        raise RebuildRequired("REBUILD_REQUIRED: current runner config differs from accepted producer fingerprint")

    members = _member_map(payload)
    if not _METADATA_MEMBERS.issubset(members):
        raise ValueError("release package is missing required metadata members")
    observed = _verified_archive_members(archive_bytes, members)
    if observed["source-descriptor.json"] != source_bytes:
        raise ValueError("packaged source descriptor differs from current accepted descriptor")
    if observed["runner.json"] != runner_bytes:
        raise ValueError("packaged runner config differs from current accepted runner")

    acceptance = _require_section(payload, "acceptance")
    replay = _require_section(payload, "replay")
    artifact_section = _require_section(payload, "artifact")
    fingerprint = _require_section(payload, "fingerprint")
    if acceptance.get("consumer_receipt_sha256") != _sha256(observed["consumer-receipt.json"]):
        raise ValueError("packaged consumer receipt binding mismatch")
    if replay.get("sha256") != _sha256(observed["replay.json"]):
        raise ValueError("packaged replay binding mismatch")

    manifest_bytes = observed.get("artifact/manifest.json")
    authority_bytes = observed.get("artifact/authority-receipt.json")
    if manifest_bytes is None or authority_bytes is None:
        raise ValueError("release package is missing canonical artifact metadata")
    if artifact_section.get("manifest_sha256") != _sha256(manifest_bytes):
        raise ValueError("artifact manifest binding mismatch")
    if artifact_section.get("authority_receipt_sha256") != _sha256(authority_bytes):
        raise ValueError("artifact authority receipt binding mismatch")

    fingerprint_payload: dict[str, object] = {
        "schema": FINGERPRINT_SCHEMA,
        "source_descriptor_sha256": source.get("sha256"),
        "runner_config_sha256": runner.get("sha256"),
        "authority_receipt_sha256": artifact_section.get("authority_receipt_sha256"),
        "artifact_manifest_sha256": artifact_section.get("manifest_sha256"),
        "artifact_identity": artifact_section.get("identity"),
        "replay_sha256": replay.get("sha256"),
        "consumer_receipt_sha256": acceptance.get("consumer_receipt_sha256"),
        "accepted_repository_head": acceptance.get("repository_head"),
    }
    if fingerprint.get("schema") != FINGERPRINT_SCHEMA:
        raise ValueError("unsupported accepted-artifact fingerprint schema")
    observed_fingerprint = _sha256(_canonical_json_line(fingerprint_payload))
    if fingerprint.get("sha256") != observed_fingerprint:
        raise ValueError("accepted-artifact fingerprint mismatch")

    # Reject dishonest class metadata before materializing any archive member.
    replay_payload = _load_json_object(observed["replay.json"], "replay")
    replay_evidence = release_replay_evidence(replay_payload)
    if "evidence" in payload and payload["evidence"] != replay_evidence:
        raise ValueError("release receipt evidence disagrees with packaged replay")

    dest.mkdir(parents=True, exist_ok=True)
    for name, data in observed.items():
        target = dest.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    artifact_dir = dest / "artifact"
    manifest, *_ = load_artifact(artifact_dir)
    identity = artifact_identity(manifest)
    if artifact_section.get("identity") != identity:
        raise ValueError("extracted artifact identity mismatch")
    expected_artifact_names = set(canonical_artifact_member_names(manifest))
    actual_artifact_names = {path.name for path in artifact_dir.iterdir()}
    if actual_artifact_names != expected_artifact_names:
        raise ValueError("extracted artifact member set is not canonical")

    consumer = _load_json_object(observed["consumer-receipt.json"], "consumer receipt")
    if consumer.get("schema") != "theseus.repo-search-consumer-receipt.v2" or consumer.get("result") != "PASS":
        raise ValueError("packaged consumer receipt is not accepted v2 evidence")
    if _require_section(consumer, "artifact").get("identity") != identity:
        raise ValueError("packaged consumer receipt artifact identity mismatch")
    if replay_payload.get("status") != "PASS" or replay_payload.get("artifact_identity") != identity:
        raise ValueError("packaged replay is not accepted evidence")

    return {
        "schema": CONSUMER_SCHEMA,
        "status": "PASS",
        "artifact_identity": identity,
        "evidence": replay_evidence,
        "accepted_repository_head": acceptance.get("repository_head"),
        "fingerprint_sha256": observed_fingerprint,
        "package_sha256": _sha256(archive_bytes),
        "source_descriptor_sha256": _sha256(source_bytes),
        "runner_config_sha256": _sha256(runner_bytes),
    }


def verify_and_extract_release(
    *,
    archive: Path,
    receipt: Path,
    source_descriptor: Path,
    runner_config: Path,
    dest: Path,
) -> dict[str, object]:
    try:
        return _verify_and_extract_release(
            archive=archive,
            receipt=receipt,
            source_descriptor=source_descriptor,
            runner_config=runner_config,
            dest=dest,
        )
    except RebuildRequired:
        raise
    except (FileNotFoundError, TypeError, ValueError, RepoSearchError) as exc:
        raise ReleaseEvidenceInvalid(f"RELEASE_EVIDENCE_INVALID: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify and extract an immutable accepted-artifact release package"
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--source-descriptor", type=Path, required=True)
    parser.add_argument("--runner-config", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = verify_and_extract_release(
        archive=args.archive,
        receipt=args.receipt,
        source_descriptor=args.source_descriptor,
        runner_config=args.runner_config,
        dest=args.dest,
    )
    encoded = _canonical_json_line(result)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(encoded)
    print(encoded.decode("utf-8").rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
