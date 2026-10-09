#!/usr/bin/env python3
"""Two real repo_search sessions; observe PostgreSQL row-lock waits."""
from __future__ import annotations

import json

from concurrency_harness import (
    HarnessError,
    PsqlSession,
    assert_scalar,
    async_step,
    join_step,
    one_shot,
    scalar,
    wait_for_lock,
)

WF, RF = "8" * 64, "9" * 64
CAT_A, CAT_B = "c" * 64, "d" * 64


def seed() -> None:
    assert_scalar(
        "SELECT count(*) FROM repo_search.generations WHERE generation_id IN "
        f"('{WF}', '{RF}')", "0", "fixture identities unused",
    )
    one_shot(f"""
INSERT INTO repo_search.generations
    (generation_id, generation_content_sha256, catalog_sha256)
VALUES ('{WF}', repeat('a',64), '{CAT_A}'),
       ('{RF}', repeat('b',64), '{CAT_B}');
INSERT INTO repo_search.corpora
    (generation_id, source_id, release_tag, artifact_sha256,
     source_descriptor_sha256, availability)
VALUES ('{WF}', 'writer-source', 'v1', repeat('e',64),
        repeat('f',64), 'SEARCHABLE'),
       ('{RF}', 'ready-source', 'v1', repeat('e',64),
        repeat('f',64), 'SEARCHABLE');
""")


def writer_before_ready(writer: PsqlSession, ready: PsqlSession) -> None:
    writer.step("BEGIN;", "writer_begin")
    writer.step(
        "INSERT INTO repo_search.search_docs "
        "(generation_id, source_id, candidate_id, source_path, source_line, body_text) "
        f"VALUES ('{WF}', 'writer-source', 'writer::needle', "
        "'Proofs/Concurrent.lean', 12, 'needle witness');", "writer_insert",
    )
    thread, result = async_step(
        ready, f"SELECT repo_search.mark_generation_ready('{WF}', '{CAT_A}', 1);",
        "ready_waits",
    )
    print("PRODUCTION_OBSERVED ready_wait=" + wait_for_lock(ready.application_name), flush=True)
    writer.step("COMMIT;", "writer_commit")
    join_step(thread, result, "writer-before-ready")
    assert_scalar(
        f"SELECT state FROM repo_search.generations WHERE generation_id='{WF}'",
        "READY", "writer-before-ready state",
    )
    assert_scalar(
        f"SELECT count(*) FROM repo_search.search_docs WHERE generation_id='{WF}'",
        "1", "writer-before-ready committed child",
    )
    hits = json.loads(scalar(
        f"SELECT repo_search.search_generation('{WF}','needle',5,'evidence')::text"
    ))["hits"]
    if len(hits) != 1 or hits[0]["candidate_id"] != "writer::needle":
        raise HarnessError("writer child missing in READY search")
    print("PRODUCTION_CASE_WRITER_FIRST_PASS", flush=True)


def ready_before_writer(ready: PsqlSession, writer: PsqlSession) -> None:
    ready.step("BEGIN;", "ready_begin")
    ready.step(
        f"SELECT repo_search.mark_generation_ready('{RF}', '{CAT_B}', 1);",
        "ready_holds_parent",
    )
    writer_sql = f"""
DO $late$
BEGIN
    BEGIN
        INSERT INTO repo_search.search_docs
            (generation_id, source_id, candidate_id, source_path, source_line, body_text)
        VALUES ('{RF}', 'ready-source', 'late::needle', 'Proofs/Late.lean',
                17, 'must not publish');
        RAISE EXCEPTION 'late writer was accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'generation not BUILDING' THEN RAISE; END IF;
    END;
END
$late$;"""
    thread, result = async_step(writer, writer_sql, "writer_waits")
    print("PRODUCTION_OBSERVED writer_wait=" + wait_for_lock(writer.application_name), flush=True)
    ready.step("COMMIT;", "ready_commit")
    join_step(thread, result, "ready-before-writer")
    assert_scalar(
        f"SELECT state FROM repo_search.generations WHERE generation_id='{RF}'",
        "READY", "ready-before-writer state",
    )
    assert_scalar(
        f"SELECT count(*) FROM repo_search.search_docs WHERE generation_id='{RF}'",
        "0", "late writer rejected",
    )
    envelope = json.loads(scalar(
        f"SELECT repo_search.search_generation('{RF}','needle',5,'evidence')::text"
    ))
    if envelope["hits"] != [] or envelope["catalog_sha256"] != CAT_B:
        raise HarnessError("READY zero-hit envelope mismatch")
    print("PRODUCTION_CASE_READY_FIRST_PASS", flush=True)


def cleanup() -> None:
    one_shot(f"""
DO $cleanup$
DECLARE
    target text;
    state_value text;
    digest text;
BEGIN
    FOR target IN
        SELECT generation_id FROM repo_search.generations
        WHERE generation_id IN ('{WF}', '{RF}')
    LOOP
        SELECT state, generation_content_sha256 INTO state_value, digest
        FROM repo_search.generations WHERE generation_id = target;
        IF state_value = 'READY' THEN
            PERFORM repo_search.gc_inactive_generation(target, digest);
        ELSE
            DELETE FROM repo_search.search_docs WHERE generation_id=target;
            DELETE FROM repo_search.corpora WHERE generation_id=target;
            DELETE FROM repo_search.generations WHERE generation_id=target;
        END IF;
    END LOOP;
END
$cleanup$;
""")
    assert_scalar(
        "SELECT count(*) FROM repo_search.generations WHERE generation_id IN "
        f"('{WF}', '{RF}')", "0", "fixture cleanup",
    )
    print("PRODUCTION_READY_WRITE_CLEANUP_PASS", flush=True)


def main() -> int:
    seed()
    writer = PsqlSession("production-pg-writer")
    ready = PsqlSession("production-pg-ready")
    try:
        writer.step("SET ROLE repo_search_materializer;", "writer_role")
        ready.step("SET ROLE repo_search_materializer;", "ready_role")
        writer_before_ready(writer, ready)
        ready_before_writer(ready, writer)
        print("PRODUCTION_READY_WRITE_SERIALIZATION_PASS", flush=True)
        return 0
    finally:
        writer.close()
        ready.close()
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
