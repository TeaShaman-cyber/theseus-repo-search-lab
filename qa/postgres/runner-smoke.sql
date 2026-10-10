\set ON_ERROR_STOP on
\echo HEAVY_POSTGRES_SMOKE_BEGIN

BEGIN;
CREATE SCHEMA heavy_postgres_runner_smoke;
CREATE TABLE heavy_postgres_runner_smoke.probe (
    id integer PRIMARY KEY,
    value text NOT NULL
);
INSERT INTO heavy_postgres_runner_smoke.probe (id, value)
VALUES (1, 'runner-ok');

DO $$
BEGIN
    IF (SELECT count(*) FROM heavy_postgres_runner_smoke.probe) <> 1 THEN
        RAISE EXCEPTION 'runner smoke row count mismatch';
    END IF;
END
$$;

ROLLBACK;

DO $$
BEGIN
    IF to_regclass('heavy_postgres_runner_smoke.probe') IS NOT NULL THEN
        RAISE EXCEPTION 'runner smoke rollback left relation behind';
    END IF;
END
$$;

\echo HEAVY_POSTGRES_SMOKE_PASS
