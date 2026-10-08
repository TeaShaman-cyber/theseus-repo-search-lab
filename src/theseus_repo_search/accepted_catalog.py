from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath


_SCHEMA = "theseus.repo-search.accepted-catalog.v1"
_TOP_LEVEL_FIELDS = frozenset({"schema", "release_repository", "corpora"})
_CORPUS_FIELDS = frozenset(
    {
        "source_id",
        "release_tag",
        "package_asset",
        "receipt_asset",
        "source_descriptor",
        "artifact_identity",
        "fingerprint_sha256",
    }
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class AcceptedCorpus:
    source_id: str
    release_tag: str
    package_asset: str
    receipt_asset: str
    source_descriptor: str
    artifact_identity: str
    fingerprint_sha256: str


@dataclass(frozen=True)
class AcceptedCatalog:
    schema: str
    release_repository: str
    corpora: tuple[AcceptedCorpus, ...]


def _require_object(value: object, *, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a JSON object")
    return value


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field} must not contain line breaks")
    return value


def _require_exact_fields(
    obj: dict[str, object], *, expected: frozenset[str], where: str
) -> None:
    actual = frozenset(obj)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"{where} fields differ from contract: missing={missing}, unknown={unknown}"
        )


def _validate_descriptor_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("source_descriptor must be a normalized repository-relative path")
    if len(path.parts) < 3 or path.parts[:2] != ("producer", "sources"):
        raise ValueError("source_descriptor must be under producer/sources/")
    if path.suffix != ".json":
        raise ValueError("source_descriptor must name a JSON descriptor")


def _parse_corpus(raw: object, *, index: int) -> AcceptedCorpus:
    obj = _require_object(raw, where=f"corpora[{index}]")
    _require_exact_fields(
        obj, expected=_CORPUS_FIELDS, where=f"corpora[{index}]"
    )
    values = {
        field: _require_text(obj[field], field=f"corpora[{index}].{field}")
        for field in _CORPUS_FIELDS
    }
    if not _HEX64.fullmatch(values["artifact_identity"]):
        raise ValueError("artifact_identity must be 64 lowercase hexadecimal characters")
    if not _HEX64.fullmatch(values["fingerprint_sha256"]):
        raise ValueError("fingerprint_sha256 must be 64 lowercase hexadecimal characters")
    _validate_descriptor_path(values["source_descriptor"])
    return AcceptedCorpus(**values)


def load_accepted_catalog(path: Path) -> AcceptedCatalog:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load accepted catalog: {path}") from exc

    obj = _require_object(raw, where="catalog")
    _require_exact_fields(obj, expected=_TOP_LEVEL_FIELDS, where="catalog")

    schema = _require_text(obj["schema"], field="schema")
    if schema != _SCHEMA:
        raise ValueError(f"unsupported accepted catalog schema: {schema}")

    repository = _require_text(
        obj["release_repository"], field="release_repository"
    )
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("release_repository must be owner/repository")

    corpora_raw = obj["corpora"]
    if not isinstance(corpora_raw, list) or not corpora_raw:
        raise ValueError("corpora must be a non-empty JSON array")
    corpora = tuple(_parse_corpus(item, index=i) for i, item in enumerate(corpora_raw))

    seen_source_ids: set[str] = set()
    seen_release_tags: set[str] = set()
    seen_release_assets: set[tuple[str, str]] = set()
    seen_artifact_ids: set[str] = set()
    for corpus in corpora:
        if corpus.source_id in seen_source_ids:
            raise ValueError(f"duplicate source_id: {corpus.source_id}")
        if corpus.release_tag in seen_release_tags:
            raise ValueError(f"duplicate release_tag: {corpus.release_tag}")
        release_asset = (corpus.release_tag, corpus.package_asset)
        if release_asset in seen_release_assets:
            raise ValueError(
                f"duplicate package asset within release: {corpus.release_tag} {corpus.package_asset}"
            )
        if corpus.artifact_identity in seen_artifact_ids:
            raise ValueError(f"duplicate artifact_identity: {corpus.artifact_identity}")
        seen_source_ids.add(corpus.source_id)
        seen_release_tags.add(corpus.release_tag)
        seen_release_assets.add(release_asset)
        seen_artifact_ids.add(corpus.artifact_identity)

    return AcceptedCatalog(
        schema=schema,
        release_repository=repository,
        corpora=corpora,
    )


def accepted_catalog_sha256(catalog: AcceptedCatalog) -> str:
    normalized = {
        "schema": catalog.schema,
        "release_repository": catalog.release_repository,
        "corpora": [
            asdict(corpus)
            for corpus in sorted(catalog.corpora, key=lambda item: item.source_id)
        ],
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
