from __future__ import annotations

import hashlib
import http.client
import json
import re
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import RepoSearchError
from .multicorpus import LocalCorpusState

_TURSO_NAME = re.compile(r"^[a-z0-9-]{1,52}$")
_DATABASE_PREFIX = "repo-search-"


@dataclass(frozen=True)
class PreparedUpload:
    path: Path
    artifact_identity: str
    nodes_count: int
    edges_count: int
    sources_count: int
    fts_count: int
    page_size: int
    page_count: int
    auto_vacuum: int
    encoding: str
    journal_mode: str
    quick_check: str
    integrity_check: str
    sqlite_bytes: int


@dataclass(frozen=True)
class TursoMaterializationResult:
    status: str
    source_id: str
    database: str
    artifact_identity: str
    projection_fingerprint: str
    sqlite_bytes: int


class TursoClient(Protocol):
    def get_database(self, name: str) -> dict[str, object] | None: ...

    def create_upload_database(
        self, name: str, *, group: str, size_limit: str | None = None
    ) -> dict[str, object]: ...

    def mint_database_token(
        self, name: str, *, expiration: str, authorization: str
    ) -> str: ...

    def upload_sqlite(self, hostname: str, token: str, db_path: Path) -> None: ...

    def read_projection_state(
        self, hostname: str, token: str
    ) -> dict[str, object]: ...

    def set_delete_protection(self, name: str, enabled: bool) -> None: ...


def derive_database_name(source_id: str, artifact_identity: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_identity):
        raise ValueError("artifact_identity must be 64 lowercase hexadecimal characters")
    slug = re.sub(r"[^a-z0-9]+", "-", source_id.lower()).strip("-")
    if not slug:
        raise ValueError("source_id cannot produce an empty Turso database name")
    identity_suffix = artifact_identity[:8]
    available = 52 - len(_DATABASE_PREFIX) - len(identity_suffix) - 1
    if len(slug) > available:
        source_suffix = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug[: available - len(source_suffix) - 1].rstrip('-')}-{source_suffix}"
    name = f"{_DATABASE_PREFIX}{slug}-{identity_suffix}"
    if not _TURSO_NAME.fullmatch(name):
        raise ValueError(f"invalid Turso database name derived from source_id: {source_id!r}")
    return name


def _scalar(conn: sqlite3.Connection, sql: str) -> object:
    row = conn.execute(sql).fetchone()
    if row is None:
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY", f"missing SQLite result for: {sql}"
        )
    return row[0]


def _int_scalar(conn: sqlite3.Connection, sql: str) -> int:
    value = _scalar(conn, sql)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            f"expected integer SQLite result for: {sql}",
        )
    return value


def _text_scalar(conn: sqlite3.Connection, sql: str) -> str:
    value = _scalar(conn, sql)
    if not isinstance(value, str):
        raise RepoSearchError(
            "BLOCKED_PROJECTION_INTEGRITY",
            f"expected text SQLite result for: {sql}",
        )
    return value


