import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/ci/postgres-production"


class ProductionPostgresGateTests(unittest.TestCase):
    def run_gate(self, files=(), readback="t"):
        with tempfile.TemporaryDirectory() as work:
            workspace = Path(work)
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            for filename in files:
                path = workspace / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("SELECT 1;\n", encoding="utf-8")
            fakebin = workspace / "bin"
            fakebin.mkdir()
            fake = fakebin / "psql"
            fake.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
                'case "$*" in *-Atqc*) printf "%s\\n" "$FAKE_READBACK";; esac\n',
                encoding="utf-8",
            )
            fake.chmod(0o755)
            fakepy = fakebin / "python3"
            fakepy.write_text(
                "#!/bin/sh\n"
                'printf "PY3 %s\\n" "$*" >> "$CALL_LOG"\n',
                encoding="utf-8",
            )
            fakepy.chmod(0o755)
            call_log = workspace / "psql-calls"
            env = {**os.environ, "PATH": str(fakebin) + ":" + os.environ["PATH"],
                   "CALL_LOG": str(call_log), "FAKE_READBACK": readback}
            result = subprocess.run(
                ["sh", str(GATE)], cwd=workspace, env=env,
                capture_output=True, text=True, check=False,
            )
            calls = []
            if call_log.exists():
                for line in call_log.read_text(encoding="utf-8").splitlines():
                    if line.startswith(("-X -v ", "PY3 ")):
                        calls.append(line)
                    elif calls:
                        calls[-1] += " " + line
            return result, calls

    def test_missing_production_migrations_explicitly_pending(self):
        result, calls = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PRODUCTION_POSTGRES_PENDING", result.stdout)
        self.assertEqual(calls, [])

    def test_partial_migration_set_fails_closed(self):
        for filename in (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
        ):
            with self.subTest(filename=filename):
                result, calls = self.run_gate((filename,))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("PRODUCTION_POSTGRES_INCOMPLETE", result.stderr)
                self.assertEqual(calls, [])

    def test_complete_migrations_without_behavior_fixture_fail_closed(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
        )
        result, calls = self.run_gate(files)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRODUCTION_POSTGRES_INCOMPLETE", result.stderr)
        self.assertEqual(calls, [])

    def test_missing_role_probe_fails_closed_before_sql_apply(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
            "qa/postgres/production-ready-search.sql",
        )
        result, calls = self.run_gate(files)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRODUCTION_POSTGRES_INCOMPLETE", result.stderr)
        self.assertEqual(calls, [])

    def test_missing_gc_probe_fails_closed_before_sql_apply(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
            "qa/postgres/production-ready-search.sql",
            "qa/postgres/production-capabilities.sql",
        )
        result, calls = self.run_gate(files)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRODUCTION_POSTGRES_INCOMPLETE", result.stderr)
        self.assertEqual(calls, [])

    def test_missing_concurrency_harness_fails_closed(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
            "qa/postgres/production-ready-search.sql",
            "qa/postgres/production-capabilities.sql",
            "qa/postgres/production-gc.sql",
        )
        result, calls = self.run_gate(files)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRODUCTION_POSTGRES_INCOMPLETE", result.stderr)
        self.assertEqual(calls, [])

    def test_missing_owner_acl_probe_fails_before_migration(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
            "qa/postgres/production-ready-search.sql",
            "qa/postgres/production-capabilities.sql",
            "qa/postgres/production-gc.sql",
            "qa/postgres/production_concurrency_harness.py",
        )
        result, calls = self.run_gate(files)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRODUCTION_POSTGRES_INCOMPLETE", result.stderr)
        self.assertEqual(calls, [])

    def test_complete_migration_set_applied_and_read_back(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
            "qa/postgres/production-ready-search.sql",
            "qa/postgres/production-capabilities.sql",
            "qa/postgres/production-gc.sql",
            "qa/postgres/production_concurrency_harness.py",
            "qa/postgres/production-catalog-acl.sql",
        )
        result, calls = self.run_gate(files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PRODUCTION_POSTGRES_SCHEMA_PASS", result.stdout)
        self.assertEqual(len(calls), 8, calls)
        self.assertIn(files[6], calls[6])
        self.assertIn(files[5], calls[7])
        self.assertIn(files[4], calls[5])
        self.assertIn(files[2], calls[3])
        self.assertIn(files[3], calls[4])
        self.assertIn(files[0], calls[0])
        self.assertIn(files[1], calls[1])
        self.assertIn("to_regclass", calls[2])

    def test_bad_catalog_readback_fails_closed(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
            "qa/postgres/production-ready-search.sql",
            "qa/postgres/production-capabilities.sql",
            "qa/postgres/production-gc.sql",
            "qa/postgres/production_concurrency_harness.py",
            "qa/postgres/production-catalog-acl.sql",
        )
        result, calls = self.run_gate(files, readback="f")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRODUCTION_POSTGRES_SCHEMA_FAILED", result.stderr)
        self.assertEqual(len(calls), 3)

    def test_production_ready_search_fixture_is_required(self):
        fixture = ROOT / "qa/postgres/production-ready-search.sql"
        self.assertTrue(fixture.is_file(), "real production search probe missing")
        content = fixture.read_text(encoding="utf-8")
        self.assertIn("PRODUCTION_READY_SEARCH_PASS", content)
        self.assertIn("PRODUCTION_READY_SEARCH_ROLLBACK_PASS", content)
        self.assertIn("repo_search.mark_generation_ready", content)
        self.assertIn("repo_search.activate_generation", content)
        self.assertIn("repo_search.search(", content)
        self.assertIn("'hits'", content)
        endpoint = GATE.read_text(encoding="utf-8")
        self.assertIn("qa/postgres/production-ready-search.sql", endpoint)

    def test_production_capability_probe_is_required(self):
        fixture = ROOT / "qa/postgres/production-capabilities.sql"
        self.assertTrue(fixture.is_file(), "real role canary missing")
        sql = fixture.read_text(encoding="utf-8")
        for token in (
            "SET ROLE repo_search_reader",
            "SET ROLE repo_search_materializer",
            "WHEN SQLSTATE '42501'",
            "repo_search.search_generation_v1",
            "repo_search.mark_generation_ready",
            "PRODUCTION_CAPABILITIES_PASS",
            "PRODUCTION_CAPABILITIES_ROLLBACK_PASS",
        ):
            self.assertIn(token, sql)
        self.assertIn("qa/postgres/production-capabilities.sql",
                      GATE.read_text(encoding="utf-8"))

    def test_real_production_gc_probe_is_required(self):
        probe = ROOT / "qa/postgres/production-gc.sql"
        self.assertTrue(probe.is_file(), "production GC behavior fixture missing")
        sql = probe.read_text(encoding="utf-8")
        for token in (
            "PRODUCTION_GC_PASS", "PRODUCTION_GC_ROLLBACK_PASS",
            "repo_search.gc_inactive_generation", "repo_search.generation_references",
            "SET ROLE repo_search_materializer", "WHEN SQLSTATE '55000'",
        ):
            self.assertIn(token, sql)
        self.assertIn("qa/postgres/production-gc.sql",
                      GATE.read_text(encoding="utf-8"))

    def test_real_production_two_session_lock_probe_is_required(self):
        probe = ROOT / "qa/postgres/production_concurrency_harness.py"
        self.assertTrue(probe.is_file(), "real production concurrency harness missing")
        source = probe.read_text(encoding="utf-8")
        for marker in (
            "PsqlSession", "wait_for_lock", "async_step", "join_step",
            "repo_search_materializer", "repo_search.mark_generation_ready",
            "PRODUCTION_READY_WRITE_SERIALIZATION_PASS",
            "PRODUCTION_READY_WRITE_CLEANUP_PASS",
        ):
            self.assertIn(marker, source)
        self.assertIn("qa/postgres/production_concurrency_harness.py",
                      GATE.read_text(encoding="utf-8"))

    def test_real_production_owner_acl_probe_is_required(self):
        fixture = ROOT / "qa/postgres/production-catalog-acl.sql"
        self.assertTrue(fixture.is_file(), "real production catalog and ACL proof missing")
        sql = fixture.read_text(encoding="utf-8")
        for fragment in (
            "pg_proc", "pg_default_acl", "aclexplode", "repo_search_owner",
            "prosecdef", "proconfig", "SET ROLE repo_search_owner",
            "PRODUCTION_CATALOG_ACL_PASS", "PRODUCTION_CATALOG_ACL_ROLLBACK_PASS",
        ):
            self.assertIn(fragment, sql)
        self.assertIn("qa/postgres/production-catalog-acl.sql",
                      GATE.read_text(encoding="utf-8"))

    def test_runner_invokes_production_gate(self):
        endpoint = (ROOT / "tools/ci/heavy-postgres").read_text(encoding="utf-8")
        self.assertIn("tools/ci/postgres-production", endpoint)


if __name__ == "__main__":
    unittest.main()
