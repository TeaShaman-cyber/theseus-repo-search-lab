import importlib.util
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

HARNESS_PATH = Path(__file__).resolve().parents[1] / "qa/postgres/concurrency_harness.py"


class PostgresHarnessCleanupTests(unittest.TestCase):
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