def prepare_upload_copy(source: Path, destination: Path) -> PreparedUpload:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)

    conn = sqlite3.connect(destination)
    try:
        page_size = _int_scalar(conn, "PRAGMA page_size")
        auto_vacuum = _int_scalar(conn, "PRAGMA auto_vacuum")
        encoding = _text_scalar(conn, "PRAGMA encoding")
        if page_size != 4096:
            raise RepoSearchError(
                "TURSO_UPLOAD_INCOMPATIBLE",
                f"Turso upload requires page_size=4096, observed={page_size}",
            )
        if auto_vacuum != 0:
            raise RepoSearchError(
                "TURSO_UPLOAD_INCOMPATIBLE",
                f"Turso upload requires auto_vacuum=0, observed={auto_vacuum}",
            )
        if encoding.upper() != "UTF-8":
            raise RepoSearchError(
                "TURSO_UPLOAD_INCOMPATIBLE",
                f"Turso upload requires UTF-8 encoding, observed={encoding}",
            )

        journal_mode = _text_scalar(conn, "PRAGMA journal_mode=WAL")
        if journal_mode.lower() != "wal":
            raise RepoSearchError(
                "TURSO_UPLOAD_INCOMPATIBLE",
                f"Turso upload requires WAL journal mode, observed={journal_mode}",
            )
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        quick_check = _text_scalar(conn, "PRAGMA quick_check")
        integrity_check = _text_scalar(conn, "PRAGMA integrity_check")
        if quick_check != "ok" or integrity_check != "ok":
            raise RepoSearchError(
                "BLOCKED_PROJECTION_INTEGRITY",
                "SQLite projection failed pre-upload integrity checks",
            )

        artifact_identity_row = conn.execute(
            "SELECT value FROM meta WHERE key='artifact_identity'"
        ).fetchone()
        if artifact_identity_row is None:
            raise RepoSearchError(
                "BLOCKED_PROJECTION_INTEGRITY",
                "SQLite projection is missing artifact_identity metadata",
            )
        artifact_identity = str(artifact_identity_row[0])
        nodes_count = _int_scalar(conn, "SELECT count(*) FROM nodes")
        edges_count = _int_scalar(conn, "SELECT count(*) FROM edges")
        sources_count = _int_scalar(conn, "SELECT count(*) FROM sources")
        fts_count = _int_scalar(conn, "SELECT count(*) FROM sources_fts")
        page_count = _int_scalar(conn, "PRAGMA page_count")
    finally:
        conn.close()

    sqlite_bytes = destination.stat().st_size
    if sqlite_bytes != page_size * page_count:
        raise RepoSearchError(
            "TURSO_UPLOAD_INCOMPATIBLE",
            "SQLite file size does not match page_size * page_count",
        )

    return PreparedUpload(
        path=destination,
        artifact_identity=artifact_identity,
        nodes_count=nodes_count,
        edges_count=edges_count,
        sources_count=sources_count,
        fts_count=fts_count,
        page_size=page_size,
        page_count=page_count,
        auto_vacuum=auto_vacuum,
        encoding=encoding,
        journal_mode=journal_mode,
        quick_check=quick_check,
        integrity_check=integrity_check,
        sqlite_bytes=sqlite_bytes,
    )


def _database_hostname(database: dict[str, object]) -> str:
    value = database.get("hostname") or database.get("Hostname")
    if not isinstance(value, str) or not value:
        raise RepoSearchError(
            "TURSO_API_INVALID_RESPONSE", "Turso database response is missing hostname"
        )
    return value


def _verify_remote(
    remote: dict[str, object],
    prepared: PreparedUpload,
    *,
    expected_artifact_identity: str,
) -> None:
    observed_identity = remote.get("artifact_identity")
    if observed_identity != expected_artifact_identity:
        raise RepoSearchError(
            "TURSO_DATABASE_IDENTITY_MISMATCH",
            "existing Turso database artifact_identity does not match accepted corpus",
        )

    expected = {
        "nodes_count": prepared.nodes_count,
        "edges_count": prepared.edges_count,
        "sources_count": prepared.sources_count,
        "fts_count": prepared.fts_count,
        "quick_check": "ok",
        "integrity_check": "ok",
        "page_size": prepared.page_size,
        "page_count": prepared.page_count,
        "journal_mode": "wal",
    }
    for key, value in expected.items():
        observed = remote.get(key)
        if key == "journal_mode" and isinstance(observed, str):
            observed = observed.lower()
        if observed != value:
            raise RepoSearchError(
                "TURSO_REMOTE_PROJECTION_MISMATCH",
                f"Turso remote {key} mismatch: expected={value!r} observed={observed!r}",
            )

    if prepared.sqlite_bytes != prepared.page_size * prepared.page_count:
        raise RepoSearchError(
            "TURSO_REMOTE_PROJECTION_MISMATCH",
            "prepared SQLite byte size does not match verified page geometry",
        )


class TursoMaterializer:
    def __init__(self, client: TursoClient):
        self.client = client

    def materialize(
        self,
        state: LocalCorpusState,
        *,
        group: str,
        database: str | None = None,
        size_limit: str | None = None,
        token_expiration: str = "15m",
    ) -> TursoMaterializationResult:
        database = database or derive_database_name(state.source_id, state.artifact_identity)
        if not _TURSO_NAME.fullmatch(database):
            raise ValueError(
                "database must contain only lowercase letters, numbers, dashes "
                "and be at most 52 characters"
            )

        with tempfile.TemporaryDirectory(prefix="repo-search-turso-") as temp:
            prepared = prepare_upload_copy(
                state.db_path, Path(temp) / "index-upload.sqlite"
            )
            if prepared.artifact_identity != state.artifact_identity:
                raise RepoSearchError(
                    "BLOCKED_PROJECTION_INTEGRITY",
                    "local projection artifact identity differs from accepted corpus state",
                )

            existing = self.client.get_database(database)
            if existing is None:
                remote_db = self.client.create_upload_database(
                    database, group=group, size_limit=size_limit
                )
                hostname = _database_hostname(remote_db)
                token = self.client.mint_database_token(
                    database,
                    expiration=token_expiration,
                    authorization="full-access",
                )
                self.client.upload_sqlite(hostname, token, prepared.path)
                status = "CREATED"
            else:
                hostname = _database_hostname(existing)
                token = self.client.mint_database_token(
                    database,
                    expiration=token_expiration,
                    authorization="read-only",
                )
                status = "REUSED"

            remote = self.client.read_projection_state(hostname, token)
            _verify_remote(
                remote,
                prepared,
                expected_artifact_identity=state.artifact_identity,
            )
            self.client.set_delete_protection(database, True)

        return TursoMaterializationResult(
            status=status,
            source_id=state.source_id,
            database=database,
            artifact_identity=state.artifact_identity,
            projection_fingerprint=state.projection_fingerprint,
            sqlite_bytes=prepared.sqlite_bytes,
        )


