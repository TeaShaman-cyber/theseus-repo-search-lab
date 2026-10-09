#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any


class HarnessError(RuntimeError):
    pass


@dataclass
class PsqlSession:
    application_name: str
    _counter: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _process: subprocess.Popen[str] = field(init=False)

    def __post_init__(self) -> None:
        env = os.environ.copy()
        env["PGAPPNAME"] = self.application_name
        self._process = subprocess.Popen(
            ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise HarnessError("failed to open psql session pipes")

    def step(self, sql: str, label: str) -> list[str]:
        with self._lock:
            self._counter += 1
            marker = (
                f"__HEAVY_PG_DONE_{self.application_name}_"
                f"{self._counter}_{label}__"
            )
            stdin = self._process.stdin
            stdout = self._process.stdout
            if stdin is None or stdout is None:
                raise HarnessError(f"{self.application_name}: psql pipes closed")

            stdin.write(sql.rstrip() + "\n")
            stdin.write(f"\\echo {marker}\n")
            stdin.flush()

            lines: list[str] = []
            while True:
                line = stdout.readline()
                if line == "":
                    return_code = self._process.poll()
                    raise HarnessError(
                        f"{self.application_name}:{label}: psql exited "
                        f"before marker rc={return_code}; output={lines!r}"
                    )
                text = line.rstrip("\n")
                if text == marker:
                    return lines
                lines.append(text)

    def close(self) -> None:
        if self._process.poll() is None and self._process.stdin is not None:
            try:
                self._process.stdin.write("\\q\n")
                self._process.stdin.flush()
            except BrokenPipeError:
                pass
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=2)


