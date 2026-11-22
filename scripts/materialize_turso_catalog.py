#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from theseus_repo_search.accepted_catalog import (
    accepted_catalog_sha256,
    load_accepted_catalog,
)
from theseus_repo_search.multicorpus import (
    LocalCorpusState,
    UnavailableCorpus,
    ensure_local_catalog,
)

_MATERIALIZATION_SCHEMA = "theseus.repo-search-turso-materialization.v1"
_CATALOG_MATERIALIZATION_SCHEMA = (
    "theseus.repo-search-turso-catalog-materialization.v1"
)
_DATABASE_NAME = re.compile(r"^[a-z0-9-]{1,64}$")


class TursoMaterializerClient(Protocol):
    def database_exists(self, name: str) -> bool: ...

    def create_upload_database(
        self, name: str, *, group: str, size_limit: str
    ) -> None: ...

    def upload_database(self, name: str, sqlite_path: Path) -> None: ...

    def query_rows(self, name: str, sql: str) -> list[tuple[object, ...]]: ...

    def set_delete_protection(self, name: str, *, enabled: bool) -> None: ...


def database_name_for_source(source_id: str) -> str:
    name = f"repo-search-{source_id}"
    if not _DATABASE_NAME.fullmatch(name):
        raise ValueError(
            "derived Turso database name must contain only lowercase letters, "
            "digits, and dashes and be at most 64 characters"
        )
    return name


def _single_value(
    client: TursoMaterializerClient, database: str, sql: str, label: str
) -> object:
    rows = client.query_rows(database, sql)
    if len(rows) != 1 or len(rows[0]) != 1:
        raise ValueError(f"remote {label} readback must return one scalar")
    return rows[0][0]