class TursoHttpClient:
    def __init__(
        self,
        *,
        organization: str,
        platform_base_url: str = "https://api.turso.tech",
        platform_token: str | None = None,
        timeout_seconds: float = 30.0,
        verification_timeout_seconds: float = 300.0,
    ):
        self.organization = organization
        self.platform_base_url = platform_base_url.rstrip("/")
        self.platform_token = platform_token
        self.timeout_seconds = timeout_seconds
        self.verification_timeout_seconds = verification_timeout_seconds

    def _platform_url(self, suffix: str) -> str:
        org = urllib.parse.quote(self.organization, safe="")
        return f"{self.platform_base_url}/v1/organizations/{org}{suffix}"

    def _json_request(
        self,
        method: str,
        url: str,
        *,
        payload: object | None = None,
        token: str | None = None,
        allow_not_found: bool = False,
        timeout_seconds: float | None = None,
    ) -> object | None:
        headers = {"Accept": "application/json"}
        bearer = token if token is not None else self.platform_token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            with urllib.request.urlopen(
                request, timeout=timeout
            ) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RepoSearchError(
                "TURSO_HTTP_ERROR",
                f"Turso HTTP {exc.code} for {method} {url}: {detail}",
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RepoSearchError(
                "TURSO_HTTP_ERROR", f"Turso request failed for {method} {url}: {exc}"
            ) from exc

        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE",
                f"Turso returned invalid JSON for {method} {url}",
            ) from exc

    def get_database(self, name: str) -> dict[str, object] | None:
        encoded = urllib.parse.quote(name, safe="")
        payload = self._json_request(
            "GET",
            self._platform_url(f"/databases/{encoded}"),
            allow_not_found=True,
        )
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE", "Turso database response is not an object"
            )
        database = payload.get("database", payload)
        if not isinstance(database, dict):
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE", "Turso database payload is not an object"
            )
        return database

    def create_upload_database(
        self, name: str, *, group: str, size_limit: str | None = None
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "name": name,
            "group": group,
            "seed": {"type": "database_upload"},
        }
        if size_limit is not None:
            body["size_limit"] = size_limit
        payload = self._json_request(
            "POST", self._platform_url("/databases"), payload=body
        )
        if not isinstance(payload, dict):
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE",
                "Turso create database response is not an object",
            )
        database = payload.get("database")
        if not isinstance(database, dict):
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE",
                "Turso create database response is missing database object",
            )
        return database

    def mint_database_token(
        self, name: str, *, expiration: str, authorization: str
    ) -> str:
        encoded = urllib.parse.quote(name, safe="")
        query = urllib.parse.urlencode(
            {"expiration": expiration, "authorization": authorization}
        )
        payload = self._json_request(
            "POST",
            self._platform_url(f"/databases/{encoded}/auth/tokens?{query}"),
        )
        if not isinstance(payload, dict):
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE", "Turso token response is not an object"
            )
        token = payload.get("jwt") or payload.get("token")
        if not isinstance(token, str) or not token:
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE", "Turso token response is missing token"
            )
        return token

    def upload_sqlite(self, hostname: str, token: str, db_path: Path) -> None:
        parsed = urllib.parse.urlparse(
            hostname if "://" in hostname else f"https://{hostname}"
        )
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Turso database hostname must use HTTPS")
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=self.timeout_seconds,
        )
        size = db_path.stat().st_size
        try:
            connection.putrequest("POST", "/v1/upload")
            connection.putheader("Authorization", f"Bearer {token}")
            connection.putheader("Content-Length", str(size))
            connection.endheaders()
            with db_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    connection.send(chunk)
            response = connection.getresponse()
            body = response.read()
            if response.status < 200 or response.status >= 300:
                detail = body.decode("utf-8", errors="replace")[:500]
                raise RepoSearchError(
                    "TURSO_UPLOAD_FAILED",
                    f"Turso upload HTTP {response.status}: {detail}",
                )
        finally:
            connection.close()

    @staticmethod
    def _decode_cell(cell: object) -> object:
        if not isinstance(cell, dict):
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE", "invalid Hrana cell"
            )
        kind = cell.get("type")
        if kind == "null":
            return None
        value = cell.get("value")
        if kind == "integer":
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise RepoSearchError(
                    "TURSO_API_INVALID_RESPONSE", "invalid Hrana integer cell"
                )
            return int(value)
        if kind == "float":
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise RepoSearchError(
                    "TURSO_API_INVALID_RESPONSE", "invalid Hrana float cell"
                )
            return float(value)
        if kind == "text":
            if not isinstance(value, str):
                raise RepoSearchError(
                    "TURSO_API_INVALID_RESPONSE", "invalid Hrana text cell"
                )
            return value
        raise RepoSearchError(
            "TURSO_API_INVALID_RESPONSE", f"unsupported Hrana cell type: {kind!r}"
        )

    def _pipeline(
        self, hostname: str, token: str, statements: list[str]
    ) -> list[list[tuple[object, ...]]]:
        requests = [
            {"type": "execute", "stmt": {"sql": statement}}
            for statement in statements
        ]
        requests.append({"type": "close"})
        payload = self._json_request(
            "POST",
            f"https://{hostname}/v2/pipeline",
            payload={"requests": requests},
            token=token,
            timeout_seconds=self.verification_timeout_seconds,
        )
        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list
        ):
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE", "invalid Turso pipeline response"
            )
        decoded: list[list[tuple[object, ...]]] = []
        results = payload["results"]
        for index, result in enumerate(results[:-1]):
            if not isinstance(result, dict) or result.get("type") != "ok":
                raise RepoSearchError(
                    "TURSO_SQL_ERROR",
                    f"Turso SQL statement {index} failed: {result!r}",
                )
            response = result.get("response")
            if not isinstance(response, dict):
                raise RepoSearchError(
                    "TURSO_API_INVALID_RESPONSE", "invalid Turso SQL response"
                )
            result_set = response.get("result")
            if not isinstance(result_set, dict) or not isinstance(
                result_set.get("rows"), list
            ):
                raise RepoSearchError(
                    "TURSO_API_INVALID_RESPONSE", "invalid Turso SQL result set"
                )
            decoded.append(
                [
                    tuple(self._decode_cell(cell) for cell in row)
                    for row in result_set["rows"]
                ]
            )
        return decoded

    def read_projection_state(
        self, hostname: str, token: str
    ) -> dict[str, object]:
        rows = self._pipeline(
            hostname,
            token,
            [
                "SELECT value FROM meta WHERE key='artifact_identity'",
                "SELECT count(*) FROM nodes",
                "SELECT count(*) FROM edges",
                "SELECT count(*) FROM sources",
                "SELECT count(*) FROM sources_fts",
                "PRAGMA quick_check",
                "PRAGMA integrity_check",
                "PRAGMA page_size",
                "PRAGMA page_count",
                "PRAGMA journal_mode",
            ],
        )
        if any(not item for item in rows):
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE",
                "Turso projection verification returned an empty scalar result",
            )
        values = [item[0][0] for item in rows]
        return {
            "artifact_identity": values[0],
            "nodes_count": values[1],
            "edges_count": values[2],
            "sources_count": values[3],
            "fts_count": values[4],
            "quick_check": values[5],
            "integrity_check": values[6],
            "page_size": values[7],
            "page_count": values[8],
            "journal_mode": values[9],
        }

    def set_delete_protection(self, name: str, enabled: bool) -> None:
        encoded = urllib.parse.quote(name, safe="")
        payload = self._json_request(
            "PATCH",
            self._platform_url(f"/databases/{encoded}/configuration"),
            payload={"delete_protection": enabled},
        )
        if not isinstance(payload, dict) or payload.get("delete_protection") is not enabled:
            raise RepoSearchError(
                "TURSO_API_INVALID_RESPONSE",
                "Turso delete-protection readback mismatch",
            )
