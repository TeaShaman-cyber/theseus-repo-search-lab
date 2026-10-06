from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeAlias
from urllib.parse import urlparse

from .errors import RepoSearchError

SOURCE_SCHEMA_V1 = "theseus.lean-git-source.v1"
SOURCE_SCHEMA_V2 = "theseus.lean-git-source.v2"
ARCHIVE_SOURCE_SCHEMA_V1 = "theseus.lean-archive-source.v1"
RUNNER_SCHEMA = "theseus.lean-producer-runner.v1"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
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


def _validated_source_id(data: dict[str, object]) -> str:
    source_id = _require_single_line_transport(_require_str(data, "source_id"), "source_id")
    if SOURCE_ID.fullmatch(source_id) is None:
        raise _blocked("source_id must be lowercase letters, digits, and hyphens")
    return source_id


def _validated_subdir(data: dict[str, object], key: str) -> str:
    value = _require_single_line_transport(_require_str(data, key), key)
    subdir = PurePosixPath(value) if value else PurePosixPath(".")
    if subdir.is_absolute() or ".." in subdir.parts:
        raise _blocked(f"{key} must stay inside the materialized source")
    return value


def _validated_roots(data: dict[str, object]) -> tuple[str, ...]:
    roots = data.get("root_modules")
    if not isinstance(roots, list) or not roots or not all(isinstance(x, str) and x for x in roots):
        raise _blocked("root_modules must be a non-empty array of strings")
    roots = [_require_single_line_transport(root, "root_modules") for root in roots]
    if len(set(roots)) != len(roots):
        raise _blocked("root_modules must be unique")
    if any("," in root for root in roots):
        raise _blocked("root_modules must be safe for CSV and GitHub environment transport")
    return tuple(roots)


def _validated_build_target(data: dict[str, object]) -> str:
    value = _require_single_line_transport(_require_str(data, "build_target"), "build_target")
    if not value.strip() or any(ch.isspace() for ch in value):
        raise _blocked("build_target must be one non-empty Lake target")
    return value


def _validated_excludes(data: dict[str, object]) -> tuple[str, ...]:
    excludes_raw = data.get("exclude_source_prefixes", [])
    if not isinstance(excludes_raw, list) or not all(isinstance(x, str) and x for x in excludes_raw):
        raise _blocked("exclude_source_prefixes must be an array of non-empty strings")
    excludes: list[str] = []
    for value in excludes_raw:
        value = _require_single_line_transport(value, "exclude_source_prefixes")
        if "," in value:
            raise _blocked("exclude_source_prefixes must be safe for CSV transport")
        posix = PurePosixPath(value)
        if posix.is_absolute() or ".." in posix.parts:
            raise _blocked("exclude_source_prefixes must stay inside source root")
        normalized = posix.as_posix()
        if not normalized:
            raise _blocked("exclude_source_prefixes must not exclude the entire source root")
        if not normalized.endswith("/"):
            normalized += "/"
        excludes.append(normalized)
    if len(set(excludes)) != len(excludes):
        raise _blocked("exclude_source_prefixes must be unique")
    if excludes != sorted(excludes):
        raise _blocked("exclude_source_prefixes must use deterministic sorted order")
    return tuple(excludes)


def _resolve_root(materialization_root: Path, source_subdir: str) -> Path:
    root = materialization_root.resolve()
    candidate = (root / source_subdir).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _blocked("resolved source root escapes materialized source") from exc
    if not candidate.is_dir():
        raise _blocked(f"source root does not exist: {candidate}")
    return candidate


