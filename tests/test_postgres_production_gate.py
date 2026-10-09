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
                    if line.startswith("-X -v "):
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

    def test_complete_migration_set_applied_and_read_back(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
        )
        result, calls = self.run_gate(files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PRODUCTION_POSTGRES_SCHEMA_PASS", result.stdout)
        self.assertEqual(len(calls), 3, calls)
        self.assertIn(files[0], calls[0])
        self.assertIn(files[1], calls[1])
        self.assertIn("to_regclass", calls[2])

    def test_bad_catalog_readback_fails_closed(self):
        files = (
            "sql/neon/001_query_plane_v1.sql",
            "sql/neon/002_query_plane_privileges.sql",
        )
        result, calls = self.run_gate(files, readback="f")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRODUCTION_POSTGRES_SCHEMA_FAILED", result.stderr)
        self.assertEqual(len(calls), 3)

    def test_runner_invokes_production_gate(self):
        endpoint = (ROOT / "tools/ci/heavy-postgres").read_text(encoding="utf-8")
        self.assertIn("tools/ci/postgres-production", endpoint)


if __name__ == "__main__":
    unittest.main()
