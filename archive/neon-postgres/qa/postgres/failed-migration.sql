\set ON_ERROR_STOP on
\echo HEAVY_POSTGRES_EXPECTED_MIGRATION_FAILURE_BEGIN
BEGIN;

-- Deliberately mutate previously working DDL and rows inside one transaction.
-- Neither the function change, row update, nor new relation may survive.
CREATE TABLE heavy_pg_concurrency.failed_migration_marker (
    id integer PRIMARY KEY
);
INSERT INTO heavy_pg_concurrency.failed_migration_marker (id) VALUES (1);

CREATE OR REPLACE FUNCTION heavy_pg_concurrency.search_generation_v2(
    target_generation_id text, query_term text
)
RETURNS text
LANGUAGE sql STABLE
AS $$
    SELECT 'corrupted-query-implementation'::text;
$$;

UPDATE heavy_pg_concurrency.generations
SET catalog_sha256 = 'broken-catalog'
WHERE generation_id = 'dispatch-v2';

-- Fail only after showing the transient mutations were applied.
DO $$
BEGIN
    IF to_regclass('heavy_pg_concurrency.failed_migration_marker') IS NULL
       OR (SELECT catalog_sha256 FROM heavy_pg_concurrency.generations
           WHERE generation_id = 'dispatch-v2') <> 'broken-catalog'
       OR heavy_pg_concurrency.search_generation_v2(
           'dispatch-v2', 'match') <> 'corrupted-query-implementation'
    THEN
        RAISE EXCEPTION 'migration failure fixture did not mutate state';
    END IF;
    RAISE EXCEPTION 'INTENTIONAL_MIGRATION_FAILURE' USING ERRCODE = 'P0001';
END
$$;

-- ON_ERROR_STOP prevents this COMMIT; psql connection exit rolls back.
COMMIT;
\echo ERROR_BAD_MIGRATION_COMMITTED
