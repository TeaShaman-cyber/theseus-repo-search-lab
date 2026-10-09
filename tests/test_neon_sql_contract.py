"""Task 3 production query-plane source contract, distinct from synthetic probes.

The source checks run locally; the Heavy PostgreSQL runner is the execution gate.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sql/neon/001_query_plane_v1.sql"
PRIVILEGES = ROOT / "sql/neon/002_query_plane_privileges.sql"


class NeonQueryPlaneSchemaTests(unittest.TestCase):
    def test_production_schema_is_checked_in_and_versioned(self):
        self.assertTrue(SCHEMA.is_file(), "Task 3 production schema missing")
        sql = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("CREATE SCHEMA repo_search", sql)
        self.assertIn("repo-search-query-v1", sql)
        self.assertNotIn("CREATE EXTENSION", sql.upper())

    def test_generation_has_distinct_content_and_instance_identities(self):
        sql = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE repo_search.generations", sql)
        self.assertIn("generation_content_sha256", sql)
        self.assertIn("catalog_sha256", sql)
        self.assertIn("projection_schema_version", sql)
        self.assertIn("BUILDING", sql)
        self.assertIn("READY", sql)
        self.assertRegex(sql, r"generation_id\s+text\s+PRIMARY KEY")
        self.assertIn("^[0-9a-f]{64}$", sql)

    def test_generation_scoped_corpora_and_indexed_search_docs(self):
        sql = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE repo_search.corpora", sql)
        self.assertIn("CREATE TABLE repo_search.search_docs", sql)
        self.assertIn("REFERENCES repo_search.generations", sql)
        self.assertIn("GENERATED ALWAYS AS", sql)
        self.assertIn("to_tsvector('simple'", sql)
        self.assertRegex(sql, r"USING\s+GIN", "full-text index must be GIN")

    def test_child_guards_prevent_moves_and_serialize_ready(self):
        sql = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("NEW.generation_id IS DISTINCT FROM OLD.generation_id", sql)
        self.assertIn("FOR SHARE", sql)
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("BEFORE INSERT OR UPDATE OR DELETE", sql)
        self.assertIn("CREATE FUNCTION repo_search.mark_generation_ready", sql)
        self.assertIn("CREATE FUNCTION repo_search.activate_generation", sql)

    def test_stable_search_and_status_wrappers_are_versioned(self):
        sql = SCHEMA.read_text(encoding="utf-8")
        for name in (
            "search_generation_v1", "search_generation", "search", "generation_status",
        ):
            self.assertIn(f"CREATE FUNCTION repo_search.{name}(", sql)
        self.assertIn("repo-search-query-v1", sql)
        self.assertIn("unsupported projection schema version", sql)
        self.assertIn("unavailable_corpora", sql)
        self.assertIn("searched_corpora", sql)
        self.assertIn("'hits'", sql)
        self.assertIn("SECURITY DEFINER", sql)
        self.assertIn("SET search_path = pg_catalog, repo_search, pg_temp", sql)

    def test_guarded_ready_gc_and_generation_row_freeze(self):
        sql = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("CREATE FUNCTION repo_search.gc_inactive_generation(", sql)
        self.assertIn("CREATE FUNCTION repo_search.guard_generation_write()", sql)
        self.assertIn("CREATE TRIGGER generations_write_guard", sql)
        self.assertIn("generation_references", sql)
        self.assertIn("FOR UPDATE", sql)

    def test_reader_and_materializer_grants_are_separated(self):
        sql = PRIVILEGES.read_text(encoding="utf-8")
        self.assertIn("GRANT EXECUTE ON FUNCTION repo_search.search(", sql)
        self.assertIn("GRANT EXECUTE ON FUNCTION repo_search.search_generation(", sql)
        self.assertIn("GRANT EXECUTE ON FUNCTION repo_search.generation_status(", sql)
        self.assertIn("GRANT EXECUTE ON FUNCTION repo_search.gc_inactive_generation(", sql)
        self.assertNotIn("GRANT SELECT ON repo_search.search_docs TO repo_search_reader", sql)

    def test_materializer_parent_lock_uses_narrow_definer_helper(self):
        schema = SCHEMA.read_text(encoding="utf-8")
        privileges = PRIVILEGES.read_text(encoding="utf-8")
        self.assertIn("CREATE FUNCTION repo_search.lock_generation_state(", schema)
        self.assertIn("SECURITY DEFINER", schema)
        self.assertIn("parent_state := repo_search.lock_generation_state(target_generation_id)", schema)
        self.assertIn("FOR SHARE", schema)
        self.assertIn("ALTER FUNCTION repo_search.lock_generation_state(text) OWNER TO repo_search_owner", privileges)
        self.assertIn("GRANT EXECUTE ON FUNCTION repo_search.lock_generation_state(text)", privileges)
        self.assertNotIn("GRANT UPDATE ON repo_search.generations", privileges)

    def test_neon_migration_executor_has_explicit_noninherited_owner_set_right(self):
        sql = PRIVILEGES.read_text(encoding="utf-8")
        owner = sql.index("CREATE ROLE repo_search_owner NOLOGIN;")
        membership = sql.index(
            "GRANT repo_search_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;"
        )
        ownership = sql.index("ALTER SCHEMA repo_search OWNER TO repo_search_owner;")
        self.assertLess(owner, membership)
        self.assertLess(membership, ownership)

    def test_default_function_acl_configured_as_noninherited_schema_owner(self):
        sql = PRIVILEGES.read_text(encoding="utf-8")
        role_switch = sql.index("SET LOCAL ROLE repo_search_owner;")
        default_acl = sql.index("ALTER DEFAULT PRIVILEGES FOR ROLE repo_search_owner")
        restore = sql.index("RESET ROLE;")
        self.assertLess(role_switch, default_acl)
        self.assertLess(default_acl, restore)
        self.assertIn("REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC", sql)

    def test_all_capability_revokes_and_grants_run_as_real_owner(self):
        sql = PRIVILEGES.read_text(encoding="utf-8")
        switched = sql.index("SET LOCAL ROLE repo_search_owner;")
        revoke = sql.index("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA repo_search FROM PUBLIC")
        grant = sql.index("GRANT EXECUTE ON FUNCTION repo_search.generation_status()")
        reset = sql.index("RESET ROLE;")
        self.assertLess(switched, revoke)
        self.assertLess(revoke, grant)
        self.assertLess(grant, reset)

    def test_privilege_migration_and_roles_are_explicit(self):
        self.assertTrue(PRIVILEGES.is_file(), "Task 3 privilege migration missing")
        sql = PRIVILEGES.read_text(encoding="utf-8")
        for role in ("repo_search_owner", "repo_search_reader", "repo_search_materializer"):
            self.assertIn(role, sql)
        self.assertIn("NOLOGIN", sql)
        self.assertIn("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA repo_search FROM PUBLIC", sql)
        self.assertIn("ALTER DEFAULT PRIVILEGES FOR ROLE repo_search_owner", sql)
        self.assertNotRegex(sql, r"IN SCHEMA repo_search\s+REVOKE EXECUTE")


if __name__ == "__main__":
    unittest.main()