def one_shot(sql: str) -> str:
    env = os.environ.copy()
    env["PGAPPNAME"] = "heavy-pg-control"
    completed = subprocess.run(
        ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def scalar(sql: str) -> str:
    value = one_shot(sql)
    if "\n" in value:
        raise HarnessError(f"expected scalar result, got {value!r}")
    return value


def wait_for_lock(application_name: str, timeout_seconds: float = 5.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    last = ""
    while time.monotonic() < deadline:
        escaped = application_name.replace("'", "''")
        last = scalar(
            "SELECT coalesce(wait_event_type, '') || '|' || "
            "coalesce(wait_event, '') "
            "FROM pg_stat_activity "
            f"WHERE application_name = '{escaped}'"
        )
        if last.startswith("Lock|"):
            return last
        threading.Event().wait(0.02)
    raise HarnessError(
        f"{application_name}: expected Lock wait state, last={last!r}"
    )


def async_step(
    session: PsqlSession,
    sql: str,
    label: str,
) -> tuple[threading.Thread, dict[str, Any]]:
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["lines"] = session.step(sql, label)
        except (HarnessError, OSError) as exc:
            result["error"] = exc

    thread = threading.Thread(
        target=run,
        name=f"{session.application_name}-{label}",
        daemon=True,
    )
    thread.start()
    return thread, result


def join_step(
    thread: threading.Thread,
    result: dict[str, Any],
    label: str,
    timeout_seconds: float = 5.0,
) -> list[str]:
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise HarnessError(f"{label}: asynchronous step did not finish")
    error = result.get("error")
    if error is not None:
        raise HarnessError(f"{label}: {error}") from error
    return list(result.get("lines", []))


def assert_scalar(sql: str, expected: str, label: str) -> None:
    actual = scalar(sql)
    if actual != expected:
        raise HarnessError(f"{label}: expected {expected!r}, got {actual!r}")


def writer_first(writer: PsqlSession, ready: PsqlSession) -> None:
    print("CONCURRENCY_CASE writer-first begin", flush=True)
    writer.step("BEGIN;", "writer_begin")
    writer.step(
        "INSERT INTO heavy_pg_concurrency.children "
        "(child_id, generation_id, payload) "
        "VALUES ('wf-child', 'writer-first', 'payload');",
        "writer_insert",
    )

    ready_thread, ready_result = async_step(
        ready,
        "SELECT heavy_pg_concurrency.mark_ready('writer-first');",
        "ready_waits",
    )
    wait = wait_for_lock(ready.application_name)
    print(f"CONCURRENCY_OBSERVED ready_wait={wait}", flush=True)

    writer.step("COMMIT;", "writer_commit")
    join_step(ready_thread, ready_result, "writer-first READY")

    assert_scalar(
        "SELECT state FROM heavy_pg_concurrency.generations "
        "WHERE generation_id = 'writer-first'",
        "READY",
        "writer-first state",
    )
    assert_scalar(
        "SELECT count(*) FROM heavy_pg_concurrency.children "
        "WHERE generation_id = 'writer-first'",
        "1",
        "writer-first child count",
    )
    print("CONCURRENCY_CASE writer-first pass", flush=True)


def ready_first(ready: PsqlSession, writer: PsqlSession) -> None:
    print("CONCURRENCY_CASE ready-first begin", flush=True)
    ready.step("BEGIN;", "ready_begin")
    ready.step(
        "SELECT heavy_pg_concurrency.mark_ready('ready-first');",
        "ready_mark",
    )

    writer_sql = r"""
DO $block$
BEGIN
    BEGIN
        INSERT INTO heavy_pg_concurrency.children
            (child_id, generation_id, payload)
        VALUES
            ('rf-child', 'ready-first', 'must-not-land');
        RAISE EXCEPTION 'late writer unexpectedly succeeded';
    EXCEPTION
        WHEN SQLSTATE '55000' THEN
            IF SQLERRM <> 'generation ready-first is not BUILDING' THEN
                RAISE;
            END IF;
    END;
END
$block$;
"""
    writer_thread, writer_result = async_step(
        writer,
        writer_sql,
        "writer_waits_then_rechecks",
    )
    wait = wait_for_lock(writer.application_name)
    print(f"CONCURRENCY_OBSERVED writer_wait={wait}", flush=True)

    ready.step("COMMIT;", "ready_commit")
    join_step(writer_thread, writer_result, "ready-first writer")

    assert_scalar(
        "SELECT state FROM heavy_pg_concurrency.generations "
        "WHERE generation_id = 'ready-first'",
        "READY",
        "ready-first state",
    )
    assert_scalar(
        "SELECT count(*) FROM heavy_pg_concurrency.children "
        "WHERE generation_id = 'ready-first'",
        "0",
        "ready-first rejected child count",
    )
    print("CONCURRENCY_CASE ready-first pass", flush=True)


def stale_cas_activation(winner: PsqlSession, stale: PsqlSession) -> None:
    print("CONCURRENCY_CASE cas-stale-activator begin", flush=True)
    winner.step("BEGIN;", "cas_winner_begin")
    result = winner.step(
        "SELECT heavy_pg_concurrency.activate_if_current("
        "'cas-base', 'cas-winner');",
        "cas_winner_activate",
    )
    if result != ["t"]:
        raise HarnessError(f"CAS winner: expected ['t'], got {result!r}")

    stale_thread, stale_result = async_step(
        stale,
        "SELECT heavy_pg_concurrency.activate_if_current("
        "'cas-base', 'cas-stale');",
        "cas_stale_waits",
    )
    wait = wait_for_lock(stale.application_name)
    print(f"CONCURRENCY_OBSERVED cas_stale_wait={wait}", flush=True)

    winner.step("COMMIT;", "cas_winner_commit")
    outcome = join_step(stale_thread, stale_result, "CAS stale activator")
    if outcome != ["f"]:
        raise HarnessError(f"CAS stale: expected ['f'], got {outcome!r}")
    assert_scalar(
        "SELECT active_generation_id FROM heavy_pg_concurrency.active_slot "
        "WHERE slot_id = 1",
        "cas-winner",
        "CAS winner remains active",
    )
    print("CAS_STALE_ACTIVATOR_PASS", flush=True)


def immutable_child_generation() -> None:
    print("CONCURRENCY_CASE immutable-child-generation begin", flush=True)
    one_shot("""
DO $block$
BEGIN
    BEGIN
        UPDATE heavy_pg_concurrency.children
        SET generation_id = 'immutable-to'
        WHERE child_id = 'immutable-child';
        RAISE EXCEPTION 'generation_id mutation unexpectedly succeeded';
    EXCEPTION
        WHEN SQLSTATE '55000' THEN
            IF SQLERRM <> 'child generation_id is immutable' THEN
                RAISE;
            END IF;
    END;
END
$block$;
""")
    assert_scalar(
        "SELECT generation_id FROM heavy_pg_concurrency.children "
        "WHERE child_id = 'immutable-child'",
        "immutable-from",
        "immutable child remains with original parent",
    )
    one_shot(
        "UPDATE heavy_pg_concurrency.children SET payload = 'changed' "
        "WHERE child_id = 'immutable-child';"
    )
    assert_scalar(
        "SELECT payload FROM heavy_pg_concurrency.children "
        "WHERE child_id = 'immutable-child'",
        "changed",
        "non-key update remains permitted",
    )
    print("IMMUTABLE_CHILD_GENERATION_PASS", flush=True)



def expect_gc_rejected(target: str, identity: str, error: str) -> None:
    # All fixture parameters are fixed, ASCII-only test literals.
    for literal in (target, identity, error):
        if not literal.replace('-', '').replace(' ', '').isalnum():
            raise HarnessError(f"unsafe GC fixture value: {literal!r}")
    one_shot(f"""
DO $block$
BEGIN
    BEGIN
        PERFORM heavy_pg_concurrency.gc_inactive_generation(
            '{target}', '{identity}');
        RAISE EXCEPTION 'GC unexpectedly succeeded';
    EXCEPTION
        WHEN SQLSTATE '55000' THEN
            IF SQLERRM <> '{error}' THEN
                RAISE;
            END IF;
    END;
END
$block$;
""")


def guarded_inactive_ready_gc() -> None:
    print("CONCURRENCY_CASE guarded-inactive-ready-gc begin", flush=True)
    assert_scalar(
        "SELECT count(*) FROM heavy_pg_concurrency.children "
        "WHERE generation_id = 'gc-disposable'", "2", "GC fixture child count",
    )
    assert_scalar(
        "SELECT active_generation_id FROM heavy_pg_concurrency.active_slot "
        "WHERE slot_id = 1", "cas-winner", "GC precondition active generation",
    )

    expect_gc_rejected("cas-winner", "fixture-digest", "cannot GC active generation")
    expect_gc_rejected("gc-disposable", "wrong-digest", "GC identity mismatch")
    expect_gc_rejected("gc-referenced", "gc-digest-2", "GC target referenced")
    expect_gc_rejected("gc-building", "fixture-digest", "GC target not READY")
    expect_gc_rejected("unknown", "fixture-digest", "GC target missing")

    # Direct row-level DELETE of a READY child remains forbidden.
    one_shot("""
DO $block$
BEGIN
    BEGIN
        DELETE FROM heavy_pg_concurrency.children
        WHERE child_id = 'gc-child-1';
        RAISE EXCEPTION 'READY child unexpectedly deleted';
    EXCEPTION
        WHEN SQLSTATE '55000' THEN
            IF SQLERRM <> 'generation gc-disposable is not BUILDING' THEN
                RAISE;
            END IF;
    END;
END
$block$;
""")
    assert_scalar(
        "SELECT count(*) FROM heavy_pg_concurrency.children "
        "WHERE generation_id = 'gc-disposable'", "2", "GC rejected attempts intact",
    )

    result = scalar(
        "SELECT heavy_pg_concurrency.gc_inactive_generation("
        "'gc-disposable', 'gc-digest-1')"
    )
    if result != "t":
        raise HarnessError(f"GC disposable generation: expected 't', got {result!r}")
    assert_scalar(
        "SELECT count(*) FROM heavy_pg_concurrency.generations "
        "WHERE generation_id = 'gc-disposable'", "0", "GC removed parent",
    )
    assert_scalar(
        "SELECT count(*) FROM heavy_pg_concurrency.children "
        "WHERE generation_id = 'gc-disposable'", "0", "GC removed children",
    )
    assert_scalar(
        "SELECT active_generation_id FROM heavy_pg_concurrency.active_slot "
        "WHERE slot_id = 1", "cas-winner", "GC retained active pointer",
    )
    assert_scalar(
        "SELECT count(*) FROM heavy_pg_concurrency.generations "
        "WHERE generation_id = 'gc-referenced'", "1", "GC preserved referenced generation",
    )
    print("GC_INACTIVE_READY_PASS", flush=True)


def assert_search_rejected(generation_id: str, expected_error: str) -> None:
    if not generation_id.replace('-', '').isalnum():
        raise HarnessError(f"invalid search fixture id: {generation_id!r}")
    if not expected_error.replace(' ', '').isalnum():
        raise HarnessError(f"invalid search fixture error: {expected_error!r}")
    one_shot(f"""
DO $block$
BEGIN
    BEGIN
        PERFORM heavy_pg_concurrency.search_generation('{generation_id}', 'match');
        RAISE EXCEPTION 'search unexpectedly succeeded';
    EXCEPTION
        WHEN SQLSTATE '55000' THEN
            IF SQLERRM <> '{expected_error}' THEN
                RAISE;
            END IF;
    END;
END
$block$;
""")


def version_dispatch_v1_v2() -> None:
    print("CONCURRENCY_CASE version-dispatch-v1-v2 begin", flush=True)
    v1_definition_sql = (
        "SELECT md5(pg_get_functiondef("
        "'heavy_pg_concurrency.search_generation_v1(text,text)'::regprocedure))"
    )
    original_v1_definition = scalar(v1_definition_sql)
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_generation('dispatch-v1', 'match')",
        "v1-a,v1-b", "v1 baseline ordering",
    )
    assert_scalar(
        "SELECT to_regprocedure("
        "'heavy_pg_concurrency.search_generation_v2(text,text)') IS NULL",
        "t", "v2 not yet installed",
    )
    assert_search_rejected("dispatch-v2", "unsupported projection schema version")
    assert_search_rejected("dispatch-unknown", "unsupported projection schema version")
    assert_search_rejected("gc-building", "search generation not READY")
    assert_search_rejected("missing-generation", "search generation missing")

    assert_scalar(
        "SELECT heavy_pg_concurrency.activate_if_current("
        "'cas-winner', 'dispatch-v1')",
        "t", "activate v1",
    )
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_active('match')",
        "v1-a,v1-b", "v1 active before migration",
    )

    completed = subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-f",
         "qa/postgres/version-dispatch-v2.sql"],
        check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise HarnessError(
            "v2 migration failed: " + completed.stdout + completed.stderr
        )
    if "HEAVY_POSTGRES_VERSION_V2_MIGRATION_PASS" not in completed.stdout:
        raise HarnessError("v2 migration success marker missing")

    assert_scalar(
        v1_definition_sql, original_v1_definition,
        "v1 implementation unchanged after migration",
    )
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_active('match')",
        "v1-a,v1-b", "active v1 unchanged after v2 installation",
    )
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_generation('dispatch-v1', 'match')",
        "v1-a,v1-b", "inactive/active v1 direct dispatch",
    )
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_generation('dispatch-v2', 'match')",
        "v2-b,v2-a", "inactive v2 dispatch ordering",
    )
    assert_search_rejected("dispatch-unknown", "unsupported projection schema version")
    assert_search_rejected("gc-building", "search generation not READY")
    assert_scalar(
        "SELECT heavy_pg_concurrency.activate_if_current("
        "'dispatch-v1', 'dispatch-v2')",
        "t", "activate v2",
    )
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_active('match')",
        "v2-b,v2-a", "active v2 after CAS",
    )
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_generation('dispatch-v1', 'match')",
        "v1-a,v1-b", "historical v1 preserved after v2 activation",
    )
    print("VERSION_DISPATCH_V1_V2_PASS", flush=True)



