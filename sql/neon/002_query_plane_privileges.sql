-- Task 3 capability security: dedicated object owner and NOLOGIN roles.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE repo_search_owner NOLOGIN;
CREATE ROLE repo_search_reader NOLOGIN;
CREATE ROLE repo_search_materializer NOLOGIN;

ALTER SCHEMA repo_search OWNER TO repo_search_owner;
ALTER TABLE repo_search.generations OWNER TO repo_search_owner;
ALTER TABLE repo_search.corpora OWNER TO repo_search_owner;
ALTER TABLE repo_search.search_docs OWNER TO repo_search_owner;
ALTER TABLE repo_search.active_generation OWNER TO repo_search_owner;
ALTER TABLE repo_search.generation_references OWNER TO repo_search_owner;

ALTER FUNCTION repo_search.guard_child_write() OWNER TO repo_search_owner;
ALTER FUNCTION repo_search.mark_generation_ready(text,text,integer) OWNER TO repo_search_owner;
ALTER FUNCTION repo_search.activate_generation(text,text) OWNER TO repo_search_owner;
ALTER FUNCTION repo_search.guard_generation_write() OWNER TO repo_search_owner;
ALTER FUNCTION repo_search.gc_inactive_generation(text,text) OWNER TO repo_search_owner;
ALTER FUNCTION repo_search.search_generation_v1(text,text,integer,text) OWNER TO repo_search_owner;
ALTER FUNCTION repo_search.search_generation(text,text,integer,text) OWNER TO repo_search_owner;
ALTER FUNCTION repo_search.search(text,integer,text) OWNER TO repo_search_owner;
ALTER FUNCTION repo_search.generation_status() OWNER TO repo_search_owner;

-- Global default revocation is required: per-schema revoke is insufficient.
ALTER DEFAULT PRIVILEGES FOR ROLE repo_search_owner
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA repo_search FROM PUBLIC;
REVOKE ALL ON SCHEMA repo_search FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA repo_search FROM PUBLIC;
GRANT USAGE ON SCHEMA repo_search TO repo_search_reader, repo_search_materializer;
GRANT SELECT, INSERT ON repo_search.generations TO repo_search_materializer;
GRANT SELECT, INSERT, UPDATE, DELETE ON repo_search.corpora TO repo_search_materializer;
GRANT SELECT, INSERT, UPDATE, DELETE ON repo_search.search_docs TO repo_search_materializer;
GRANT EXECUTE ON FUNCTION repo_search.mark_generation_ready(text,text,integer)
    TO repo_search_materializer;
GRANT EXECUTE ON FUNCTION repo_search.activate_generation(text,text)
    TO repo_search_materializer;

GRANT EXECUTE ON FUNCTION repo_search.gc_inactive_generation(text,text)
    TO repo_search_materializer;
GRANT EXECUTE ON FUNCTION repo_search.search_generation(text,text,integer,text)
    TO repo_search_reader, repo_search_materializer;
GRANT EXECUTE ON FUNCTION repo_search.search(text,integer,text)
    TO repo_search_reader;
GRANT EXECUTE ON FUNCTION repo_search.generation_status()
    TO repo_search_reader, repo_search_materializer;

-- No reader table access; only narrow stable wrappers may be granted.
COMMIT;
