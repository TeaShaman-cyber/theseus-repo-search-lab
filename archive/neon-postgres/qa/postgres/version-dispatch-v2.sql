\set ON_ERROR_STOP on
\echo HEAVY_POSTGRES_VERSION_V2_MIGRATION_BEGIN
BEGIN;

-- Add the new implementation; leave search_generation_v1 unchanged.
CREATE FUNCTION heavy_pg_concurrency.search_generation_v2(
    target_generation_id text, query_term text
)
RETURNS text
LANGUAGE sql STABLE
AS $$
    SELECT coalesce(string_agg(child_id, ',' ORDER BY child_id DESC), '')
    FROM heavy_pg_concurrency.children
    WHERE generation_id = target_generation_id
      AND position(query_term IN payload) > 0;
$$;

-- Replacing the dispatcher must not modify v1 semantics.
CREATE OR REPLACE FUNCTION heavy_pg_concurrency.search_generation(
    target_generation_id text, query_term text
)
RETURNS text
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    generation_state text;
    generation_version text;
BEGIN
    SELECT state, projection_schema_version
    INTO generation_state, generation_version
    FROM heavy_pg_concurrency.generations
    WHERE generation_id = target_generation_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'search generation missing';
    END IF;
    IF generation_state <> 'READY' THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'search generation not READY';
    END IF;
    IF generation_version = 'repo-search-query-v1' THEN
        RETURN heavy_pg_concurrency.search_generation_v1(
            target_generation_id, query_term);
    ELSIF generation_version = 'repo-search-query-v2' THEN
        RETURN heavy_pg_concurrency.search_generation_v2(
            target_generation_id, query_term);
    END IF;
    RAISE EXCEPTION USING ERRCODE = '55000',
        MESSAGE = 'unsupported projection schema version';
END
$$;
COMMIT;
\echo HEAVY_POSTGRES_VERSION_V2_MIGRATION_PASS
