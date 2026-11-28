\set ON_ERROR_STOP on
\echo PRODUCTION_READY_SEARCH_BEGIN
BEGIN;

-- Real repo_search functions, not the synthetic heavy_pg_concurrency schema.
INSERT INTO repo_search.generations (
    generation_id, generation_content_sha256, catalog_sha256
) VALUES (repeat('a',64), repeat('b',64), repeat('c',64));
INSERT INTO repo_search.corpora (
    generation_id, source_id, release_tag,
    artifact_sha256, source_descriptor_sha256, availability, unavailable_reason
) VALUES
    (repeat('a',64), 'ready-source', 'v1', repeat('d',64), repeat('e',64),
     'SEARCHABLE', NULL),
    (repeat('a',64), 'missing-source', 'v1', repeat('d',64), repeat('e',64),
     'UNAVAILABLE', 'source not published');
INSERT INTO repo_search.search_docs (
    generation_id, source_id, candidate_id,
    declaration_id, source_path, source_line, exact_identity, body_text
) VALUES
    (repeat('a',64), 'ready-source', 'ready-source::decl-1',
     'decl-1', 'Proofs/Example.lean', 42, 'Example.needle', 'a needle lemma');

DO $probe$
DECLARE
    target text := repeat('a',64);
    output jsonb;
BEGIN
    -- BUILDING cannot activate, even when fully populated.
    BEGIN
        PERFORM repo_search.activate_generation(target, NULL);
        RAISE EXCEPTION 'unready generation activated';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'candidate not READY' THEN RAISE; END IF;
    END;

    -- Completeness is checked before the one-way READY freeze.
    BEGIN
        PERFORM repo_search.mark_generation_ready(target, repeat('c',64), 3);
        RAISE EXCEPTION 'incomplete generation marked READY';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'corpus count mismatch' THEN RAISE; END IF;
    END;
    IF (SELECT state FROM repo_search.generations
        WHERE generation_id = target) <> 'BUILDING' THEN
        RAISE EXCEPTION 'failed publication changed state';
    END IF;

    PERFORM repo_search.mark_generation_ready(target, repeat('c',64), 2);
    IF (SELECT state FROM repo_search.generations
        WHERE generation_id = target) <> 'READY' THEN
        RAISE EXCEPTION 'generation did not become READY';
    END IF;
    PERFORM repo_search.activate_generation(target, NULL);

    output := repo_search.search('absentterm', 5, 'discovery');
    IF output->>'generation_id' <> target
       OR output->>'catalog_sha256' <> repeat('c',64)
       OR output->>'generation_content_sha256' <> repeat('b',64)
       OR output->>'projection_schema_version' <> 'repo-search-query-v1'
       OR output->>'searched_corpora' <> '1'
       OR jsonb_array_length(output->'unavailable_corpora') <> 1
       OR output->'unavailable_corpora'->0->>'source_id' <> 'missing-source'
       OR output->'hits' <> '[]'::jsonb THEN
        RAISE EXCEPTION 'zero-hit envelope mismatch: %', output;
    END IF;

    output := repo_search.search('needle', 5, 'evidence');
    IF jsonb_array_length(output->'hits') <> 1
       OR output->'hits'->0->>'candidate_id' <> 'ready-source::decl-1'
       OR output->'hits'->0->>'match_kind' <> 'lexical' THEN
        RAISE EXCEPTION 'lexical search mismatch: %', output;
    END IF;

    output := repo_search.search_generation(target, 'Example.needle', 5, 'discovery');
    IF jsonb_array_length(output->'hits') <> 1
       OR output->'hits'->0->>'match_kind' <> 'exact' THEN
        RAISE EXCEPTION 'exact candidate search mismatch: %', output;
    END IF;

    BEGIN
        UPDATE repo_search.corpora SET release_tag = 'changed'
        WHERE generation_id = target AND source_id = 'ready-source';
        RAISE EXCEPTION 'READY corpus update accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'generation not BUILDING' THEN RAISE; END IF;
    END;
    BEGIN
        DELETE FROM repo_search.search_docs WHERE generation_id = target;
        RAISE EXCEPTION 'READY child deletion accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'generation not BUILDING' THEN RAISE; END IF;
    END;
    BEGIN
        PERFORM repo_search.activate_generation(target, NULL);
        RAISE EXCEPTION 'stale activator accepted';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        IF SQLERRM <> 'stale activation' THEN RAISE; END IF;
    END;
    IF (SELECT generation_id FROM repo_search.active_generation
        WHERE singleton_id = 1) <> target THEN
        RAISE EXCEPTION 'active pointer changed after stale CAS';
    END IF;
END
$probe$;
\echo PRODUCTION_READY_SEARCH_PASS
ROLLBACK;

DO $rollback$
BEGIN
    IF (SELECT count(*) FROM repo_search.generations) <> 0
       OR (SELECT generation_id FROM repo_search.active_generation
           WHERE singleton_id = 1) IS NOT NULL
       OR (SELECT count(*) FROM repo_search.search_docs) <> 0 THEN
        RAISE EXCEPTION 'production fixture rollback left rows';
    END IF;
END
$rollback$;
\echo PRODUCTION_READY_SEARCH_ROLLBACK_PASS