@dataclass(frozen=True)
class LeanGitSource:
    schema: str
    source_id: str
    source_repo: str
    source_commit: str
    source_subdir: str
    root_modules: tuple[str, ...]
    build_target: str
    exclude_source_prefixes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LeanGitSource:
        schema = _require_str(data, "schema")
        if schema == SOURCE_SCHEMA_V1:
            expected = {
                "schema", "source_id", "source_repo", "source_commit",
                "source_subdir", "root_modules", "build_target",
            }
        elif schema == SOURCE_SCHEMA_V2:
            expected = {
                "schema", "source_id", "source_repo", "source_commit",
                "source_subdir", "root_modules", "build_target",
                "exclude_source_prefixes",
            }
        else:
            raise _blocked(f"unsupported source schema: {schema}")
        _require_exact_keys(data, expected, "source descriptor")
        source_id = _validated_source_id(data)
        source_repo = _require_single_line_transport(_require_str(data, "source_repo"), "source_repo")
        source_commit = _require_single_line_transport(_require_str(data, "source_commit"), "source_commit")
        source_subdir = _validated_subdir(data, "source_subdir")
        if source_repo.count("/") != 1 or source_repo.startswith("/") or source_repo.endswith("/"):
            raise _blocked("source_repo must be owner/repository")
        if HEX40.fullmatch(source_commit) is None:
            raise _blocked("source_commit must be a 40-character lowercase Git SHA")
        return cls(
            schema=schema,
            source_id=source_id,
            source_repo=source_repo,
            source_commit=source_commit,
            source_subdir=source_subdir,
            root_modules=_validated_roots(data),
            build_target=_validated_build_target(data),
            exclude_source_prefixes=_validated_excludes(data),
        )

    def resolve_source_root(self, checkout_root: Path) -> Path:
        return _resolve_root(checkout_root, self.source_subdir)


@dataclass(frozen=True)
class LeanArchiveSource:
    schema: str
    source_id: str
    archive_url: str
    archive_sha256: str
    archive_format: str
    source_subdir: str
    root_modules: tuple[str, ...]
    build_target: str
    exclude_source_prefixes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LeanArchiveSource:
        _require_exact_keys(
            data,
            {
                "schema", "source_id", "archive_url", "archive_sha256",
                "archive_format", "source_subdir", "root_modules", "build_target",
                "exclude_source_prefixes",
            },
            "archive source descriptor",
        )
        schema = _require_str(data, "schema")
        if schema != ARCHIVE_SOURCE_SCHEMA_V1:
            raise _blocked(f"unsupported source schema: {schema}")
        archive_url = _require_single_line_transport(_require_str(data, "archive_url"), "archive_url")
        parsed = urlparse(archive_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise _blocked("archive_url must be an absolute HTTPS URL without credentials or fragment")
        archive_sha256 = _require_single_line_transport(_require_str(data, "archive_sha256"), "archive_sha256")
        if HEX64.fullmatch(archive_sha256) is None:
            raise _blocked("archive_sha256 must be SHA-256 hex")
        archive_format = _require_single_line_transport(_require_str(data, "archive_format"), "archive_format")
        if archive_format != "zip":
            raise _blocked("archive_format v1 supports only zip")
        return cls(
            schema=schema,
            source_id=_validated_source_id(data),
            archive_url=archive_url,
            archive_sha256=archive_sha256,
            archive_format=archive_format,
            source_subdir=_validated_subdir(data, "source_subdir"),
            root_modules=_validated_roots(data),
            build_target=_validated_build_target(data),
            exclude_source_prefixes=_validated_excludes(data),
        )

    def resolve_source_root(self, materialization_root: Path) -> Path:
        return _resolve_root(materialization_root, self.source_subdir)


LeanSource: TypeAlias = LeanGitSource | LeanArchiveSource


@dataclass(frozen=True)
class RunnerPins:
    schema: str
    extractor_repo: str
    extractor_commit: str
    extractor_main_sha256: str
    elan_version: str
    elan_sha256: str

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RunnerPins:
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
        if HEX64.fullmatch(values["extractor_main_sha256"]) is None:
            raise _blocked("extractor_main_sha256 must be SHA-256 hex")
        if HEX64.fullmatch(values["elan_sha256"]) is None:
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


def load_lean_source(path: Path) -> LeanSource:
    data = _load_json_object(path)
    schema = data.get("schema")
    if schema in {SOURCE_SCHEMA_V1, SOURCE_SCHEMA_V2}:
        return LeanGitSource.from_dict(data)
    if schema == ARCHIVE_SOURCE_SCHEMA_V1:
        return LeanArchiveSource.from_dict(data)
    raise _blocked(f"unsupported source schema: {schema}")


def load_lean_git_source(path: Path) -> LeanGitSource:
    source = load_lean_source(path)
    if not isinstance(source, LeanGitSource):
        raise _blocked("source descriptor is not Git-backed")
    return source


def load_lean_archive_source(path: Path) -> LeanArchiveSource:
    source = load_lean_source(path)
    if not isinstance(source, LeanArchiveSource):
        raise _blocked("source descriptor is not archive-backed")
    return source


def load_runner_pins(path: Path) -> RunnerPins:
    return RunnerPins.from_dict(_load_json_object(path))
