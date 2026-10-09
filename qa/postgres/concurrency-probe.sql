\set ON_ERROR_STOP on
\echo HEAVY_POSTGRES_CONCURRENCY_PROBE_BEGIN

DROP SCHEMA IF EXISTS heavy_pg_concurrency CASCADE;
CREATE SCHEMA heavy_pg_concurrency;

CREATE TABLE heavy_pg_concurrency.generations (
    generation_id text PRIMARY KEY,
    state text NOT NULL CHECK (state IN ('BUILDING', 'READY'))
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

INSERT INTO heavy_pg_concurrency.generations (generation_id, state)
VALUES
    ('writer-first', 'BUILDING'),
    ('ready-first', 'BUILDING'),
    ('cas-base', 'READY'),
    ('cas-winner', 'READY'),
    ('cas-stale', 'READY'),
    ('immutable-from', 'BUILDING'),
    ('immutable-to', 'BUILDING');

INSERT INTO heavy_pg_concurrency.active_slot
    (slot_id, active_generation_id) VALUES (1, 'cas-base');

INSERT INTO heavy_pg_concurrency.children
    (child_id, generation_id, payload)
VALUES ('immutable-child', 'immutable-from', 'original');

\echo HEAVY_POSTGRES_CONCURRENCY_PROBE_READY
