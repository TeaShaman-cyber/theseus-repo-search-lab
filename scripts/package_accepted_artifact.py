#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

from theseus_repo_search.artifact import (
    artifact_identity,
    canonical_artifact_member_names,
    load_artifact,
)
from theseus_repo_search.model import ArtifactManifestV2
from theseus_repo_search.producer_config import LeanGitSource
from theseus_repo_search.replay_contract import (
    validate_registered_replay_manifest,
    validate_registered_replay_provenance,
)

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
    artifact_member_names: tuple[str, ...],
    replay: bytes,
    consumer_receipt: bytes,
    source_descriptor: bytes,
    runner_config: bytes,
) -> list[tuple[str, bytes]]:
    expected = set(artifact_member_names)
    actual_entries = list(artifact.iterdir())
    actual = {path.name for path in actual_entries}
    if actual != expected:
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(
            f"artifact package member set mismatch: extra={extra} missing={missing}"
        )

    members: list[tuple[str, bytes]] = []
    for name in artifact_member_names:
        path = artifact / name
        if path.is_symlink():
            raise ValueError(f"artifact package refuses symlink: {path}")
        if not path.is_file():
            raise ValueError(f"artifact package member is not a regular file: {path}")
        members.append((f"artifact/{name}", path.read_bytes()))
    members.extend(
        [
            ("consumer-receipt.json", consumer_receipt),
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



def _require_commit(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ValueError(f"{label} must be a full lowercase Git commit")
    return value


def _repo_relative(repository_root: Path, path: Path, label: str) -> str:
    root = repository_root.resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must be inside repository root") from exc
    return relative.as_posix()


def _git_blob(repository_root: Path, commit: str, path: Path, label: str) -> bytes:
    relative = _repo_relative(repository_root, path, label)
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "show", f"{commit}:{relative}"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"cannot resolve {label} at accepted repository head") from exc
    return result.stdout


def _validate_output_paths(
    *,
    artifact: Path,
    inputs: tuple[Path, ...],
    archive: Path,
    receipt: Path,
) -> None:
    archive_path = archive.resolve()
    receipt_path = receipt.resolve()
    if archive_path == receipt_path:
        raise ValueError("archive and receipt outputs must be distinct")

    input_paths = {path.resolve() for path in inputs}
    for label, output in (("archive", archive_path), ("receipt", receipt_path)):
        if output in input_paths:
            raise ValueError(f"{label} output must not overwrite an input")
        try:
            output.relative_to(artifact.resolve())
        except ValueError:
            pass
        else:
            raise ValueError(f"{label} output must stay outside artifact directory")


def _validate_consumer_acceptance(
    *,
    data: bytes,
    manifest: object,
    identity: str,
    replay_sha256: str,
    source_descriptor_path: str,
    source_descriptor_sha256: str,
    runner_config_path: str,
    runner_config_sha256: str,
) -> dict[str, object]:
    payload = _load_json_object(data, "consumer receipt")
    if payload.get("schema") != "theseus.repo-search-consumer-receipt.v2":
        raise ValueError("unsupported consumer receipt schema")
    if payload.get("result") != "PASS":
        raise ValueError("consumer receipt result must be PASS")

    artifact = payload.get("artifact")
    producer_config = payload.get("producer_config")
    projection = payload.get("projection")
    replay = payload.get("replay")
    workflow = payload.get("workflow")
    if not all(
        isinstance(section, dict)
        for section in (artifact, producer_config, projection, replay, workflow)
    ):
        raise TypeError("consumer receipt sections must be objects")
    assert isinstance(artifact, dict)
    assert isinstance(producer_config, dict)
    assert isinstance(projection, dict)
    assert isinstance(replay, dict)
    assert isinstance(workflow, dict)

    if set(producer_config) != {"source_descriptor", "runner_config"}:
        raise ValueError("consumer receipt producer_config fields mismatch")
    source_evidence = producer_config.get("source_descriptor")
    runner_evidence = producer_config.get("runner_config")
    if not isinstance(source_evidence, dict) or not isinstance(runner_evidence, dict):
        raise TypeError("consumer receipt producer_config entries must be objects")
    if set(source_evidence) != {"path", "sha256"}:
        raise ValueError("consumer receipt source descriptor evidence fields mismatch")
    if set(runner_evidence) != {"path", "sha256"}:
        raise ValueError("consumer receipt runner config evidence fields mismatch")
    if source_evidence != {
        "path": source_descriptor_path,
        "sha256": source_descriptor_sha256,
    }:
        raise ValueError("consumer receipt source descriptor evidence mismatch")
    if runner_evidence != {
        "path": runner_config_path,
        "sha256": runner_config_sha256,
    }:
        raise ValueError("consumer receipt runner config evidence mismatch")

    if artifact.get("identity") != identity:
        raise ValueError("consumer receipt artifact identity mismatch")
    source_repo = getattr(manifest, "source_repo", None)
    source_commit = getattr(manifest, "source_commit", None)
    source_subdir = getattr(manifest, "source_subdir", None)
    if (
        artifact.get("source_repo"),
        artifact.get("source_commit"),
        artifact.get("source_subdir"),
    ) != (source_repo, source_commit, source_subdir):
        raise ValueError("consumer receipt artifact source mismatch")
    if projection.get("quick_check") != "ok" or projection.get("artifact_identity") != identity:
        raise ValueError("consumer receipt projection acceptance mismatch")
    if (
        replay.get("status") != "PASS"
        or replay.get("artifact_identity") != identity
        or replay.get("sha256") != replay_sha256
    ):
        raise ValueError("consumer receipt replay acceptance mismatch")

    repository_head = _require_commit(
        workflow.get("repository_head"), "consumer receipt repository_head"
    )
    _require_commit(workflow.get("workflow_sha"), "consumer receipt workflow_sha")
    run_id = workflow.get("run_id")
    run_attempt = workflow.get("run_attempt")
    workflow_ref = workflow.get("workflow_ref")
    if not isinstance(run_id, str) or not run_id.isdigit() or not run_id:
        raise ValueError("consumer receipt run_id must be decimal text")
    if not isinstance(run_attempt, str) or not run_attempt.isdigit() or not run_attempt:
        raise ValueError("consumer receipt run_attempt must be decimal text")
    if (
        not isinstance(workflow_ref, str)
        or "/.github/workflows/lean-source-producer-smoke.yml@" not in workflow_ref
    ):
        raise ValueError("consumer receipt workflow_ref is not the producer acceptance workflow")
    return {
        "repository_head": repository_head,
        "workflow_sha": workflow["workflow_sha"],
        "run_id": run_id,
        "run_attempt": run_attempt,
        "workflow_ref": workflow_ref,
    }


def build_release_package(
    *,
    artifact: Path,
    replay: Path,
    source_descriptor: Path,
    runner_config: Path,
    consumer_receipt: Path,
    repository_root: Path,
    archive: Path,
    receipt: Path,
) -> dict[str, object]:
    _validate_output_paths(
        artifact=artifact,
        inputs=(replay, source_descriptor, runner_config, consumer_receipt),
        archive=archive,
        receipt=receipt,
    )
    manifest, *_ = load_artifact(artifact)
    if isinstance(manifest, ArtifactManifestV2):
        raise TypeError("release package pilot currently supports git-backed repo-index.v1 only")

    source = validate_registered_replay_manifest(
        manifest, source_descriptor, runner_path=runner_config
    )
    if not isinstance(source, LeanGitSource):
        raise TypeError("release package pilot currently supports Git sources only")

    identity = artifact_identity(manifest)
    replay_bytes = replay.read_bytes()
    replay_payload = _load_json_object(replay_bytes, "replay")
    if replay_payload.get("status") != "PASS":
        raise ValueError("release package requires replay status PASS")
    if replay_payload.get("artifact_identity") != identity:
        raise ValueError("replay artifact identity does not match accepted artifact")
    validate_registered_replay_provenance(
        artifact, manifest, replay_payload.get("provenance")
    )

    authority_path = artifact / "authority-receipt.json"
    authority_bytes = authority_path.read_bytes()
    authority = _load_json_object(authority_bytes, "authority receipt")
    observed = authority.get("observed")
    if not isinstance(observed, dict) or not isinstance(observed.get("lean_toolchain"), str):
        raise TypeError("authority receipt must contain observed lean_toolchain")

    descriptor_bytes = source_descriptor.read_bytes()
    runner_bytes = runner_config.read_bytes()
    descriptor_relative = _repo_relative(
        repository_root, source_descriptor, "source descriptor"
    )
    runner_relative = _repo_relative(repository_root, runner_config, "runner config")
    consumer_receipt_bytes = consumer_receipt.read_bytes()
    acceptance = _validate_consumer_acceptance(
        data=consumer_receipt_bytes,
        manifest=manifest,
        identity=identity,
        replay_sha256=_sha256(replay_bytes),
        source_descriptor_path=descriptor_relative,
        source_descriptor_sha256=_sha256(descriptor_bytes),
        runner_config_path=runner_relative,
        runner_config_sha256=_sha256(runner_bytes),
    )
    repository_head = str(acceptance["repository_head"])
    if _git_blob(
        repository_root, repository_head, source_descriptor, "source descriptor"
    ) != descriptor_bytes:
        raise ValueError("source descriptor bytes do not match accepted repository head")
    if _git_blob(repository_root, repository_head, runner_config, "runner config") != runner_bytes:
        raise ValueError("runner config bytes do not match accepted repository head")
    manifest_bytes = (artifact / "manifest.json").read_bytes()

    fingerprint_payload: dict[str, object] = {
        "schema": FINGERPRINT_SCHEMA,
        "source_descriptor_sha256": _sha256(descriptor_bytes),
        "runner_config_sha256": _sha256(runner_bytes),
        "authority_receipt_sha256": _sha256(authority_bytes),
        "artifact_manifest_sha256": _sha256(manifest_bytes),
        "artifact_identity": identity,
        "replay_sha256": _sha256(replay_bytes),
        "consumer_receipt_sha256": _sha256(consumer_receipt_bytes),
        "accepted_repository_head": repository_head,
    }
    fingerprint_sha256 = _sha256(_json_line(fingerprint_payload))

    members = _package_member_bytes(
        artifact=artifact,
        artifact_member_names=canonical_artifact_member_names(manifest),
        replay=replay_bytes,
        consumer_receipt=consumer_receipt_bytes,
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
        "acceptance": {
            "consumer_receipt_sha256": _sha256(consumer_receipt_bytes),
            **acceptance,
        },
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
    parser.add_argument("--consumer-receipt", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    payload = build_release_package(
        artifact=args.artifact,
        replay=args.replay,
        source_descriptor=args.source_descriptor,
        runner_config=args.runner_config,
        consumer_receipt=args.consumer_receipt,
        repository_root=args.repository_root,
        archive=args.archive,
        receipt=args.receipt,
    )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
