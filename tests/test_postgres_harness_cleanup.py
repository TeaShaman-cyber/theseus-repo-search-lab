import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

HARNESS_PATH = Path(__file__).resolve().parents[1] / "qa/postgres/concurrency_harness.py"


class PostgresHarnessCleanupTests(unittest.TestCase):
    def test_cleanup_readback_passes_one_complete_sql_argument(self):
        runner = HARNESS_PATH.parents[2] / "tools/ci/heavy-postgres"
        text = runner.read_text(encoding="utf-8")
        begin = text.index("cleanup_ok=$(psql")
        end = text.index("printf '%s\\n' 'HEAVY_POSTGRES_CLEANUP_PASS'", begin)
        shell_fragment = text[begin:end] + "printf 'POSTFLIGHT_DONE\\n'\n"
        with tempfile.TemporaryDirectory() as directory:
            fake_psql = Path(directory) / "psql"
            fake_psql.write_text(
                "#!/bin/sh\n"
                '[ "$#" -eq 5 ] || exit 40\n'
                'case "$5" in\n'
                "  *to_regnamespace*pg_roles*) printf 't\\n';;\n"
                "  *) exit 41;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_psql.chmod(0o755)
            env = {**os.environ, "PATH": directory + ":" + os.environ["PATH"]}
            proc = subprocess.run(
                ["sh", "-c", shell_fragment], env=env,
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("POSTFLIGHT_DONE", proc.stdout)

    def test_failed_cleanup_must_not_report_harness_success(self):
        name = "postgres_harness_cleanup_test_module"
        spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            with ExitStack() as stack:
                stack.enter_context(patch.object(module, "PsqlSession"))
                for step in (
                    "writer_first", "ready_first", "stale_cas_activation",
                    "immutable_child_generation", "guarded_inactive_ready_gc",
                    "version_dispatch_v1_v2", "zero_hit_atomic_envelope",
                    "bounded_query_inputs", "failed_migration_rollback",
                ):
                    stack.enter_context(patch.object(module, step))
                cleanup = stack.enter_context(patch.object(
                    module, "one_shot", side_effect=subprocess.CalledProcessError(3, "psql"),
                ))
                with self.assertRaises(module.HarnessError):
                    module.main()
                cleanup.assert_called_once()
        finally:
            sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
