from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import RepoSearchError


SOURCE_SCHEMA = "theseus.lean-git-source.v1"
RUNNER_SCHEMA = "theseus.lean-producer-runner.v1"
HEX40 = re.compile(r"[0-9a-f]{40}")
SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


def _blocked(message: str) -> RepoSearchError:
    return RepoSearchError("BLOCKED_SOURCE_BINDING", message)


def _require_exact_keys(data: dict[str, object], expected: set[str], label: str) -> None:
    if set(data) != expected:
        raise _blocked(f"{label} keys mismatch")


def _require_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise _blocked(f"{key} must be a string")
    return value


def _require_single_line_transport(value: str, key: str) -> str:
    if any(ch in value for ch in ("\n", "\r", "\x00")):
        raise _blocked(f"{key} must be safe for GitHub environment transport")
    return value


@dataclass(frozen=True)
class LeanGitSource:
    schema: str
    source_id: str
    source_repo: str
    source_commit: str
    source_subdir: str
    root_modules: tuple[str, ...]
    build_target: str

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "LeanGitSource":
        expected = {
            "schema", "source_id", "source_repo", "source_commit",
            "source_subdir", "root_modules", "build_target",
        }
        _require_exact_keys(data, expected, "source descriptor")
        schema = _require_str(data, "schema")
        source_id = _require_single_line_transport(_require_str(data, "source_id"), "source_id")
        source_repo = _require_single_line_transport(_require_str(data, "source_repo"), "source_repo")
        source_commit = _require_single_line_transport(_require_str(data, "source_commit"), "source_commit")
        source_subdir = _require_single_line_transport(_require_str(data, "source_subdir"), "source_subdir")
        build_target = _require_single_line_transport(_require_str(data, "build_target"), "build_target")
        roots = data.get("root_modules")

        if schema != SOURCE_SCHEMA:
            raise _blocked(f"unsupported source schema: {schema}")
        if SOURCE_ID.fullmatch(source_id) is None:
            raise _blocked("source_id must be lowercase letters, digits, and hyphens")
        if source_repo.count("/") != 1 or source_repo.startswith("/") or source_repo.endswith("/"):
            raise _blocked("source_repo must be owner/repository")
        if HEX40.fullmatch(source_commit) is None:
            raise _blocked("source_commit must be a 40-character lowercase Git SHA")
        subdir = PurePosixPath(source_subdir) if source_subdir else PurePosixPath(".")
        if subdir.is_absolute() or ".." in subdir.parts:
            raise _blocked("source_subdir must stay inside the Git checkout")
        if not isinstance(roots, list) or not roots or not all(isinstance(x, str) and x for x in roots):
            raise _blocked("root_modules must be a non-empty array of strings")
        roots = [_require_single_line_transport(root, "root_modules") for root in roots]
        if len(set(roots)) != len(roots):
            raise _blocked("root_modules must be unique")
        if any(any(ch in root for ch in (",", "\n", "\r", "\x00")) for root in roots):
            raise _blocked("root_modules must be safe for CSV and GitHub environment transport")
        if not build_target.strip() or any(ch.isspace() for ch in build_target):
            raise _blocked("build_target must be one non-empty Lake target")

        return cls(
            schema=schema,
            source_id=source_id,
            source_repo=source_repo,
            source_commit=source_commit,
            source_subdir=source_subdir,
            root_modules=tuple(roots),
            build_target=build_target,
        )

    def resolve_source_root(self, checkout_root: Path) -> Path:
        checkout = checkout_root.resolve()
        candidate = (checkout / self.source_subdir).resolve()
        try:
            candidate.relative_to(checkout)
        except ValueError as exc:
            raise _blocked("resolved source root escapes Git checkout") from exc
        if not candidate.is_dir():
            raise _blocked(f"source root does not exist: {candidate}")
        return candidate


@dataclass(frozen=True)
class RunnerPins:
    schema: str
    extractor_repo: str
    extractor_commit: str
    extractor_main_sha256: str
    elan_version: str
    elan_sha256: str

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RunnerPins":
        expected = {
            "schema", "extractor_repo", "extractor_commit",
            "extractor_main_sha256", "elan_version", "elan_sha256",
        }
        _require_exact_keys(data, expected, "runner config")
        values = {key: _require_single_line_transport(_require_str(data, key), key) for key in expected}
        if values["schema"] != RUNNER_SCHEMA:
            raise _blocked("unsupported runner schema")
        if HEX40.fullmatch(values["extractor_commit"]) is None:
            raise _blocked("extractor_commit must be a 40-character lowercase Git SHA")
        if re.fullmatch(r"[0-9a-f]{64}", values["extractor_main_sha256"]) is None:
            raise _blocked("extractor_main_sha256 must be SHA-256 hex")
        if re.fullmatch(r"[0-9a-f]{64}", values["elan_sha256"]) is None:
            raise _blocked("elan_sha256 must be SHA-256 hex")
        return cls(**values)


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _blocked(f"cannot load producer config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise _blocked(f"producer config must be a JSON object: {path}")
    return value


def load_lean_git_source(path: Path) -> LeanGitSource:
    return LeanGitSource.from_dict(_load_json_object(path))


def load_runner_pins(path: Path) -> RunnerPins:
    return RunnerPins.from_dict(_load_json_object(path))
