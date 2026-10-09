\set ON_ERROR_STOP on
\echo HEAVY_POSTGRES_SECURITY_BEGIN

BEGIN;

CREATE ROLE heavy_pg_owner
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE heavy_pg_reader
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE heavy_pg_materializer
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;

CREATE SCHEMA heavy_pg_contract AUTHORIZATION heavy_pg_owner;
REVOKE ALL ON SCHEMA heavy_pg_contract FROM PUBLIC;
GRANT USAGE ON SCHEMA heavy_pg_contract
    TO heavy_pg_reader, heavy_pg_materializer;

ALTER DEFAULT PRIVILEGES FOR ROLE heavy_pg_owner
    IN SCHEMA heavy_pg_contract
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

SET ROLE heavy_pg_owner;

CREATE TABLE heavy_pg_contract.secret (
    key text PRIMARY KEY,
    value text NOT NULL
);
INSERT INTO heavy_pg_contract.secret (key, value)
VALUES ('canonical', 'trusted');

CREATE FUNCTION heavy_pg_contract.read_secret()
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, heavy_pg_contract, pg_temp
AS $$
DECLARE
    result text;
BEGIN
    SELECT value
    INTO result
    FROM secret
    WHERE key = 'canonical';
    RETURN result;
END
$$;

CREATE FUNCTION heavy_pg_contract.write_secret(new_value text)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, heavy_pg_contract, pg_temp
AS $$
BEGIN
    UPDATE secret
    SET value = new_value
    WHERE key = 'canonical';
END
$$;

CREATE FUNCTION heavy_pg_contract.future_private()
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT 1
$$;

RESET ROLE;

REVOKE ALL ON FUNCTION heavy_pg_contract.read_secret() FROM PUBLIC;
REVOKE ALL ON FUNCTION heavy_pg_contract.write_secret(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION heavy_pg_contract.read_secret()
    TO heavy_pg_reader;
GRANT EXECUTE ON FUNCTION heavy_pg_contract.write_secret(text)
    TO heavy_pg_materializer;

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'heavy_pg_owner',
        'heavy_pg_reader',
        'heavy_pg_materializer'
    ]
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = role_name
              AND NOT rolcanlogin
              AND NOT rolsuper
              AND NOT rolcreatedb
              AND NOT rolcreaterole
              AND NOT rolreplication
              AND NOT rolbypassrls
        ) THEN
            RAISE EXCEPTION 'capability role contract failed for %', role_name;
        END IF;
    END LOOP;
END
$$;

DO $$
DECLARE
    proc_oid oid;
    proc_name text;