def check_envelope(payload: str, version: str, hits: list[str]) -> None:
    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HarnessError(f"zero-hit envelope is not JSON: {payload!r}") from exc
    expected = {
        "generation_id": f"dispatch-{version}",
        "catalog_sha256": f"catalog-{version}",
        "generation_content_sha256": f"content-{version}",
        "projection_schema_version": f"repo-search-query-{version}",
        "searched_corpora": 1,
        "unavailable_corpora": [
            {"source_id": f"missing-{version}", "reason": "unavailable fixture"}
        ],
        "hits": hits,
    }
    if envelope != expected:
        raise HarnessError(
            f"{version} atomic envelope mismatch: "
            f"expected={expected!r} got={envelope!r}"
        )


def zero_hit_atomic_envelope() -> None:
    print("CONCURRENCY_CASE zero-hit-atomic-envelope begin", flush=True)
    reader = PsqlSession("heavy-pg-envelope-reader")
    query = "SELECT heavy_pg_concurrency.search_envelope('absent-term')::text;"
    try:
        # Active v2 after the preceding version dispatch contract.
        reader.step("BEGIN ISOLATION LEVEL REPEATABLE READ;", "envelope_snapshot_begin")
        snapshot = reader.step(query, "v2_snapshot")
        if len(snapshot) != 1:
            raise HarnessError(f"v2 envelope expected one row, got {snapshot!r}")
        check_envelope(snapshot[0], "v2", [])

        # Concurrent CAS changes the pointer; snapshot-bound reader must
        # still see consistent v2 metadata and hits in one result envelope.
        assert_scalar(
            "SELECT heavy_pg_concurrency.activate_if_current("
            "'dispatch-v2', 'dispatch-v1')",
            "t", "envelope concurrent switch to v1",
        )
        repeated = reader.step(query, "v2_snapshot_after_activation")
        if repeated != snapshot:
            raise HarnessError(
                f"snapshot changed after activation: {snapshot!r} -> {repeated!r}"
            )
        reader.step("COMMIT;", "envelope_snapshot_commit")

        fresh_v1 = reader.step(query, "fresh_v1_zero_hit")
        if len(fresh_v1) != 1:
            raise HarnessError(f"v1 envelope expected one row, got {fresh_v1!r}")
        check_envelope(fresh_v1[0], "v1", [])
        v1_hits = reader.step(
            "SELECT heavy_pg_concurrency.search_envelope('match')::text;",
            "fresh_v1_hits",
        )
        if len(v1_hits) != 1:
            raise HarnessError(f"v1 hits expected one row, got {v1_hits!r}")
        check_envelope(v1_hits[0], "v1", ["v1-a", "v1-b"])

        assert_scalar(
            "SELECT heavy_pg_concurrency.activate_if_current("
            "'dispatch-v1', 'dispatch-v2')",
            "t", "envelope switch back to v2",
        )
        fresh_v2 = reader.step(query, "fresh_v2_zero_hit")
        if len(fresh_v2) != 1:
            raise HarnessError(f"v2 envelope expected one row, got {fresh_v2!r}")
        check_envelope(fresh_v2[0], "v2", [])
        v2_hits = reader.step(
            "SELECT heavy_pg_concurrency.search_envelope('match')::text;",
            "fresh_v2_hits",
        )
        if len(v2_hits) != 1:
            raise HarnessError(f"v2 hits expected one row, got {v2_hits!r}")
        check_envelope(v2_hits[0], "v2", ["v2-b", "v2-a"])
        print("ZERO_HIT_ATOMIC_ENVELOPE_PASS", flush=True)
    finally:
        reader.close()