def _local_fts_schema(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type='table' AND name='sources_fts'"
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise ValueError("local projection is missing sources_fts schema")
    return row[0]


def _prepare_upload_copy(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    with sqlite3.connect(destination) as conn:
        quick = conn.execute("PRAGMA quick_check").fetchone()
        if quick is None or quick[0] != "ok":
            raise ValueError("local SQLite quick_check failed before Turso upload")
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        auto_vacuum = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        encoding = conn.execute("PRAGMA encoding").fetchone()[0]
        if page_size != 4096:
            raise ValueError(f"Turso upload requires page_size=4096, got {page_size}")
        if auto_vacuum != 0:
            raise ValueError(f"Turso upload requires auto_vacuum=0, got {auto_vacuum}")
        if str(encoding).upper() != "UTF-8":
            raise ValueError(f"Turso upload requires UTF-8 encoding, got {encoding}")
        journal = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(journal).lower() != "wal":
            raise ValueError(
                f"cannot prepare WAL upload copy, got journal_mode={journal}"
            )

    # SQLite rejects wal_checkpoint on the same connection that just switched
    # journal mode. Reopen so the upload file is checkpointed with no live WAL.
    with sqlite3.connect(destination) as conn:
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or checkpoint[0] != 0:
            raise ValueError(f"WAL checkpoint failed before upload: {checkpoint}")

    wal = Path(f"{destination}-wal")
    shm = Path(f"{destination}-shm")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("upload copy retains non-empty WAL after checkpoint")
    wal.unlink(missing_ok=True)
    shm.unlink(missing_ok=True)


def _verify_remote(
    state: LocalCorpusState,
    *,
    client: TursoMaterializerClient,
    database: str,
    expected_sqlite_bytes: int | None,
) -> dict[str, object]:
    identity = _single_value(
        client,
        database,
        "SELECT value FROM meta WHERE key='artifact_identity'",
        "artifact identity",
    )
    if identity != state.artifact_identity:
        raise ValueError(
            "remote artifact identity mismatch: "
            f"expected {state.artifact_identity}, observed {identity}"
        )

    quick = _single_value(client, database, "PRAGMA quick_check", "quick_check")
    if quick != "ok":
        raise ValueError(f"remote SQLite quick_check failed: {quick}")

    integrity = _single_value(
        client, database, "PRAGMA integrity_check", "integrity_check"
    )
    if integrity != "ok":
        raise ValueError(f"remote SQLite integrity_check failed: {integrity}")

    journal = _single_value(client, database, "PRAGMA journal_mode", "journal_mode")
    if str(journal).lower() != "wal":
        raise ValueError(f"remote journal_mode is not WAL: {journal}")

    remote_fts_schema = _single_value(
        client,
        database,
        "SELECT sql FROM sqlite_schema "
        "WHERE type='table' AND name='sources_fts'",
        "FTS schema",
    )
    local_fts_schema = _local_fts_schema(state.db_path)
    if remote_fts_schema != local_fts_schema:
        raise ValueError("remote FTS schema differs from local canonical projection")

    counts: dict[str, int] = {}
    for table in ("nodes", "edges", "sources"):
        value = _single_value(
            client, database, f"SELECT count(*) FROM {table}", f"{table} count"
        )
        if not isinstance(value, int):
            raise TypeError(f"remote {table} count is not an integer")
        counts[table] = value

    fts_rows = _single_value(
        client, database, "SELECT count(*) FROM sources_fts", "FTS row count"
    )
    if fts_rows != counts["sources"]:
        raise ValueError(
            f"remote FTS/source row count mismatch: {fts_rows} != {counts['sources']}"
        )

    page_size = _single_value(client, database, "PRAGMA page_size", "page_size")
    page_count = _single_value(client, database, "PRAGMA page_count", "page_count")
    if not isinstance(page_size, int) or not isinstance(page_count, int):
        raise TypeError("remote page size/count must be integers")
    remote_bytes = page_size * page_count
    if expected_sqlite_bytes is not None and remote_bytes != expected_sqlite_bytes:
        raise ValueError(
            "remote SQLite byte size differs from uploaded file: "
            f"{remote_bytes} != {expected_sqlite_bytes}"
        )

    return {
        "artifact_identity": state.artifact_identity,
        "projection_fingerprint": state.projection_fingerprint,
        "sqlite_bytes": remote_bytes,
        "nodes": counts["nodes"],
        "edges": counts["edges"],
        "sources": counts["sources"],
        "fts_rows": fts_rows,
        "quick_check": "ok",
        "integrity_check": "ok",
        "journal_mode": "wal",
    }


def materialize_state(
    state: LocalCorpusState,
    *,
    client: TursoMaterializerClient,
    group: str,
    size_limit: str,
) -> dict[str, object]:
    database = database_name_for_source(state.source_id)

    if client.database_exists(database):
        verified = _verify_remote(
            state,
            client=client,
            database=database,
            expected_sqlite_bytes=None,
        )
        client.set_delete_protection(database, enabled=True)
        return {
            "schema": _MATERIALIZATION_SCHEMA,
            "status": "ALREADY_PRESENT",
            "source_id": state.source_id,
            "database": database,
            "delete_protection": True,
            **verified,
        }

    with tempfile.TemporaryDirectory(prefix="repo-search-turso-") as d:
        upload_path = Path(d) / "index.sqlite"
        _prepare_upload_copy(state.db_path, upload_path)
        upload_bytes = upload_path.stat().st_size
        client.create_upload_database(
            database,
            group=group,
            size_limit=size_limit,
        )
        client.upload_database(database, upload_path)

    verified = _verify_remote(
        state,
        client=client,
        database=database,
        expected_sqlite_bytes=upload_bytes,
    )
    client.set_delete_protection(database, enabled=True)
    return {
        "schema": _MATERIALIZATION_SCHEMA,
        "status": "UPLOADED",
        "source_id": state.source_id,
        "database": database,
        "delete_protection": True,
        **verified,
    }


def materialize_catalog_states(
    states: tuple[LocalCorpusState, ...],
    unavailable: tuple[UnavailableCorpus, ...],
    *,
    client: TursoMaterializerClient,
    group: str,
    size_limit: str,
) -> tuple[dict[str, object], ...]:
    if unavailable:
        details = ", ".join(
            f"{item.source_id}:{item.code}" for item in unavailable
        )
        raise ValueError(
            "accepted catalog is not fully materializable; "
            f"no Turso mutation started: {details}"
        )

    # Validate the complete namespace before any remote mutation.
    for state in states:
        database_name_for_source(state.source_id)

    # Read-only preflight all already-present databases before creating any new ones.
    for state in states:
        database = database_name_for_source(state.source_id)
        if client.database_exists(database):
            _verify_remote(
                state,
                client=client,
                database=database,
                expected_sqlite_bytes=None,
            )

    return tuple(
        materialize_state(
            state,
            client=client,
            group=group,
            size_limit=size_limit,
        )
        for state in states
    )


class TursoHttpClient:
    def __init__(
        self,
        *,
        platform_api_base: str,
        organization: str,
        platform_token: str | None = None,
        platform_opener: Callable[..., Any] | None = None,
        connection_factory: Callable[..., Any] | None = None,
        timeout: int = 30,
        upload_timeout: int = 300,
    ):
        if not platform_api_base.startswith(("https://", "http://")):
            raise ValueError("platform_api_base must be an HTTP(S) URL")
        if not organization:
            raise ValueError("organization must be non-empty")
        self.platform_api_base = platform_api_base.rstrip("/")
        self.organization = organization
        self.platform_token = platform_token
        self.platform_opener = platform_opener or urllib.request.urlopen
        self.connection_factory = connection_factory or http.client.HTTPSConnection
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self._database_hosts: dict[str, str] = {}
        self._read_tokens: dict[str, str] = {}

    def _platform_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> object:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif method == "POST":
            data = b""
        if self.platform_token:
            headers["Authorization"] = f"Bearer {self.platform_token}"
        request = urllib.request.Request(
            f"{self.platform_api_base}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self.platform_opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError:
            raise
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Turso Platform API request failed: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Turso Platform API returned invalid JSON") from exc

    def _database_path(self, name: str) -> str:
        org = urllib.parse.quote(self.organization, safe="")
        db = urllib.parse.quote(name, safe="")
        return f"/v1/organizations/{org}/databases/{db}"

    def _database_info(self, name: str) -> dict[str, object]:
        payload = self._platform_request("GET", self._database_path(name))
        if not isinstance(payload, dict):
            raise TypeError("Turso database response must be an object")
        raw = payload.get("database", payload)
        if not isinstance(raw, dict):
            raise TypeError("Turso database metadata must be an object")
        hostname = raw.get("Hostname", raw.get("hostname"))
        if not isinstance(hostname, str) or not hostname:
            raise RuntimeError("Turso database metadata is missing hostname")
        self._database_hosts[name] = hostname
        return raw

    def database_exists(self, name: str) -> bool:
        try:
            self._database_info(name)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise
        return True

    def create_upload_database(
        self, name: str, *, group: str, size_limit: str
    ) -> None:
        org = urllib.parse.quote(self.organization, safe="")
        payload = self._platform_request(
            "POST",
            f"/v1/organizations/{org}/databases",
            payload={
                "name": name,
                "group": group,
                "size_limit": size_limit,
                "seed": {"type": "database_upload"},
            },
        )
        if not isinstance(payload, dict):
            raise TypeError("Turso create-database response must be an object")
        raw = payload.get("database")
        if not isinstance(raw, dict):
            raise TypeError("Turso create-database response is missing database")
        hostname = raw.get("Hostname", raw.get("hostname"))
        if not isinstance(hostname, str) or not hostname:
            raise RuntimeError("Turso create-database response is missing hostname")
        self._database_hosts[name] = hostname

    def _mint_token(self, name: str, *, read_only: bool) -> str:
        authorization = "read-only" if read_only else "full-access"
        query = urllib.parse.urlencode(
            {"expiration": "20m", "authorization": authorization}
        )
        payload = self._platform_request(
            "POST",
            f"{self._database_path(name)}/auth/tokens?{query}",
        )
        if not isinstance(payload, dict):
            raise TypeError("Turso token response must be an object")
        token = payload.get("jwt")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Turso token response is missing jwt")
        return token

    def _database_host(self, name: str) -> str:
        hostname = self._database_hosts.get(name)
        if hostname is None:
            self._database_info(name)
            hostname = self._database_hosts[name]
        return hostname

    def upload_database(self, name: str, sqlite_path: Path) -> None:
        hostname = self._database_host(name)
        token = self._mint_token(name, read_only=False)
        connection = self.connection_factory(
            hostname, timeout=self.upload_timeout
        )
        try:
            connection.putrequest("POST", "/v1/upload")
            connection.putheader("Authorization", f"Bearer {token}")
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Content-Length", str(sqlite_path.stat().st_size))
            connection.endheaders()
            with sqlite_path.open("rb") as fh:
                while chunk := fh.read(1024 * 1024):
                    connection.send(chunk)
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                detail = body.decode("utf-8", errors="replace")[:500]
                raise RuntimeError(
                    f"Turso database upload failed HTTP {response.status}: {detail}"
                )
        finally:
            connection.close()

    @staticmethod
    def _decode_cell(cell: object) -> object:
        if not isinstance(cell, dict):
            raise TypeError("Turso SQL cell must be an object")
        kind = cell.get("type")
        if kind == "null":
            return None
        if kind == "integer":
            return int(str(cell.get("value")))
        if kind == "float":
            return float(str(cell.get("value")))
        if kind == "text":
            return cell.get("value")
        if kind == "blob":
            return cell.get("base64")
        raise RuntimeError(f"unsupported Turso SQL cell type: {kind}")

    def query_rows(self, name: str, sql: str) -> list[tuple[object, ...]]:
        hostname = self._database_host(name)
        token = self._read_tokens.get(name)
        if token is None:
            token = self._mint_token(name, read_only=True)
            self._read_tokens[name] = token
        body = json.dumps(
            {
                "requests": [
                    {"type": "execute", "stmt": {"sql": sql}},
                    {"type": "close"},
                ]
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://{hostname}/v2/pipeline",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Turso SQL readback failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise TypeError("Turso SQL response must be an object")
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            raise RuntimeError("Turso SQL response is missing results")
        first = results[0]
        if not isinstance(first, dict) or first.get("type") != "ok":
            raise RuntimeError(f"Turso SQL query failed: {first}")
        response = first.get("response")
        if not isinstance(response, dict):
            raise TypeError("Turso SQL result response must be an object")
        result = response.get("result")
        if not isinstance(result, dict):
            raise TypeError("Turso SQL result must be an object")
        rows = result.get("rows")
        if not isinstance(rows, list):
            raise TypeError("Turso SQL rows must be an array")
        decoded: list[tuple[object, ...]] = []
        for row in rows:
            if not isinstance(row, list):
                raise TypeError("Turso SQL row must be an array")
            decoded.append(tuple(self._decode_cell(cell) for cell in row))
        return decoded

    def set_delete_protection(self, name: str, *, enabled: bool) -> None:
        self._platform_request(
            "PATCH",
            f"{self._database_path(name)}/configuration",
            payload={"delete_protection": enabled},
        )

    def require_group(self, group: str) -> None:
        org = urllib.parse.quote(self.organization, safe="")
        payload = self._platform_request(
            "GET", f"/v1/organizations/{org}/groups"
        )
        if not isinstance(payload, dict):
            raise TypeError("Turso group inventory must be an object")
        groups = payload.get("groups")
        if not isinstance(groups, list):
            raise TypeError("Turso group inventory is missing groups")
        names = {
            item.get("name")
            for item in groups
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if group not in names:
            raise ValueError(f"Turso group does not exist: {group}")


def discover_single_organization(
    *,
    platform_api_base: str,
    platform_token: str | None,
    opener: Callable[..., Any] | None = None,
    timeout: int = 30,
) -> str:
    headers = {"Accept": "application/json"}
    if platform_token:
        headers["Authorization"] = f"Bearer {platform_token}"
    request = urllib.request.Request(
        f"{platform_api_base.rstrip('/')}/v1/organizations",
        headers=headers,
        method="GET",
    )
    open_url = opener or urllib.request.urlopen
    with open_url(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list) or len(payload) != 1:
        raise ValueError(
            "organization discovery requires exactly one visible Turso organization"
        )
    item = payload[0]
    if not isinstance(item, dict):
        raise TypeError("Turso organization entry must be an object")
    slug = item.get("slug")
    if not isinstance(slug, str) or not slug:
        raise ValueError("Turso organization entry is missing slug")
    return slug


def sync_accepted_catalog(
    *,
    catalog_path: Path,
    cache_root: Path,
    repository_root: Path,
    client: TursoMaterializerClient,
    group: str,
    size_limit: str,
    gh_executable: str = "gh",
) -> dict[str, object]:
    catalog = load_accepted_catalog(catalog_path)
    states, unavailable = ensure_local_catalog(
        catalog,
        repository_root=repository_root,
        cache_root=cache_root,
        gh_executable=gh_executable,
    )
    receipts = materialize_catalog_states(
        states,
        unavailable,
        client=client,
        group=group,
        size_limit=size_limit,
    )
    return {
        "schema": _CATALOG_MATERIALIZATION_SCHEMA,
        "status": "PASS",
        "catalog_sha256": accepted_catalog_sha256(catalog),
        "corpora": list(receipts),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify accepted Repository Search releases, build canonical SQLite "
            "projections, and materialize them to Turso/libSQL."
        )
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("producer/accepted-corpora.json"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp/theseus-repo-search-turso-cache"),
    )
    parser.add_argument("--group", default="repo-search")
    parser.add_argument("--size-limit", default="1gb")
    parser.add_argument(
        "--platform-api-base",
        default=os.environ.get(
            "TURSO_PLATFORM_API_BASE", "https://api.turso.tech"
        ),
    )
    parser.add_argument("--organization")
    parser.add_argument("--platform-token-env", default="TURSO_API_TOKEN")
    parser.add_argument("--gh-executable", default="gh")
    return parser


def main() -> int:
    args = _parser().parse_args()
    catalog_path = args.catalog.resolve()
    repository_root = catalog_path.parent.parent
    platform_token = os.environ.get(args.platform_token_env)
    organization = args.organization or discover_single_organization(
        platform_api_base=args.platform_api_base,
        platform_token=platform_token,
    )
    client = TursoHttpClient(
        platform_api_base=args.platform_api_base,
        organization=organization,
        platform_token=platform_token,
    )
    client.require_group(args.group)
    receipt = sync_accepted_catalog(
        catalog_path=catalog_path,
        cache_root=args.cache_dir.resolve(),
        repository_root=repository_root,
        client=client,
        group=args.group,
        size_limit=args.size_limit,
        gh_executable=args.gh_executable,
    )
    print(
        json.dumps(
            receipt,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
