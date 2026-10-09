-- Task 3 production PostgreSQL 17 schema; no extensions.
\set ON_ERROR_STOP on
BEGIN;
CREATE SCHEMA repo_search;
CREATE TABLE repo_search.generations (
    generation_id text PRIMARY KEY CHECK (generation_id ~ '^[0-9a-f]{64}$'),
    generation_content_sha256 text NOT NULL CHECK (generation_content_sha256 ~ '^[0-9a-f]{64}$'),
    catalog_sha256 text NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'),
    projection_schema_version text NOT NULL DEFAULT 'repo-search-query-v1',
    state text NOT NULL DEFAULT 'BUILDING' CHECK (state IN ('BUILDING', 'READY')),
    ready_at timestamptz,
    CHECK ((state = 'BUILDING' AND ready_at IS NULL)
        OR (state = 'READY' AND ready_at IS NOT NULL))
);
CREATE TABLE repo_search.corpora (
    generation_id text NOT NULL REFERENCES repo_search.generations(generation_id),
    source_id text NOT NULL,
    release_tag text NOT NULL,
    artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    source_descriptor_sha256 text NOT NULL CHECK (source_descriptor_sha256 ~ '^[0-9a-f]{64}$'),
    availability text NOT NULL CHECK (availability IN ('SEARCHABLE','UNAVAILABLE')),
    unavailable_reason text,
    PRIMARY KEY (generation_id, source_id)
);
CREATE TABLE repo_search.search_docs (
    generation_id text NOT NULL,
    source_id text NOT NULL,
    candidate_id text NOT NULL,
    declaration_id text,
    source_row_id bigint,
    source_path text NOT NULL,
    source_line integer CHECK (source_line > 0),
    exact_identity text,
    body_text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', body_text)) STORED,
    PRIMARY KEY (generation_id, source_id, candidate_id),
    FOREIGN KEY (generation_id, source_id)
        REFERENCES repo_search.corpora(generation_id, source_id)
);
CREATE INDEX search_docs_vector_gin ON repo_search.search_docs USING GIN(search_vector);
CREATE TABLE repo_search.active_generation (
    singleton_id integer PRIMARY KEY CHECK (singleton_id = 1),
    generation_id text REFERENCES repo_search.generations(generation_id)
);
INSERT INTO repo_search.active_generation(singleton_id) VALUES (1);
CREATE TABLE repo_search.generation_references (
    reference_id text PRIMARY KEY,
    generation_id text NOT NULL REFERENCES repo_search.generations(generation_id)
);
-- Guard child writes against READY, and forbid reparenting even while BUILDING.
CREATE FUNCTION repo_search.guard_child_write()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
DECLARE
    target_generation_id text;
    parent_state text;
BEGIN
    IF TG_OP = 'UPDATE' AND
       NEW.generation_id IS DISTINCT FROM OLD.generation_id THEN
        RAISE EXCEPTION 'child generation_id is immutable' USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        target_generation_id := OLD.generation_id;
    ELSE
        target_generation_id := NEW.generation_id;
    END IF;
    SELECT state INTO parent_state FROM repo_search.generations
    WHERE generation_id = target_generation_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'generation does not exist' USING ERRCODE = '23503';
    END IF;
    IF parent_state <> 'BUILDING' AND NOT (TG_OP = 'DELETE' AND current_user = 'repo_search_owner') THEN
        RAISE EXCEPTION 'generation not BUILDING' USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER corpora_write_guard BEFORE INSERT OR UPDATE OR DELETE
    ON repo_search.corpora FOR EACH ROW EXECUTE FUNCTION repo_search.guard_child_write();
CREATE TRIGGER search_docs_write_guard BEFORE INSERT OR UPDATE OR DELETE
    ON repo_search.search_docs FOR EACH ROW EXECUTE FUNCTION repo_search.guard_child_write();