def reject_bounded_query(query: str | None, limit: int | None, reason: str) -> None:
    query_sql = "NULL" if query is None else "'" + query.replace("'", "''") + "'"
    limit_sql = "NULL" if limit is None else str(limit)
    one_shot(f"""
DO $block$
BEGIN
    BEGIN
        PERFORM heavy_pg_concurrency.search_envelope({query_sql}, {limit_sql});
        RAISE EXCEPTION 'invalid query unexpectedly accepted';
    EXCEPTION
        WHEN SQLSTATE '22023' THEN
            IF SQLERRM <> '{reason}' THEN
                RAISE;
            END IF;
    END;
END
$block$;
""")


def bounded_query_inputs() -> None:
    print("CONCURRENCY_CASE query-input-bounds begin", flush=True)
    reject_bounded_query(None, 1, "query text empty")
    reject_bounded_query("   ", 1, "query text empty")
    reject_bounded_query("x" * 65, 1, "query text too long")
    reject_bounded_query("one two three four five", 1, "too many normalized terms")
    reject_bounded_query("match", None, "result limit out of bounds")
    reject_bounded_query("match", 0, "result limit out of bounds")
    reject_bounded_query("match", 11, "result limit out of bounds")

    # At every accepted boundary, preserve the exact generation metadata.
    check_envelope(
        scalar("SELECT heavy_pg_concurrency.search_envelope('match', 1)::text"),
        "v2", ["v2-b"],
    )
    check_envelope(
        scalar("SELECT heavy_pg_concurrency.search_envelope('match', 2)::text"),
        "v2", ["v2-b", "v2-a"],
    )
    check_envelope(
        scalar("SELECT heavy_pg_concurrency.search_envelope('match', 10)::text"),
        "v2", ["v2-b", "v2-a"],
    )
    check_envelope(
        scalar("SELECT heavy_pg_concurrency.search_envelope('" + "x" * 64 + "', 1)::text"),
        "v2", [],
    )
    check_envelope(
        scalar("SELECT heavy_pg_concurrency.search_envelope('one two three four', 1)::text"),
        "v2", [],
    )
    print("QUERY_INPUT_BOUNDS_PASS", flush=True)



