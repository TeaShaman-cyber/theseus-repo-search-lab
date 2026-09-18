from __future__ import annotations

from pathlib import Path

from .model import ArtifactManifest, ProducerPin
from .producer_config import LeanGitSource, load_lean_git_source, load_runner_pins
from .projection import build_projection


DEFAULT_RUNNER = Path("producer/runner.json")


def validate_registered_replay_manifest(
    manifest: ArtifactManifest,
    descriptor_path: Path,
    runner_path: Path = DEFAULT_RUNNER,
) -> LeanGitSource:
    source = load_lean_git_source(descriptor_path)
    runner = load_runner_pins(runner_path)

    if not manifest.created_from_authoritative_commit:
        raise AssertionError("registered replay requires an authoritative artifact")

    if (
        manifest.source_repo,
        manifest.source_commit,
        manifest.source_subdir,
        manifest.scope.root_modules,
    ) != (
        source.source_repo,
        source.source_commit,
        source.source_subdir,
        source.root_modules,
    ):
        raise AssertionError("artifact provenance/scope does not match selected source descriptor")

    expected_producer = ProducerPin(
        kind="lean-dep-viz",
        tool_repo=runner.extractor_repo,
        tool_commit=runner.extractor_commit,
        tool_hash=runner.extractor_main_sha256,
    )
    if manifest.producer != expected_producer:
        raise AssertionError("artifact producer pin does not match selected runner configuration")

    return source


def prepare_registered_replay(
    artifact_path: Path,
    db_path: Path,
    descriptor_path: Path,
    runner_path: Path = DEFAULT_RUNNER,
) -> tuple[ArtifactManifest, LeanGitSource]:
    from .artifact import load_artifact

    manifest, _, _, _ = load_artifact(artifact_path)
    source = validate_registered_replay_manifest(
        manifest, descriptor_path, runner_path=runner_path
    )
    build_projection(artifact_path, db_path)
    return manifest, source
