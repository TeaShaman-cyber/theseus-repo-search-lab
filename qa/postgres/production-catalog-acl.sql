\set ON_ERROR_STOP on
\echo PRODUCTION_CATALOG_ACL_BEGIN
BEGIN;
DO $catalog$
DECLARE
    owner_id oid := 'repo_search_owner'::regrole;
    proc record;
    table_row record;
    r text;
BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='repo_search') <> owner_id THEN
        RAISE EXCEPTION 'production schema owner mismatch';
    END IF;
    FOR table_row IN
        SELECT c.oid, c.relname, c.relowner FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='repo_search' AND c.relkind IN ('r','p','S')
    LOOP
        IF table_row.relowner <> owner_id THEN
            RAISE EXCEPTION 'production relation owner mismatch: %', table_row.relname;
        END IF;
        IF has_table_privilege('repo_search_reader', table_row.oid, 'SELECT')
           OR has_table_privilege('repo_search_reader',table_row.oid,'INSERT')
           OR has_table_privilege('repo_search_reader',table_row.oid,'UPDATE')
           OR has_table_privilege('repo_search_reader',table_row.oid,'DELETE') THEN
            RAISE EXCEPTION 'reader direct relation privilege: %',table_row.relname;
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='repo_search' AND c.relkind IN ('r','p')) <> 5 THEN
        RAISE EXCEPTION 'unexpected production table set';
    END IF;
    FOR r IN SELECT rolname FROM pg_roles
        WHERE rolname IN ('repo_search_owner','repo_search_reader','repo_search_materializer')
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles
                   WHERE rolname=r AND
                   (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls)) THEN
            RAISE EXCEPTION 'unsafe query-plane role: %',r;
        END IF;
    END LOOP;
    FOR proc IN
        SELECT p.oid, p.proname, p.proowner,p.prosecdef,p.proconfig
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='repo_search'
    LOOP
        IF proc.proowner <> owner_id OR NOT coalesce(
           proc.proconfig @> ARRAY['search_path=pg_catalog, repo_search, pg_temp']::text[],
           false) THEN
            RAISE EXCEPTION 'function owner/search_path mismatch: %',proc.proname;
        END IF;
        IF proc.proname IN (
            'search','search_generation','generation_status',
            'mark_generation_ready','activate_generation','gc_inactive_generation',
            'lock_generation_state'
        ) AND NOT proc.prosecdef THEN
            RAISE EXCEPTION 'missing SECURITY DEFINER: %',proc.proname;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_proc p
            CROSS JOIN LATERAL aclexplode(
                coalesce(p.proacl,acldefault('f',p.proowner))
            ) acl WHERE p.oid=proc.oid AND acl.grantee=0
              AND acl.privilege_type='EXECUTE'
        ) THEN
            RAISE EXCEPTION 'production function PUBLIC EXECUTE: %',proc.proname;
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='repo_search') < 10 THEN
        RAISE EXCEPTION 'unexpected production function count';
    END IF;
END
$catalog$;

DO $surface$
DECLARE
    proc record;
    reader_allowed text[] := ARRAY['search','search_generation','generation_status'];
    materializer_allowed text[] := ARRAY[
        'search_generation','generation_status','mark_generation_ready',
        'activate_generation','gc_inactive_generation','lock_generation_state'
    ];
