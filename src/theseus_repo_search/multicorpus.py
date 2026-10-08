from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from scripts.consume_accepted_release import (
    RebuildRequired,
    ReleaseEvidenceInvalid,
    verify_and_extract_release,
)

from .accepted_catalog import AcceptedCatalog, AcceptedCorpus, accepted_catalog_sha256
from .artifact import artifact_identity, load_artifact
from .errors import RepoSearchError
from .projection import (
    SQLITE_PROJECTION_SCHEMA_VERSION,
    build_projection,
    projection_fingerprint,
)
from .retrieval import SearchHit, SearchMode, search

_CACHE_RECEIPT_SCHEMA = "theseus.repo-search-local-cache-verification.v1"
_CACHE_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "catalog_sha256",
        "sqlite_projection_schema_version",
        "source_id",
        "release_tag",
        "package_asset",
        "receipt_asset",
        "package_sha256",
        "receipt_sha256",
        "artifact_identity",
        "fingerprint_sha256",
        "projection_fingerprint",
        "source_descriptor_sha256",
        "runner_config_sha256",
    }
)


@dataclass(frozen=True)
class LocalCorpusState:
    source_id: str
    release_tag: str
    artifact_identity: str
    fingerprint_sha256: str
    artifact_dir: Path
    db_path: Path
    projection_fingerprint: str


@dataclass(frozen=True)
class UnavailableCorpus:
    source_id: str
    code: str
    message: str


@dataclass(frozen=True)
class MultiCorpusHit:
    candidate_id: str
    source_id: str
    local_rank: int
    hit: SearchHit


@dataclass(frozen=True)
class MultiCorpusResult:
    catalog_sha256: str
    searched_corpora: int
    unavailable_corpora: tuple[UnavailableCorpus, ...]
    hits: tuple[MultiCorpusHit, ...]


def _candidate_id(source_id: str, hit: SearchHit) -> str:
    if hit.declaration_id is not None:
        return f"{source_id}::{hit.declaration_id}"
    if hit.source_row_id is not None:
        return f"{source_id}::{hit.source_row_id}"
    raise RepoSearchError(
        "BLOCKED_PROJECTION_INTEGRITY",
        f"search hit for {source_id} has neither declaration_id nor source_row_id",
    )


def search_local_catalog(
    states: tuple[LocalCorpusState, ...],
    unavailable_corpora: tuple[UnavailableCorpus, ...],
    *,
    catalog_sha256: str,
    query: str,
    limit: int = 20,
    mode: SearchMode = "discovery",
) -> MultiCorpusResult:
    searched = 0
    unavailable = list(unavailable_corpora)
    federated: list[MultiCorpusHit] = []

    for state in states:
        try:
            local_hits = search(state.db_path, query, limit=limit, mode=mode)
        except RepoSearchError as exc:
            unavailable.append(
                UnavailableCorpus(
                    source_id=state.source_id,
                    code=exc.code,
                    message=str(exc),
                )
            )
            continue
        except sqlite3.Error as exc:
            unavailable.append(
                UnavailableCorpus(
                    source_id=state.source_id,
                    code="UNAVAILABLE_PROJECTION",
                    message=f"SQLite projection unavailable: {exc}",
                )
            )
            continue

        searched += 1
        for local_rank, hit in enumerate(local_hits, start=1):
            federated.append(
                MultiCorpusHit(
                    candidate_id=_candidate_id(state.source_id, hit),
                    source_id=state.source_id,
                    local_rank=local_rank,
                    hit=hit,
                )
            )

    federated.sort(
        key=lambda item: (
            0 if item.hit.match_mode == "exact" else 1,
            item.local_rank,
            item.source_id,
            item.candidate_id,
        )
    )
    if limit <= 0:
        selected: tuple[MultiCorpusHit, ...] = ()
    else:
        selected = tuple(federated[:limit])
    return MultiCorpusResult(
        catalog_sha256=catalog_sha256,
        searched_corpora=searched,
        unavailable_corpora=tuple(unavailable),
        hits=selected,
    )


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cache_path(
    cache_root: Path,
    *,
    catalog_sha256: str,
    corpus: AcceptedCorpus,
) -> Path:
    return (
        cache_root
        / catalog_sha256
        / SQLITE_PROJECTION_SCHEMA_VERSION
        / corpus.source_id
        / corpus.artifact_identity
    )


