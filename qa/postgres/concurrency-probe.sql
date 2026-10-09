\set ON_ERROR_STOP on
\echo HEAVY_POSTGRES_CONCURRENCY_PROBE_BEGIN

DROP SCHEMA IF EXISTS heavy_pg_concurrency CASCADE;
DROP ROLE IF EXISTS heavy_pg_gc_owner;
CREATE SCHEMA heavy_pg_concurrency;
CREATE ROLE heavy_pg_gc_owner NOLOGIN;

CREATE TABLE heavy_pg_concurrency.generations (
    generation_id text PRIMARY KEY,
    state text NOT NULL CHECK (state IN ('BUILDING', 'READY')),
    content_identity text NOT NULL DEFAULT 'fixture-digest',
    projection_schema_version text NOT NULL DEFAULT 'repo-search-query-v1'
);

CREATE TABLE heavy_pg_concurrency.children (
    child_id text PRIMARY KEY,
    generation_id text NOT NULL
        REFERENCES heavy_pg_concurrency.generations(generation_id),
    payload text NOT NULL
);

CREATE FUNCTION heavy_pg_concurrency.guard_child_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    target_generation_id text;
    parent_state text;
BEGIN
    -- Only the sealed GC definer may remove READY children as a whole generation.
    IF TG_OP = 'DELETE' AND current_user = 'heavy_pg_gc_owner' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.generation_id IS DISTINCT FROM OLD.generation_id THEN
            RAISE EXCEPTION
                USING ERRCODE = '55000',
                      MESSAGE = 'child generation_id is immutable';
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        target_generation_id := OLD.generation_id;
    ELSE
        target_generation_id := NEW.generation_id;
    END IF;

    SELECT state
    INTO parent_state
    FROM heavy_pg_concurrency.generations
    WHERE generation_id = target_generation_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            USING ERRCODE = '23503',
                  MESSAGE = format(
                      'generation %s does not exist',
                      target_generation_id
                  );
    END IF;

    IF parent_state <> 'BUILDING' THEN
        RAISE EXCEPTION
            USING ERRCODE = '55000',
                  MESSAGE = format(
                      'generation %s is not BUILDING',
                      target_generation_id
                  );
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER children_write_guard
BEFORE INSERT OR UPDATE OR DELETE
ON heavy_pg_concurrency.children
FOR EACH ROW
EXECUTE FUNCTION heavy_pg_concurrency.guard_child_write();

CREATE FUNCTION heavy_pg_concurrency.mark_ready(target_generation_id text)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    parent_state text;
BEGIN
    SELECT state
    INTO parent_state
    FROM heavy_pg_concurrency.generations
    WHERE generation_id = target_generation_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            USING ERRCODE = 'P0002',
                  MESSAGE = format(
                      'generation %s does not exist',
                      target_generation_id
                  );
    END IF;

    IF parent_state <> 'BUILDING' THEN
        RAISE EXCEPTION
            USING ERRCODE = '55000',
                  MESSAGE = format(
                      'generation %s is not BUILDING',
                      target_generation_id
                  );
    END IF;

    UPDATE heavy_pg_concurrency.generations
    SET state = 'READY'
    WHERE generation_id = target_generation_id;
END
$$;

CREATE TABLE heavy_pg_concurrency.active_slot (
    slot_id integer PRIMARY KEY CHECK (slot_id = 1),
    active_generation_id text NOT NULL
        REFERENCES heavy_pg_concurrency.generations(generation_id)
);

CREATE TABLE heavy_pg_concurrency.external_references (
    reference_id text PRIMARY KEY,
    generation_id text NOT NULL
        REFERENCES heavy_pg_concurrency.generations(generation_id)
);

