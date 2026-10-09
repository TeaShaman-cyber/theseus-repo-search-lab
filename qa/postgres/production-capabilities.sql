\set ON_ERROR_STOP on
\echo PRODUCTION_CAPABILITIES_BEGIN
BEGIN;

-- Real owner-controlled production data for effective-role checks.
INSERT INTO repo_search.generations
    (generation_id, generation_content_sha256, catalog_sha256)
VALUES (repeat('1',64), repeat('2',64), repeat('3',64));
INSERT INTO repo_search.corpora
    (generation_id, source_id, release_tag, artifact_sha256,
     source_descriptor_sha256, availability)
VALUES (repeat('1',64), 'reader-source', 'v1', repeat('4',64),
        repeat('5',64), 'SEARCHABLE');
INSERT INTO repo_search.search_docs
    (generation_id, source_id, candidate_id, source_path, source_line, body_text)
VALUES (repeat('1',64), 'reader-source', 'reader-source::needle',
        'Proofs/Role.lean', 8, 'needle proof');
SELECT repo_search.mark_generation_ready(repeat('1',64), repeat('3',64), 1);
SELECT repo_search.activate_generation(repeat('1',64), NULL);

SET ROLE repo_search_reader;
DO $reader$
DECLARE
    found jsonb;
BEGIN
    IF current_user <> 'repo_search_reader' THEN
        RAISE EXCEPTION 'reader effective role mismatch';
    END IF;
    found := repo_search.search('needle', 3, 'evidence');
    IF jsonb_array_length(found->'hits') <> 1
       OR found->>'generation_id' <> repeat('1',64) THEN
        RAISE EXCEPTION 'reader search did not work';
    END IF;
    found := repo_search.search_generation(repeat('1',64), 'missing', 3, 'discovery');
    IF found->'hits' <> '[]'::jsonb
       OR found->>'catalog_sha256' <> repeat('3',64) THEN
        RAISE EXCEPTION 'reader zero-hit envelope unavailable';
    END IF;
    found := repo_search.generation_status();
    IF found->>'generation_id' <> repeat('1',64) THEN
        RAISE EXCEPTION 'reader status unavailable';
    END IF;

    BEGIN
        PERFORM 1 FROM repo_search.generations LIMIT 1;
        RAISE EXCEPTION 'reader table access unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM repo_search.corpora LIMIT 1;
        RAISE EXCEPTION 'reader corpus access unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM repo_search.search_docs LIMIT 1;
        RAISE EXCEPTION 'reader search-docs access unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM repo_search.active_generation LIMIT 1;
        RAISE EXCEPTION 'reader active pointer access unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM repo_search.search_generation_v1(repeat('1',64), 'needle', 3, 'evidence');
        RAISE EXCEPTION 'reader private version helper unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM repo_search.mark_generation_ready(repeat('1',64), repeat('3',64), 1);
        RAISE EXCEPTION 'reader READY mutator unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM repo_search.activate_generation(repeat('1',64), repeat('1',64));
        RAISE EXCEPTION 'reader CAS mutator unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM repo_search.gc_inactive_generation(repeat('1',64), repeat('2',64));
        RAISE EXCEPTION 'reader GC mutator unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
END
$reader$;
RESET ROLE;

SET ROLE repo_search_materializer;
DO $materializer$
DECLARE
    found jsonb;
BEGIN
    IF current_user <> 'repo_search_materializer' THEN
        RAISE EXCEPTION 'materializer effective role mismatch';
    END IF;
    found := repo_search.generation_status();
    IF found->>'generation_id' <> repeat('1',64) THEN
        RAISE EXCEPTION 'materializer status unavailable';
    END IF;
    found := repo_search.search_generation(repeat('1',64), 'needle', 3, 'evidence');
    IF jsonb_array_length(found->'hits') <> 1 THEN
        RAISE EXCEPTION 'materializer candidate search unavailable';
    END IF;

    BEGIN
        PERFORM repo_search.search('needle', 3, 'evidence');
        RAISE EXCEPTION 'materializer reader-only search unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM repo_search.search_generation_v1(repeat('1',64), 'needle', 3, 'evidence');
        RAISE EXCEPTION 'materializer private helper unexpectedly allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM repo_search.active_generation LIMIT 1;
        RAISE EXCEPTION 'materializer direct active pointer read allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;
    BEGIN
        UPDATE repo_search.generations SET state='READY'
        WHERE generation_id = repeat('1',64);
        RAISE EXCEPTION 'materializer direct generation update allowed';
    EXCEPTION WHEN SQLSTATE '42501' THEN NULL;
    END;

    INSERT INTO repo_search.generations
        (generation_id, generation_content_sha256, catalog_sha256)
    VALUES (repeat('6',64), repeat('7',64), repeat('8',64));
    INSERT INTO repo_search.corpora
        (generation_id, source_id, release_tag, artifact_sha256,
         source_descriptor_sha256, availability)
    VALUES (repeat('6',64), 'candidate-source', 'v1', repeat('9',64),
            repeat('a',64), 'SEARCHABLE');
    INSERT INTO repo_search.search_docs
        (generation_id, source_id, candidate_id, source_path, source_line, body_text)
    VALUES (repeat('6',64), 'candidate-source', 'candidate::needle',
            'Proofs/Candidate.lean', 9, 'needle witness');
    PERFORM repo_search.mark_generation_ready(repeat('6',64), repeat('8',64), 1);
    found := repo_search.search_generation(repeat('6',64), 'needle', 3, 'evidence');
    IF jsonb_array_length(found->'hits') <> 1 THEN
        RAISE EXCEPTION 'materializer READY candidate search failed';
    END IF;
    PERFORM repo_search.activate_generation(repeat('6',64), repeat('1',64));
END
$materializer$;
RESET ROLE;

-- After switching, the same reader capability sees the new READY generation.
SET ROLE repo_search_reader;
DO $reader_after$
DECLARE
    found jsonb;
BEGIN
    found := repo_search.search('needle', 3, 'evidence');
    IF found->>'generation_id' <> repeat('6',64)
       OR found->'hits'->0->>'candidate_id' <> 'candidate::needle' THEN
        RAISE EXCEPTION 'reader did not follow guarded publication';
    END IF;
END
$reader_after$;
RESET ROLE;
\echo PRODUCTION_CAPABILITIES_PASS
ROLLBACK;

DO $rollback$
BEGIN
    IF (SELECT count(*) FROM repo_search.generations) <> 0
       OR (SELECT count(*) FROM repo_search.corpora) <> 0
       OR (SELECT count(*) FROM repo_search.search_docs) <> 0
       OR (SELECT generation_id FROM repo_search.active_generation
           WHERE singleton_id = 1) IS NOT NULL THEN
        RAISE EXCEPTION 'capability fixture rollback leaked data';
    END IF;
END
$rollback$;
\echo PRODUCTION_CAPABILITIES_ROLLBACK_PASS