def failed_migration_rollback() -> None:
    print("CONCURRENCY_CASE failed-migration-rollback begin", flush=True)
    v2_definition_sql = (
        "SELECT md5(pg_get_functiondef("
        "'heavy_pg_concurrency.search_generation_v2(text,text)'::regprocedure))"
    )
    original_definition = scalar(v2_definition_sql)
    original_envelope = scalar(
        "SELECT heavy_pg_concurrency.search_envelope('match', 2)::text"
    )
    check_envelope(original_envelope, "v2", ["v2-b", "v2-a"])

    completed = subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-f",
         "qa/postgres/failed-migration.sql"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        raise HarnessError("failed migration unexpectedly exited zero")
    if "INTENTIONAL_MIGRATION_FAILURE" not in completed.stderr:
        raise HarnessError(
            "wrong failure cause in migration: "
            f"rc={completed.returncode} stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r}"
        )
    if "ERROR_BAD_MIGRATION_COMMITTED" in completed.stdout:
        raise HarnessError("failed migration reached COMMIT")

    assert_scalar(
        "SELECT to_regclass('heavy_pg_concurrency.failed_migration_marker') "
        "IS NULL",
        "t", "failed migration created no lingering relation",
    )
    assert_scalar(
        v2_definition_sql,
        original_definition,
        "failed migration restored version-specific function definition",
    )
    assert_scalar(
        "SELECT catalog_sha256 FROM heavy_pg_concurrency.generations "
        "WHERE generation_id = 'dispatch-v2'",
        "catalog-v2", "failed migration restored catalog digest",
    )
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_generation('dispatch-v1', 'match')",
        "v1-a,v1-b", "v1 remains usable after failed migration",
    )
    assert_scalar(
        "SELECT heavy_pg_concurrency.search_generation('dispatch-v2', 'match')",
        "v2-b,v2-a", "v2 remains usable after failed migration",
    )
    after = scalar(
        "SELECT heavy_pg_concurrency.search_envelope('match', 2)::text"
    )
    check_envelope(after, "v2", ["v2-b", "v2-a"])
    if after != original_envelope:
        raise HarnessError("atomic search response changed after failed migration")
    print("FAILED_MIGRATION_ROLLBACK_PASS", flush=True)


def main() -> int:
    sessions = [
        PsqlSession("heavy-pg-writer-a"),
        PsqlSession("heavy-pg-ready-a"),
        PsqlSession("heavy-pg-ready-b"),
        PsqlSession("heavy-pg-writer-b"),
    ]
    try:
        writer_first(sessions[0], sessions[1])
        ready_first(sessions[2], sessions[3])
        print("READY_WRITE_SERIALIZATION_PASS", flush=True)
        stale_cas_activation(sessions[0], sessions[1])
        immutable_child_generation()
        guarded_inactive_ready_gc()
        version_dispatch_v1_v2()
        zero_hit_atomic_envelope()
        bounded_query_inputs()
        failed_migration_rollback()
        return 0
    finally:
        for session in sessions:
            session.close()
        try:
            one_shot("DROP SCHEMA IF EXISTS heavy_pg_concurrency CASCADE; "
                     "DROP ROLE IF EXISTS heavy_pg_gc_owner;")
        except subprocess.CalledProcessError as exc:
            print(
                f"CONCURRENCY_CLEANUP_FAILED rc={exc.returncode}",
                file=sys.stderr,
                flush=True,
            )


if __name__ == "__main__":
    raise SystemExit(main())