CREATE FUNCTION heavy_pg_concurrency.activate_if_current(
    expected_generation_id text,
    candidate_generation_id text
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    changed integer;
BEGIN
    PERFORM 1 FROM heavy_pg_concurrency.generations
    WHERE generation_id = candidate_generation_id AND state = 'READY';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            USING ERRCODE = '55000', MESSAGE = 'candidate is not READY';
    END IF;

    UPDATE heavy_pg_concurrency.active_slot
    SET active_generation_id = candidate_generation_id
    WHERE slot_id = 1 AND active_generation_id = expected_generation_id;
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END
$$;

-- Privileged deletion escape is reachable only through the dedicated
-- NOLOGIN SECURITY DEFINER owner; ordinary child DELETE still checks READY.
GRANT USAGE ON SCHEMA heavy_pg_concurrency TO heavy_pg_gc_owner;
GRANT SELECT, UPDATE ON heavy_pg_concurrency.active_slot TO heavy_pg_gc_owner;
GRANT SELECT, UPDATE, DELETE ON heavy_pg_concurrency.generations TO heavy_pg_gc_owner;
GRANT SELECT, DELETE ON heavy_pg_concurrency.children TO heavy_pg_gc_owner;
GRANT SELECT ON heavy_pg_concurrency.external_references TO heavy_pg_gc_owner;

CREATE FUNCTION heavy_pg_concurrency.gc_inactive_generation(
    target_generation_id text,
    expected_content_identity text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, heavy_pg_concurrency, pg_temp
AS $$
DECLARE
    current_active text;
    target_state text;
    actual_content_identity text;
BEGIN
    SELECT active_generation_id INTO current_active
    FROM heavy_pg_concurrency.active_slot
    WHERE slot_id = 1 FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'active slot missing';
    END IF;
    IF current_active = target_generation_id THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'cannot GC active generation';
    END IF;

    SELECT state, content_identity INTO target_state, actual_content_identity
    FROM heavy_pg_concurrency.generations
    WHERE generation_id = target_generation_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'GC target missing';
    END IF;
    IF target_state <> 'READY' THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'GC target not READY';
    END IF;
    IF actual_content_identity IS DISTINCT FROM expected_content_identity THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'GC identity mismatch';
    END IF;
    IF EXISTS (
        SELECT 1 FROM heavy_pg_concurrency.external_references
        WHERE generation_id = target_generation_id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'GC target referenced';
    END IF;

    DELETE FROM heavy_pg_concurrency.children
    WHERE generation_id = target_generation_id;
    DELETE FROM heavy_pg_concurrency.generations
    WHERE generation_id = target_generation_id;
    RETURN true;
END
$$;
ALTER FUNCTION heavy_pg_concurrency.gc_inactive_generation(text, text)
    OWNER TO heavy_pg_gc_owner;
REVOKE ALL ON FUNCTION heavy_pg_concurrency.gc_inactive_generation(text, text)
    FROM PUBLIC;

-- Synthetic v1 semantics intentionally sort lexical matches ascending.
-- Later v2 will change this order without replacing this implementation.
CREATE FUNCTION heavy_pg_concurrency.search_generation_v1(
    target_generation_id text, query_term text
)
RETURNS text
LANGUAGE sql STABLE
AS $$
    SELECT coalesce(string_agg(child_id, ',' ORDER BY child_id ASC), '')
    FROM heavy_pg_concurrency.children
    WHERE generation_id = target_generation_id
      AND position(query_term IN payload) > 0;
$$;

CREATE FUNCTION heavy_pg_concurrency.search_generation(
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
    END IF;
    RAISE EXCEPTION USING ERRCODE = '55000',
        MESSAGE = 'unsupported projection schema version';
END
$$;

CREATE FUNCTION heavy_pg_concurrency.search_active(query_term text)
RETURNS text
LANGUAGE sql STABLE
AS $$
    SELECT heavy_pg_concurrency.search_generation(
        active_generation_id, query_term)
    FROM heavy_pg_concurrency.active_slot WHERE slot_id = 1;
$$;

INSERT INTO heavy_pg_concurrency.generations (generation_id, state)
VALUES
    ('writer-first', 'BUILDING'),
    ('ready-first', 'BUILDING'),
    ('cas-base', 'READY'),
    ('cas-winner', 'READY'),
    ('cas-stale', 'READY'),
    ('immutable-from', 'BUILDING'),
    ('immutable-to', 'BUILDING'),
    ('gc-disposable', 'BUILDING'),
    ('gc-referenced', 'READY'),
    ('gc-building', 'BUILDING'),
    ('dispatch-v1', 'BUILDING'),
    ('dispatch-v2', 'BUILDING'),
    ('dispatch-unknown', 'READY');

UPDATE heavy_pg_concurrency.generations
SET projection_schema_version = 'repo-search-query-v2'
WHERE generation_id = 'dispatch-v2';
UPDATE heavy_pg_concurrency.generations
SET projection_schema_version = 'unknown-query-version'
WHERE generation_id = 'dispatch-unknown';

UPDATE heavy_pg_concurrency.generations
SET content_identity = 'gc-digest-1'
WHERE generation_id = 'gc-disposable';
UPDATE heavy_pg_concurrency.generations
SET content_identity = 'gc-digest-2'
WHERE generation_id = 'gc-referenced';

INSERT INTO heavy_pg_concurrency.active_slot
    (slot_id, active_generation_id) VALUES (1, 'cas-base');

INSERT INTO heavy_pg_concurrency.children
    (child_id, generation_id, payload)
VALUES ('immutable-child', 'immutable-from', 'original');

INSERT INTO heavy_pg_concurrency.children
    (child_id, generation_id, payload)
VALUES ('gc-child-1', 'gc-disposable', 'first'),
       ('gc-child-2', 'gc-disposable', 'second');
SELECT heavy_pg_concurrency.mark_ready('gc-disposable');

INSERT INTO heavy_pg_concurrency.external_references
    (reference_id, generation_id)
VALUES ('pinned', 'gc-referenced');

INSERT INTO heavy_pg_concurrency.children
    (child_id, generation_id, payload)
VALUES ('v1-a', 'dispatch-v1', 'lexical match'),
       ('v1-b', 'dispatch-v1', 'lexical match'),
       ('v2-a', 'dispatch-v2', 'lexical match'),
       ('v2-b', 'dispatch-v2', 'lexical match');
SELECT heavy_pg_concurrency.mark_ready('dispatch-v1');
SELECT heavy_pg_concurrency.mark_ready('dispatch-v2');

\echo HEAVY_POSTGRES_CONCURRENCY_PROBE_READY