def _runner_path(repository_root: Path) -> Path:
    return repository_root / "producer" / "runner.json"


def _descriptor_path(repository_root: Path, corpus: AcceptedCorpus) -> Path:
    return repository_root / corpus.source_descriptor


def _load_cache_receipt(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid local cache receipt: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError("local cache receipt root must be an object")
    if frozenset(payload) != _CACHE_RECEIPT_FIELDS:
        raise ValueError("local cache receipt fields mismatch")
    if payload.get("schema") != _CACHE_RECEIPT_SCHEMA:
        raise ValueError("unsupported local cache receipt schema")
    return payload


def _quick_check(db_path: Path) -> None:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"SQLite quick_check failed to execute: {exc}") from exc
    if row is None or row[0] != "ok":
        raise ValueError(
            f"SQLite quick_check failed: {None if row is None else row[0]}"
        )


def _verify_cached_state(
    final: Path,
    *,
    corpus: AcceptedCorpus,
    repository_root: Path,
    catalog_sha256: str,
) -> LocalCorpusState:
    receipt = _load_cache_receipt(final / "verified.json")
    exact = {
        "catalog_sha256": catalog_sha256,
        "sqlite_projection_schema_version": SQLITE_PROJECTION_SCHEMA_VERSION,
        "source_id": corpus.source_id,
        "release_tag": corpus.release_tag,
        "package_asset": corpus.package_asset,
        "receipt_asset": corpus.receipt_asset,
        "artifact_identity": corpus.artifact_identity,
        "fingerprint_sha256": corpus.fingerprint_sha256,
    }
    for key, expected in exact.items():
        if receipt.get(key) != expected:
            raise ValueError(f"local cache {key} mismatch")

    descriptor = _descriptor_path(repository_root, corpus)
    runner = _runner_path(repository_root)
    descriptor_sha = _sha256_path(descriptor)
    runner_sha = _sha256_path(runner)
    if receipt.get("source_descriptor_sha256") != descriptor_sha:
        raise RebuildRequired(
            "REBUILD_REQUIRED: current source descriptor differs from cached producer fingerprint"
        )
    if receipt.get("runner_config_sha256") != runner_sha:
        raise RebuildRequired(
            "REBUILD_REQUIRED: current runner config differs from cached producer fingerprint"
        )

    artifact_dir = final / "release" / "artifact"
    manifest = load_artifact(artifact_dir)[0]
    if artifact_identity(manifest) != corpus.artifact_identity:
        raise ValueError("cached artifact identity mismatch")

    db_path = final / "index.sqlite"
    _quick_check(db_path)
    observed_projection = projection_fingerprint(db_path)
    if receipt.get("projection_fingerprint") != observed_projection:
        raise ValueError("cached projection fingerprint mismatch")

    return LocalCorpusState(
        source_id=corpus.source_id,
        release_tag=corpus.release_tag,
        artifact_identity=corpus.artifact_identity,
        fingerprint_sha256=corpus.fingerprint_sha256,
        artifact_dir=artifact_dir,
        db_path=db_path,
        projection_fingerprint=observed_projection,
    )


def _download_asset(
    *,
    gh_executable: str,
    repository: str,
    release_tag: str,
    asset: str,
    destination: Path,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            gh_executable,
            "release",
            "download",
            release_tag,
            "-R",
            repository,
            "-D",
            str(destination),
            "-p",
            asset,
        ],
        check=True,
    )
    path = destination / asset
    if not path.is_file():
        raise FileNotFoundError(f"downloaded release asset missing: {asset}")
    return path


