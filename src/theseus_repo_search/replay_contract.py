from __future__ import annotations

import json
from pathlib import Path

from .model import (
    ArtifactManifest,
    ArtifactManifestAny,
    ArtifactManifestV2,
    ProducerPin,
)
from .producer_config import (
    LeanArchiveSource,
    LeanGitSource,
    LeanSource,
    load_lean_source,
    load_runner_pins,
)
from .projection import build_projection

DEFAULT_RUNNER = Path("producer/runner.json")


def _expected_producer(runner_path: Path) -> ProducerPin:
    runner = load_runner_pins(runner_path)
    return ProducerPin(
        kind="lean-dep-viz",
        tool_repo=runner.extractor_repo,
        tool_commit=runner.extractor_commit,
        tool_hash=runner.extractor_main_sha256,
    )


def _archive_source_payload(manifest: ArtifactManifestV2) -> dict[str, object]:
    authority = manifest.source_authority
    return {
        "kind": "archive",
        "url": authority.url,
        "sha256": authority.sha256,
        "format": authority.format,
        "subdir": authority.subdir,
    }


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise AssertionError(f"invalid archive replay {label}")
    return value


def require_context_chunks(context_result: dict[str, object]) -> list[dict[str, object]]:
    chunks = context_result.get("chunks")
    if not isinstance(chunks, list) or not all(isinstance(chunk, dict) for chunk in chunks):
        raise AssertionError("replay context chunks must be a list of objects")
    return chunks


def registered_replay_provenance(
    artifact_path: Path, manifest: ArtifactManifestAny
) -> dict[str, object]:
    if isinstance(manifest, ArtifactManifest):
        return {
            "repo": manifest.source_repo,
            "commit": manifest.source_commit,
            "subdir": manifest.source_subdir,
        }

    receipt_path = artifact_path / "authority-receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"cannot load archive authority receipt: {exc}") from exc
    if not isinstance(receipt, dict) or receipt.get("schema") != "theseus.raw-depgraph-receipt.v3":
        raise AssertionError("archive replay requires raw-depgraph receipt v3")
    expected_source = _archive_source_payload(manifest)
    if receipt.get("source") != expected_source:
        raise AssertionError("archive authority receipt source does not match artifact")
    materialization = receipt.get("materialization")
    if not isinstance(materialization, dict) or set(materialization) != {
        "tree_sha256",
        "member_manifest_sha256",
    }:
        raise AssertionError("archive replay materialization evidence is invalid")
    tree_sha256 = _require_sha256(materialization.get("tree_sha256"), "tree_sha256")
    member_manifest_sha256 = _require_sha256(
        materialization.get("member_manifest_sha256"), "member_manifest_sha256"
    )
    return {
        "source": expected_source,
        "materialization": {
            "tree_sha256": tree_sha256,
            "member_manifest_sha256": member_manifest_sha256,
        },
    }


def validate_registered_replay_provenance(
    artifact_path: Path,
    manifest: ArtifactManifestAny,
    observed: object,
) -> None:
    expected = registered_replay_provenance(artifact_path, manifest)
    if observed != expected:
        raise ValueError("replay provenance mismatch for selected artifact")


def validate_registered_replay_manifest(
    manifest: ArtifactManifestAny,
    descriptor_path: Path,
    runner_path: Path = DEFAULT_RUNNER,
) -> LeanSource:
    source = load_lean_source(descriptor_path)
    expected_producer = _expected_producer(runner_path)

    if isinstance(manifest, ArtifactManifest):
        if not isinstance(source, LeanGitSource):
            raise TypeError("artifact/source descriptor source kind mismatch")
        if not manifest.created_from_authoritative_commit:
            raise AssertionError("registered replay requires an authoritative artifact")
        if (
            manifest.source_repo,
            manifest.source_commit,
            manifest.source_subdir,
            manifest.scope.root_modules,
            manifest.scope.exclude_source_prefixes,
        ) != (
            source.source_repo,
            source.source_commit,
            source.source_subdir,
            source.root_modules,
            source.exclude_source_prefixes,
        ):
            raise AssertionError(
                "artifact provenance/scope does not match selected source descriptor"
            )
    elif isinstance(manifest, ArtifactManifestV2):
        if not isinstance(source, LeanArchiveSource):
            raise TypeError("artifact/source descriptor source kind mismatch")
        if not manifest.created_from_authoritative_source:
            raise AssertionError("registered replay requires an authoritative artifact")
        authority = manifest.source_authority
        if (
            authority.url,
            authority.sha256,
            authority.format,
            authority.subdir,
            manifest.scope.root_modules,
            manifest.scope.exclude_source_prefixes,
        ) != (
            source.archive_url,
            source.archive_sha256,
            source.archive_format,
            source.source_subdir,
            source.root_modules,
            source.exclude_source_prefixes,
        ):
            raise AssertionError(
                "artifact provenance/scope does not match selected source descriptor"
            )
        if manifest.authority_receipt_sha256 is None:
            raise AssertionError("archive registered replay requires authority receipt evidence")
    else:
        raise TypeError("unsupported artifact manifest type")

    if manifest.producer != expected_producer:
        raise AssertionError("artifact producer pin does not match selected runner configuration")
    return source


def prepare_registered_replay(
    artifact_path: Path,
    db_path: Path,
    descriptor_path: Path,
    runner_path: Path = DEFAULT_RUNNER,
) -> tuple[ArtifactManifestAny, LeanSource]:
    from .artifact import load_artifact

    manifest, _, _, _ = load_artifact(artifact_path)
    source = validate_registered_replay_manifest(
        manifest, descriptor_path, runner_path=runner_path
    )
    if isinstance(manifest, ArtifactManifestV2):
        registered_replay_provenance(artifact_path, manifest)
    build_projection(artifact_path, db_path)
    return manifest, source
