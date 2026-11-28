import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class HeavyPostgresWorkflowTests(unittest.TestCase):
    def test_heavy_postgres_runner_contract_is_repo_local_and_ephemeral(self):
        workflow = ROOT / ".github" / "workflows" / "heavy-postgres.yml"
        endpoint = ROOT / "tools" / "ci" / "heavy-postgres"
        smoke = ROOT / "qa" / "postgres" / "runner-smoke.sql"

        self.assertTrue(workflow.is_file())
        self.assertTrue(endpoint.is_file())
        self.assertTrue(smoke.is_file())
        self.assertTrue(endpoint.stat().st_mode & 0o111)

        workflow_text = workflow.read_text(encoding="utf-8")
        trigger = workflow_text.split("permissions:", 1)[0]
        self.assertIn("pull_request:", trigger)
        self.assertIn("workflow_dispatch:", trigger)
        self.assertIn("postgres:17.11", workflow_text)
        self.assertIn("actions/cache/restore@", workflow_text)
        self.assertIn("actions/cache/save@", workflow_text)
        self.assertIn("docker load", workflow_text)
        self.assertIn("docker save", workflow_text)
        self.assertIn("public.ecr.aws/docker/library/postgres@sha256:", workflow_text)
        self.assertIn("pg_isready", workflow_text)
        self.assertIn("tools/ci/heavy-postgres", workflow_text)
        self.assertIn("postgresql-client", workflow_text)
        self.assertNotIn("services:", workflow_text)
        self.assertNotIn("docker.io", workflow_text)
        self.assertNotIn("NEON", workflow_text.upper())

        endpoint_text = endpoint.read_text(encoding="utf-8")
        self.assertIn("psql", endpoint_text)
        self.assertIn("ON_ERROR_STOP=1", endpoint_text)
        self.assertIn("qa/postgres/runner-smoke.sql", endpoint_text)
        self.assertIn("server_version_num", endpoint_text)
        self.assertIn("expected PostgreSQL 17", endpoint_text)
        self.assertIn("HEAVY_POSTGRES_PASS", endpoint_text)

        smoke_text = smoke.read_text(encoding="utf-8")
        self.assertIn("BEGIN;", smoke_text)
        self.assertIn("ROLLBACK;", smoke_text)
        self.assertIn("HEAVY_POSTGRES_SMOKE_PASS", smoke_text)

        security = ROOT / "qa" / "postgres" / "security-contract.sql"
        self.assertTrue(security.is_file())
        security_text = security.read_text(encoding="utf-8")
        self.assertIn("SECURITY DEFINER", security_text)
        self.assertIn(
            "SET search_path = pg_catalog, heavy_pg_contract, pg_temp",
            security_text,
        )
        self.assertIn("REVOKE ALL ON FUNCTION", security_text)
        self.assertIn("FROM PUBLIC", security_text)
        self.assertIn(
            "ALTER DEFAULT PRIVILEGES FOR ROLE heavy_pg_owner\n"
            "    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
            security_text,
        )
        self.assertNotIn(
            "IN SCHEMA heavy_pg_contract\n    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
            security_text,
        )
        self.assertIn("SET ROLE heavy_pg_reader", security_text)
        self.assertIn("SET ROLE heavy_pg_materializer", security_text)
        self.assertIn("CREATE TEMP TABLE secret", security_text)
        self.assertIn("HEAVY_POSTGRES_SECURITY_PASS", security_text)
        self.assertIn("qa/postgres/security-contract.sql", endpoint_text)

        concurrency_sql = ROOT / "qa" / "postgres" / "concurrency-probe.sql"
        concurrency_harness = ROOT / "qa" / "postgres" / "concurrency_harness.py"
        self.assertTrue(concurrency_sql.is_file())
        self.assertTrue(concurrency_harness.is_file())
        self.assertIn("qa/postgres/concurrency-probe.sql", endpoint_text)
        self.assertIn("qa/postgres/concurrency_harness.py", endpoint_text)

        probe_text = concurrency_sql.read_text(encoding="utf-8")
        self.assertIn("FOR SHARE", probe_text)
        self.assertIn("FOR UPDATE", probe_text)
        self.assertIn("BUILDING", probe_text)
        self.assertIn("READY", probe_text)

        harness_text = concurrency_harness.read_text(encoding="utf-8")
        self.assertIn("subprocess.Popen", harness_text)
        self.assertIn("PGAPPNAME", harness_text)
        self.assertIn("pg_stat_activity", harness_text)
        self.assertIn("wait_event_type", harness_text)
        self.assertIn("threading.Thread", harness_text)
        self.assertIn("READY_WRITE_SERIALIZATION_PASS", harness_text)
        self.assertIn("activate_if_current", probe_text)
        self.assertIn("CAS_STALE_ACTIVATOR_PASS", harness_text)
        self.assertIn("IMMUTABLE_CHILD_GENERATION_PASS", harness_text)
        self.assertIn("child generation_id is immutable", probe_text)
        self.assertIn("gc_inactive_generation", probe_text)
        self.assertIn("GC_INACTIVE_READY_PASS", harness_text)
        version_upgrade = ROOT / "qa" / "postgres" / "version-dispatch-v2.sql"
        self.assertTrue(version_upgrade.is_file())
        self.assertIn("projection_schema_version", probe_text)
        self.assertIn("search_generation_v1", probe_text)
        self.assertIn("search_generation_v2", version_upgrade.read_text(encoding="utf-8"))
        self.assertIn("VERSION_DISPATCH_V1_V2_PASS", harness_text)
        self.assertIn("CREATE FUNCTION heavy_pg_concurrency.search_envelope", probe_text)
        self.assertIn("WITH selected_generation AS MATERIALIZED", probe_text)
        self.assertIn("ZERO_HIT_ATOMIC_ENVELOPE_PASS", harness_text)
        self.assertIn("QUERY_INPUT_BOUNDS_PASS", harness_text)
        self.assertIn("normalized_term_count", probe_text)
        self.assertIn("result_limit integer DEFAULT", probe_text)
        broken_migration = ROOT / "qa" / "postgres" / "failed-migration.sql"
        self.assertTrue(broken_migration.is_file())
        self.assertIn("ON_ERROR_STOP", broken_migration.read_text(encoding="utf-8"))
        self.assertIn("FAILED_MIGRATION_ROLLBACK_PASS", harness_text)