def _build_staged_cache(
    stage: Path,
    *,
    catalog: AcceptedCatalog,
    corpus: AcceptedCorpus,
    repository_root: Path,
    catalog_sha256: str,
    gh_executable: str,
) -> None:
    downloads = stage / "downloads"
    archive = _download_asset(
        gh_executable=gh_executable,
        repository=catalog.release_repository,
        release_tag=corpus.release_tag,
        asset=corpus.package_asset,
        destination=downloads,
    )
    receipt_path = _download_asset(
        gh_executable=gh_executable,
        repository=catalog.release_repository,
        release_tag=corpus.release_tag,
        asset=corpus.receipt_asset,
        destination=downloads,
    )
    descriptor = _descriptor_path(repository_root, corpus)
    runner = _runner_path(repository_root)
    release_dir = stage / "release"
    verified = verify_and_extract_release(
        archive=archive,
        receipt=receipt_path,
        source_descriptor=descriptor,
        runner_config=runner,
        dest=release_dir,
    )
    if verified.get("artifact_identity") != corpus.artifact_identity:
        raise ReleaseEvidenceInvalid(
            "RELEASE_EVIDENCE_INVALID: catalog artifact identity mismatch"
        )
    if verified.get("fingerprint_sha256") != corpus.fingerprint_sha256:
        raise ReleaseEvidenceInvalid(
            "RELEASE_EVIDENCE_INVALID: catalog accepted fingerprint mismatch"
        )

    db_path = stage / "index.sqlite"
    projection_fp = build_projection(release_dir / "artifact", db_path)
    _quick_check(db_path)

    cache_receipt = {
        "schema": _CACHE_RECEIPT_SCHEMA,
        "catalog_sha256": catalog_sha256,
        "sqlite_projection_schema_version": SQLITE_PROJECTION_SCHEMA_VERSION,
        "source_id": corpus.source_id,
        "release_tag": corpus.release_tag,
        "package_asset": corpus.package_asset,
        "receipt_asset": corpus.receipt_asset,
        "package_sha256": verified["package_sha256"],
        "receipt_sha256": _sha256_path(receipt_path),
        "artifact_identity": corpus.artifact_identity,
        "fingerprint_sha256": corpus.fingerprint_sha256,
        "projection_fingerprint": projection_fp,
        "source_descriptor_sha256": verified["source_descriptor_sha256"],
        "runner_config_sha256": verified["runner_config_sha256"],
    }
    (stage / "verified.json").write_text(
        json.dumps(cache_receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(downloads)


@contextmanager
def _repair_lock(final: Path):
    lock_path = final.with_name(f".{final.name}.repair-lock.sqlite")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(lock_path, timeout=30.0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        yield
    finally:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        finally:
            conn.close()


def _discard_stage(stage: Path) -> None:
    if stage.exists():
        shutil.rmtree(stage)


def _verify_existing_final(
    final: Path,
    *,
    corpus: AcceptedCorpus,
    repository_root: Path,
    catalog_sha256: str,
) -> LocalCorpusState | None:
    if not final.exists():
        return None
    try:
        return _verify_cached_state(
            final,
            corpus=corpus,
            repository_root=repository_root,
            catalog_sha256=catalog_sha256,
        )
    except RebuildRequired:
        raise
    except (OSError, TypeError, ValueError, RepoSearchError):
        return None


def _publish_staged_cache(
    stage: Path,
    final: Path,
    *,
    corpus: AcceptedCorpus,
    repository_root: Path,
    catalog_sha256: str,
) -> LocalCorpusState:
    if not final.exists():
        try:
            os.rename(stage, final)
        except OSError:
            winner = _verify_existing_final(
                final,
                corpus=corpus,
                repository_root=repository_root,
                catalog_sha256=catalog_sha256,
            )
            if winner is None:
                raise
            _discard_stage(stage)
            return winner
        return _verify_cached_state(
            final,
            corpus=corpus,
            repository_root=repository_root,
            catalog_sha256=catalog_sha256,
        )

    with _repair_lock(final):
        winner = _verify_existing_final(
            final,
            corpus=corpus,
            repository_root=repository_root,
            catalog_sha256=catalog_sha256,
        )
        if winner is not None:
            _discard_stage(stage)
            return winner

        if not final.exists():
            os.rename(stage, final)
            return _verify_cached_state(
                final,
                corpus=corpus,
                repository_root=repository_root,
                catalog_sha256=catalog_sha256,
            )

        quarantine = final.with_name(f".{final.name}.invalid-{uuid.uuid4().hex}")
        os.rename(final, quarantine)
        try:
            os.rename(stage, final)
            state = _verify_cached_state(
                final,
                corpus=corpus,
                repository_root=repository_root,
                catalog_sha256=catalog_sha256,
            )
        except Exception:
            if final.exists():
                shutil.rmtree(final)
            if quarantine.exists():
                os.rename(quarantine, final)
            raise
        shutil.rmtree(quarantine)
        return state


def _ensure_one(
    catalog: AcceptedCatalog,
    corpus: AcceptedCorpus,
    *,
    repository_root: Path,
    cache_root: Path,
    gh_executable: str,
    catalog_sha256: str,
) -> LocalCorpusState:
    final = _cache_path(
        cache_root,
        catalog_sha256=catalog_sha256,
        corpus=corpus,
    )
    if final.exists():
        try:
            return _verify_cached_state(
                final,
                corpus=corpus,
                repository_root=repository_root,
                catalog_sha256=catalog_sha256,
            )
        except (OSError, TypeError, ValueError, RepoSearchError):
            pass

    final.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{corpus.artifact_identity}.staging-", dir=final.parent)
    )
    try:
        _build_staged_cache(
            stage,
            catalog=catalog,
            corpus=corpus,
            repository_root=repository_root,
            catalog_sha256=catalog_sha256,
            gh_executable=gh_executable,
        )
        _verify_cached_state(
            stage,
            corpus=corpus,
            repository_root=repository_root,
            catalog_sha256=catalog_sha256,
        )
        return _publish_staged_cache(
            stage,
            final,
            corpus=corpus,
            repository_root=repository_root,
            catalog_sha256=catalog_sha256,
        )
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def ensure_local_catalog(
    catalog: AcceptedCatalog,
    *,
    repository_root: Path,
    cache_root: Path,
    gh_executable: str = "gh",
) -> tuple[tuple[LocalCorpusState, ...], tuple[UnavailableCorpus, ...]]:
    digest = accepted_catalog_sha256(catalog)
    states: list[LocalCorpusState] = []
    unavailable: list[UnavailableCorpus] = []
    for corpus in catalog.corpora:
        try:
            states.append(
                _ensure_one(
                    catalog,
                    corpus,
                    repository_root=repository_root,
                    cache_root=cache_root,
                    gh_executable=gh_executable,
                    catalog_sha256=digest,
                )
            )
        except RebuildRequired as exc:
            unavailable.append(
                UnavailableCorpus(
                    source_id=corpus.source_id,
                    code="REBUILD_REQUIRED",
                    message=str(exc),
                )
            )
        except ReleaseEvidenceInvalid as exc:
            unavailable.append(
                UnavailableCorpus(
                    source_id=corpus.source_id,
                    code="RELEASE_EVIDENCE_INVALID",
                    message=str(exc),
                )
            )
        except RepoSearchError as exc:
            unavailable.append(
                UnavailableCorpus(
                    source_id=corpus.source_id,
                    code=exc.code,
                    message=str(exc),
                )
            )
        except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
            unavailable.append(
                UnavailableCorpus(
                    source_id=corpus.source_id,
                    code="LOCAL_CACHE_UNAVAILABLE",
                    message=str(exc),
                )
            )
    return tuple(states), tuple(unavailable)