BEGIN
    FOREACH proc_name IN ARRAY ARRAY['read_secret()', 'write_secret(text)']
    LOOP
        proc_oid := to_regprocedure('heavy_pg_contract.' || proc_name);
        IF proc_oid IS NULL THEN
            RAISE EXCEPTION 'missing contract function %', proc_name;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_proc
            WHERE oid = proc_oid
              AND prosecdef
              AND proowner = 'heavy_pg_owner'::regrole
              AND proconfig @> ARRAY[
                  'search_path=pg_catalog, heavy_pg_contract, pg_temp'
              ]::text[]
        ) THEN
            RAISE EXCEPTION
                'SECURITY DEFINER/search_path/owner contract failed for %',
                proc_name;
        END IF;

        IF EXISTS (
            SELECT 1
            FROM pg_proc p
            CROSS JOIN LATERAL aclexplode(
                COALESCE(p.proacl, acldefault('f', p.proowner))
            ) acl
            WHERE p.oid = proc_oid
              AND acl.grantee = 0
              AND acl.privilege_type = 'EXECUTE'
        ) THEN
            RAISE EXCEPTION 'PUBLIC EXECUTE remains on %', proc_name;
        END IF;
    END LOOP;

    proc_oid := 'heavy_pg_contract.future_private()'::regprocedure;
    IF EXISTS (
        SELECT 1
        FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(
            COALESCE(p.proacl, acldefault('f', p.proowner))
        ) acl
        WHERE p.oid = proc_oid
          AND acl.grantee = 0
          AND acl.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION
            'default privileges granted PUBLIC EXECUTE to future helper';
    END IF;
END
$$;

DO $$
BEGIN
    IF has_table_privilege(
        'heavy_pg_reader',
        'heavy_pg_contract.secret',
        'SELECT'
    ) OR has_table_privilege(
        'heavy_pg_reader',
        'heavy_pg_contract.secret',
        'INSERT,UPDATE,DELETE'
    ) THEN
        RAISE EXCEPTION 'reader unexpectedly has direct table privileges';
    END IF;

    IF has_table_privilege(
        'heavy_pg_materializer',
        'heavy_pg_contract.secret',
        'SELECT'
    ) OR has_table_privilege(
        'heavy_pg_materializer',
        'heavy_pg_contract.secret',
        'INSERT,UPDATE,DELETE'
    ) THEN
        RAISE EXCEPTION
            'materializer unexpectedly has direct table privileges';
    END IF;

    IF NOT has_function_privilege(
        'heavy_pg_reader',
        'heavy_pg_contract.read_secret()',
        'EXECUTE'
    ) OR has_function_privilege(
        'heavy_pg_reader',
        'heavy_pg_contract.write_secret(text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'reader function capability boundary failed';
    END IF;

    IF NOT has_function_privilege(
        'heavy_pg_materializer',
        'heavy_pg_contract.write_secret(text)',
        'EXECUTE'
    ) OR has_function_privilege(
        'heavy_pg_materializer',
        'heavy_pg_contract.read_secret()',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'materializer function capability boundary failed';
    END IF;
END
$$;

SET ROLE heavy_pg_reader;

DO $$
BEGIN
    BEGIN
        PERFORM value
        FROM heavy_pg_contract.secret
        WHERE key = 'canonical';
        RAISE EXCEPTION 'reader direct SELECT unexpectedly succeeded';
    EXCEPTION
        WHEN insufficient_privilege THEN NULL;
    END;

    BEGIN
        PERFORM heavy_pg_contract.write_secret('reader-bad');
        RAISE EXCEPTION 'reader mutator execution unexpectedly succeeded';
    EXCEPTION
        WHEN insufficient_privilege THEN NULL;
    END;
END
$$;

CREATE TEMP TABLE secret (
    key text PRIMARY KEY,
    value text NOT NULL
);
INSERT INTO secret (key, value)
VALUES ('canonical', 'shadowed');

DO $$
BEGIN
    IF heavy_pg_contract.read_secret() <> 'trusted' THEN
        RAISE EXCEPTION
            'SECURITY DEFINER resolved an untrusted temp shadow';
    END IF;
END
$$;

DROP TABLE secret;
RESET ROLE;

SET ROLE heavy_pg_materializer;

DO $$
BEGIN
    BEGIN
        PERFORM value
        FROM heavy_pg_contract.secret
        WHERE key = 'canonical';
        RAISE EXCEPTION 'materializer direct SELECT unexpectedly succeeded';
    EXCEPTION
        WHEN insufficient_privilege THEN NULL;
    END;

    BEGIN
        PERFORM heavy_pg_contract.read_secret();
        RAISE EXCEPTION 'materializer reader execution unexpectedly succeeded';
    EXCEPTION
        WHEN insufficient_privilege THEN NULL;
    END;
END
$$;

SELECT heavy_pg_contract.write_secret('materializer-ok');
RESET ROLE;

DO $$
BEGIN
    IF (
        SELECT value
        FROM heavy_pg_contract.secret
        WHERE key = 'canonical'
    ) <> 'materializer-ok' THEN
        RAISE EXCEPTION 'guarded materializer mutation did not execute';
    END IF;
END
$$;

ROLLBACK;

DO $$
BEGIN
    IF to_regnamespace('heavy_pg_contract') IS NOT NULL THEN
        RAISE EXCEPTION 'security contract rollback left schema behind';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname IN (
            'heavy_pg_owner',
            'heavy_pg_reader',
            'heavy_pg_materializer'
        )
    ) THEN
        RAISE EXCEPTION 'security contract rollback left roles behind';
    END IF;
END
$$;

\echo HEAVY_POSTGRES_SECURITY_PASS