CREATE FUNCTION repo_search.mark_generation_ready(
    target_generation_id text, expected_catalog_sha256 text,
    expected_corpus_count integer
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
DECLARE
    parent_state text;
    parent_catalog text;
    actual_corpora bigint;
BEGIN
    SELECT state, catalog_sha256 INTO parent_state, parent_catalog
    FROM repo_search.generations WHERE generation_id = target_generation_id FOR UPDATE;
    IF NOT FOUND OR parent_state <> 'BUILDING'
       OR parent_catalog IS DISTINCT FROM expected_catalog_sha256 THEN
        RAISE EXCEPTION 'READY precondition mismatch' USING ERRCODE = '55000';
    END IF;
    SELECT count(*) INTO actual_corpora FROM repo_search.corpora
    WHERE generation_id = target_generation_id;
    IF expected_corpus_count IS NULL OR expected_corpus_count < 1
       OR actual_corpora <> expected_corpus_count THEN
        RAISE EXCEPTION 'corpus count mismatch' USING ERRCODE = '55000';
    END IF;
    UPDATE repo_search.generations SET state = 'READY', ready_at = CURRENT_TIMESTAMP
    WHERE generation_id = target_generation_id;
END
$$;

CREATE FUNCTION repo_search.activate_generation(
    target_generation_id text, expected_previous_generation_id text
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
DECLARE
    previous_generation_id text;
    candidate_state text;
BEGIN
    SELECT generation_id INTO previous_generation_id
    FROM repo_search.active_generation WHERE singleton_id = 1 FOR UPDATE;
    IF NOT FOUND OR previous_generation_id IS DISTINCT FROM expected_previous_generation_id THEN
        RAISE EXCEPTION 'stale activation' USING ERRCODE = '55000';
    END IF;
    SELECT state INTO candidate_state FROM repo_search.generations
    WHERE generation_id = target_generation_id FOR SHARE;
    IF NOT FOUND OR candidate_state <> 'READY' THEN
        RAISE EXCEPTION 'candidate not READY' USING ERRCODE = '55000';
    END IF;
    UPDATE repo_search.active_generation SET generation_id = target_generation_id
    WHERE singleton_id = 1;
END
$$;

-- Parent-row guard is independent of child guards and grant configuration.
CREATE FUNCTION repo_search.guard_generation_write()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'BUILDING' THEN
            RAISE EXCEPTION 'generation must start BUILDING' USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF OLD.state = 'READY' AND current_user <> 'repo_search_owner' THEN
            RAISE EXCEPTION 'READY generation cannot be deleted' USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.generation_id IS DISTINCT FROM OLD.generation_id
       OR NEW.generation_content_sha256 IS DISTINCT FROM OLD.generation_content_sha256
       OR NEW.catalog_sha256 IS DISTINCT FROM OLD.catalog_sha256
       OR NEW.projection_schema_version IS DISTINCT FROM OLD.projection_schema_version THEN
        RAISE EXCEPTION 'generation identity immutable' USING ERRCODE = '55000';
    END IF;
    IF OLD.state <> 'BUILDING' OR NEW.state <> 'READY'
       OR current_user <> 'repo_search_owner'
       OR NEW.ready_at IS NULL THEN
        RAISE EXCEPTION 'generation state immutable except guarded READY' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER generations_write_guard BEFORE INSERT OR UPDATE OR DELETE
    ON repo_search.generations FOR EACH ROW
    EXECUTE FUNCTION repo_search.guard_generation_write();

CREATE FUNCTION repo_search.gc_inactive_generation(
    target_generation_id text,
    expected_generation_content_sha256 text
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
DECLARE
    active_id text;
    actual_state text;
    actual_content text;
BEGIN
    SELECT generation_id INTO active_id
    FROM repo_search.active_generation WHERE singleton_id = 1 FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'active pointer missing' USING ERRCODE = '55000';
    END IF;
    IF active_id = target_generation_id THEN
        RAISE EXCEPTION 'cannot GC active generation' USING ERRCODE = '55000';
    END IF;
    SELECT state, generation_content_sha256 INTO actual_state, actual_content
    FROM repo_search.generations
    WHERE generation_id = target_generation_id FOR UPDATE;
    IF NOT FOUND OR actual_state <> 'READY' THEN
        RAISE EXCEPTION 'GC target not READY' USING ERRCODE = '55000';
    END IF;
    IF actual_content IS DISTINCT FROM expected_generation_content_sha256 THEN
        RAISE EXCEPTION 'GC content identity mismatch' USING ERRCODE = '55000';
    END IF;
    IF EXISTS (SELECT 1 FROM repo_search.generation_references
               WHERE generation_id = target_generation_id) THEN
        RAISE EXCEPTION 'GC target referenced' USING ERRCODE = '55000';
    END IF;
    DELETE FROM repo_search.search_docs WHERE generation_id = target_generation_id;
    DELETE FROM repo_search.corpora WHERE generation_id = target_generation_id;
    DELETE FROM repo_search.generations WHERE generation_id = target_generation_id;
END
$$;

-- Version-specific implementation. Never redefine v1 for a future v2 rollout.
CREATE FUNCTION repo_search.search_generation_v1(
    target_generation_id text, query_text text, result_limit integer, query_mode text
)
RETURNS jsonb LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
DECLARE
    terms text[];
    lexical_query tsquery;
    result_hits jsonb;
BEGIN
    IF query_text IS NULL OR btrim(query_text) = ''
       OR char_length(query_text) > 512 THEN
        RAISE EXCEPTION 'query text out of bounds' USING ERRCODE = '22023';
    END IF;
    SELECT array_agg(token ORDER BY token_ord) INTO terms
    FROM unnest(regexp_split_to_array(lower(btrim(query_text)), '[^[:alnum:]_]+'))
        WITH ORDINALITY AS t(token, token_ord)
    WHERE token <> '';
    IF terms IS NULL OR cardinality(terms) > 16 THEN
        RAISE EXCEPTION 'normalized term count out of bounds' USING ERRCODE = '22023';
    END IF;
    IF result_limit IS NULL OR result_limit < 1 OR result_limit > 100 THEN
        RAISE EXCEPTION 'result limit out of bounds' USING ERRCODE = '22023';
    END IF;
    IF query_mode IS NULL OR query_mode NOT IN ('discovery', 'evidence') THEN
        RAISE EXCEPTION 'unknown query mode' USING ERRCODE = '22023';
    END IF;
    IF query_mode = 'discovery' THEN
        lexical_query := to_tsquery('simple', array_to_string(terms, ' | '));
    ELSE
        lexical_query := plainto_tsquery('simple', array_to_string(terms, ' '));
    END IF;

    SELECT coalesce(jsonb_agg(
        jsonb_build_object(
            'source_id', h.source_id,
            'candidate_id', h.candidate_id,
            'declaration_id', h.declaration_id,
            'source_row_id', h.source_row_id,
            'source_path', h.source_path,
            'source_line', h.source_line,
            'match_kind', CASE WHEN h.is_exact THEN 'exact' ELSE 'lexical' END
        ) ORDER BY h.is_exact DESC, h.source_id, h.candidate_id
    ), '[]'::jsonb) INTO result_hits
    FROM (
        SELECT d.source_id, d.candidate_id, d.declaration_id,
               d.source_row_id, d.source_path, d.source_line,
               (d.exact_identity = query_text OR d.candidate_id = query_text)
                   AS is_exact
        FROM repo_search.search_docs AS d
        WHERE d.generation_id = target_generation_id
          AND (d.exact_identity = query_text OR d.candidate_id = query_text
               OR d.search_vector @@ lexical_query)
        ORDER BY is_exact DESC NULLS LAST, d.source_id, d.candidate_id
        LIMIT result_limit
    ) AS h;
    RETURN result_hits;
END
$$;

-- STABLE functions use one calling-statement snapshot for content and metadata.
CREATE FUNCTION repo_search.search_generation(
    target_generation_id text, query_text text, result_limit integer, query_mode text
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
DECLARE
    generation_record repo_search.generations%ROWTYPE;
    searched bigint;
    unavailable jsonb;
BEGIN
    SELECT * INTO generation_record FROM repo_search.generations
    WHERE generation_id = target_generation_id;
    IF NOT FOUND OR generation_record.state <> 'READY' THEN
        RAISE EXCEPTION 'search generation not READY' USING ERRCODE = '55000';
    END IF;
    IF generation_record.projection_schema_version <> 'repo-search-query-v1' THEN
        RAISE EXCEPTION 'unsupported projection schema version' USING ERRCODE = '55000';
    END IF;
    SELECT count(*) FILTER (WHERE availability = 'SEARCHABLE'),
           coalesce(jsonb_agg(
               jsonb_build_object('source_id', source_id, 'reason', unavailable_reason)
               ORDER BY source_id
           ) FILTER (WHERE availability = 'UNAVAILABLE'), '[]'::jsonb)
    INTO searched, unavailable
    FROM repo_search.corpora WHERE generation_id = target_generation_id;
    RETURN jsonb_build_object(
        'generation_id', generation_record.generation_id,
        'catalog_sha256', generation_record.catalog_sha256,
        'generation_content_sha256', generation_record.generation_content_sha256,
        'projection_schema_version', generation_record.projection_schema_version,
        'searched_corpora', searched,
        'unavailable_corpora', unavailable,
        'hits', repo_search.search_generation_v1(
            target_generation_id, query_text, result_limit, query_mode)
    );
END
$$;

CREATE FUNCTION repo_search.search(
    query_text text, result_limit integer, query_mode text
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
DECLARE
    target_generation_id text;
BEGIN
    SELECT generation_id INTO target_generation_id FROM repo_search.active_generation
    WHERE singleton_id = 1;
    IF target_generation_id IS NULL THEN
        RAISE EXCEPTION 'active generation missing' USING ERRCODE = '55000';
    END IF;
    RETURN repo_search.search_generation(
        target_generation_id, query_text, result_limit, query_mode);
END
$$;

CREATE FUNCTION repo_search.generation_status()
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, repo_search, pg_temp
AS $$
    SELECT jsonb_build_object(
        'generation_id', a.generation_id,
        'state', g.state,
        'catalog_sha256', g.catalog_sha256,
        'generation_content_sha256', g.generation_content_sha256,
        'projection_schema_version', g.projection_schema_version
    )
    FROM repo_search.active_generation a
    LEFT JOIN repo_search.generations g ON g.generation_id = a.generation_id
    WHERE a.singleton_id = 1;
$$;
COMMIT;