BEGIN
    FOR proc IN SELECT p.oid,p.proname FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='repo_search'
    LOOP
        IF has_function_privilege('repo_search_reader',proc.oid,'EXECUTE')
           IS DISTINCT FROM (proc.proname=ANY(reader_allowed)) THEN
            RAISE EXCEPTION 'reader execute ACL mismatch: %',proc.proname;
        END IF;
        IF has_function_privilege('repo_search_materializer',proc.oid,'EXECUTE')
           IS DISTINCT FROM (proc.proname=ANY(materializer_allowed)) THEN
            RAISE EXCEPTION 'materializer execute ACL mismatch: %',proc.proname;
        END IF;
    END LOOP;
    IF NOT has_table_privilege('repo_search_materializer',
                               'repo_search.generations','SELECT')
       OR NOT has_table_privilege('repo_search_materializer',
                                 'repo_search.generations','INSERT')
       OR has_table_privilege('repo_search_materializer',
                              'repo_search.generations','UPDATE')
       OR has_table_privilege('repo_search_materializer',
                              'repo_search.generations','DELETE') THEN
        RAISE EXCEPTION 'materializer generations grant mismatch';
    END IF;
    IF NOT has_table_privilege('repo_search_materializer',
                               'repo_search.corpora','SELECT')
       OR NOT has_table_privilege('repo_search_materializer',
                                  'repo_search.corpora','INSERT')
       OR NOT has_table_privilege('repo_search_materializer',
                                  'repo_search.corpora','UPDATE')
       OR NOT has_table_privilege('repo_search_materializer',
                                  'repo_search.corpora','DELETE')
       OR NOT has_table_privilege('repo_search_materializer',
                                  'repo_search.search_docs','SELECT')
       OR NOT has_table_privilege('repo_search_materializer',
                                  'repo_search.search_docs','INSERT')
       OR NOT has_table_privilege('repo_search_materializer',
                                  'repo_search.search_docs','UPDATE')
       OR NOT has_table_privilege('repo_search_materializer',
                                  'repo_search.search_docs','DELETE') THEN
        RAISE EXCEPTION 'materializer child write grants mismatch';
    END IF;
    IF has_table_privilege('repo_search_materializer',
                           'repo_search.active_generation','SELECT')
       OR has_table_privilege('repo_search_materializer',
                              'repo_search.active_generation','UPDATE')
       OR has_table_privilege('repo_search_materializer',
                              'repo_search.generation_references','SELECT') THEN
        RAISE EXCEPTION 'materializer direct pointer/reference access';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_default_acl d
        WHERE d.defaclrole='repo_search_owner'::regrole
          AND d.defaclnamespace=0 AND d.defaclobjtype='f') THEN
        RAISE EXCEPTION 'missing role-global default function ACL';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_default_acl d
       CROSS JOIN LATERAL aclexplode(d.defaclacl) a
       WHERE d.defaclrole='repo_search_owner'::regrole
         AND d.defaclnamespace=0 AND d.defaclobjtype='f'
         AND a.grantee=0 AND a.privilege_type='EXECUTE') THEN
        RAISE EXCEPTION 'role-global default PUBLIC EXECUTE not revoked';
    END IF;
END
$surface$;

SET ROLE repo_search_owner;
CREATE FUNCTION repo_search.future_private_acl_probe()
RETURNS integer LANGUAGE sql
SET search_path = pg_catalog, repo_search, pg_temp
AS $$ SELECT 1 $$;
RESET ROLE;
DO $future_acl$
DECLARE
    helper_oid oid := 'repo_search.future_private_acl_probe()'::regprocedure;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc p
       CROSS JOIN LATERAL aclexplode(
           coalesce(p.proacl,acldefault('f',p.proowner))) a
       WHERE p.oid=helper_oid AND a.grantee=0
         AND a.privilege_type='EXECUTE') THEN
        RAISE EXCEPTION 'future function leaked PUBLIC EXECUTE';
    END IF;
    IF has_function_privilege('repo_search_reader',helper_oid,'EXECUTE')
       OR has_function_privilege('repo_search_materializer',helper_oid,'EXECUTE') THEN
        RAISE EXCEPTION 'future private helper leaked to capability role';
    END IF;
    IF (SELECT proowner FROM pg_proc WHERE oid=helper_oid)
       <> 'repo_search_owner'::regrole THEN
        RAISE EXCEPTION 'future function owner mismatch';
    END IF;
END
$future_acl$;
\echo PRODUCTION_CATALOG_ACL_PASS
ROLLBACK;

DO $rollback$
BEGIN
    IF to_regprocedure('repo_search.future_private_acl_probe()') IS NOT NULL THEN
        RAISE EXCEPTION 'temporary future ACL probe survived rollback';
    END IF;
END
$rollback$;
\echo PRODUCTION_CATALOG_ACL_ROLLBACK_PASS
