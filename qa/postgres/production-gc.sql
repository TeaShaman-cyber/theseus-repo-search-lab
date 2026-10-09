\set ON_ERROR_STOP on
\echo PRODUCTION_GC_BEGIN
BEGIN;

-- Real production generation/child tables: active, referenced, GC target, BUILDING.
INSERT INTO repo_search.generations
    (generation_id, generation_content_sha256, catalog_sha256)
VALUES
    (repeat('a',64), repeat('e',64), repeat('1',64)),
    (repeat('b',64), repeat('f',64), repeat('2',64)),
    (repeat('c',64), repeat('0',64), repeat('3',64)),
    (repeat('d',64), repeat('4',64), repeat('5',64));
INSERT INTO repo_search.corpora
    (generation_id, source_id, release_tag, artifact_sha256,
     source_descriptor_sha256, availability)
SELECT generation_id, 'gc-source', 'v1', repeat('6',64), repeat('7',64),
       'SEARCHABLE'
FROM repo_search.generations;
INSERT INTO repo_search.search_docs
    (generation_id, source_id, candidate_id, source_path, source_line, body_text)
SELECT generation_id, 'gc-source', 'candidate::' || left(generation_id,1),
       'Proofs/GC.lean', 10, 'gc needle'
FROM repo_search.generations;

SELECT repo_search.mark_generation_ready(repeat('a',64), repeat('1',64), 1);
SELECT repo_search.mark_generation_ready(repeat('b',64), repeat('2',64), 1);
SELECT repo_search.mark_generation_ready(repeat('c',64), repeat('3',64), 1);
SELECT repo_search.activate_generation(repeat('a',64), NULL);
INSERT INTO repo_search.generation_references (reference_id, generation_id)
VALUES ('external-proof-b', repeat('b',64));

SET ROLE repo_search_materializer;
DO $materializer_gc$
DECLARE
    gc_target text := repeat('c',64);
    protected text := repeat('b',64);
    active_id text := repeat('a',64);
BEGIN
    IF current_user <> 'repo_search_materializer' THEN
        RAISE EXCEPTION 'effective GC caller role wrong';
    END IF;
    BEGIN
        PERFORM repo_search.gc_inactive_generation(active_id, repeat('e',64));
        RAISE EXCEPTION 'active READY generation GC accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'cannot GC active generation' THEN RAISE; END IF;
    END;
    BEGIN
        PERFORM repo_search.gc_inactive_generation(repeat('d',64), repeat('4',64));
        RAISE EXCEPTION 'BUILDING generation GC accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'GC target not READY' THEN RAISE; END IF;
    END;
    BEGIN
        PERFORM repo_search.gc_inactive_generation(gc_target, repeat('9',64));
        RAISE EXCEPTION 'GC with stale content identity accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'GC content identity mismatch' THEN RAISE; END IF;
    END;
    BEGIN
        PERFORM repo_search.gc_inactive_generation(protected, repeat('f',64));
        RAISE EXCEPTION 'externally referenced READY GC accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'GC target referenced' THEN RAISE; END IF;
    END;
    IF (SELECT count(*) FROM repo_search.search_docs
        WHERE generation_id = gc_target) <> 1
       OR (SELECT count(*) FROM repo_search.corpora
           WHERE generation_id = gc_target) <> 1 THEN
        RAISE EXCEPTION 'rejected GC changed candidate children';
    END IF;
    BEGIN
        DELETE FROM repo_search.search_docs WHERE generation_id = gc_target;
        RAISE EXCEPTION 'direct READY document deletion accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'generation not BUILDING' THEN RAISE; END IF;
    END;
    BEGIN
        DELETE FROM repo_search.corpora WHERE generation_id = gc_target;
        RAISE EXCEPTION 'direct READY corpus deletion accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'generation not BUILDING' THEN RAISE; END IF;
    END;
    PERFORM repo_search.gc_inactive_generation(gc_target, repeat('0',64));
    IF EXISTS (SELECT 1 FROM repo_search.generations WHERE generation_id = gc_target)
       OR EXISTS (SELECT 1 FROM repo_search.corpora WHERE generation_id = gc_target)
       OR EXISTS (SELECT 1 FROM repo_search.search_docs WHERE generation_id = gc_target)
       OR (SELECT count(*) FROM repo_search.generations) <> 3
       OR (SELECT count(*) FROM repo_search.corpora) <> 3
       OR (SELECT count(*) FROM repo_search.search_docs) <> 3 THEN
        RAISE EXCEPTION 'inactive READY GC was not whole-generation atomic';
    END IF;
    IF jsonb_array_length(
        repo_search.search_generation(protected,'needle',10,'evidence')->'hits'
    ) <> 1 THEN
        RAISE EXCEPTION 'referenced inactive READY generation changed';
    END IF;
END
$materializer_gc$;
RESET ROLE;

DO $verify$
DECLARE
    output jsonb;
BEGIN
    IF (SELECT generation_id FROM repo_search.active_generation
        WHERE singleton_id=1) <> repeat('a',64)
       OR (SELECT count(*) FROM repo_search.generation_references) <> 1
       OR (SELECT state FROM repo_search.generations
           WHERE generation_id=repeat('b',64)) <> 'READY'
       OR (SELECT state FROM repo_search.generations
           WHERE generation_id=repeat('d',64)) <> 'BUILDING' THEN
        RAISE EXCEPTION 'GC damaged active, reference, or BUILDING generation';
    END IF;
    output := repo_search.search('needle',10,'evidence');
    IF output->>'generation_id' <> repeat('a',64)
       OR output->'hits'->0->>'candidate_id' <> 'candidate::a' THEN
        RAISE EXCEPTION 'active search changed after GC: %', output;
    END IF;
END
$verify$;
\echo PRODUCTION_GC_PASS
ROLLBACK;

DO $rollback$
BEGIN
    IF (SELECT count(*) FROM repo_search.generations) <> 0
       OR (SELECT count(*) FROM repo_search.corpora) <> 0
       OR (SELECT count(*) FROM repo_search.search_docs) <> 0
       OR (SELECT count(*) FROM repo_search.generation_references) <> 0
       OR (SELECT generation_id FROM repo_search.active_generation
           WHERE singleton_id = 1) IS NOT NULL THEN
        RAISE EXCEPTION 'production GC test rollback leaked state';
    END IF;
END
$rollback$;
\echo PRODUCTION_GC_ROLLBACK_PASS
