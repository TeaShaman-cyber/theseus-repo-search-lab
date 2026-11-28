import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theseus_repo_search import cli
from theseus_repo_search.accepted_catalog import load_accepted_catalog
from theseus_repo_search.query_capabilities import query_capabilities


class QueryStartupTests(unittest.TestCase):
    def test_startup_without_credentials_or_catalog(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(cli, "_turso_client") as turso,
            patch.object(cli, "_agent_kusto_client") as kusto,
            patch.object(cli, "load_accepted_catalog") as load,
            patch.object(cli, "_emit") as emit,
        ):
            self.assertEqual(cli.main(["query", "startup"]), 0)
        result = emit.call_args.args[0]
        self.assertEqual(result["availability"], "NOT_CHECKED")
        self.assertEqual(result["catalog"], {"status": "NOT_CONFIGURED"})
        self.assertEqual(set(result["components"]), {
            "turso", "kusto", "local-duckdb", "motherduck", "immutable-releases",
        })
        for op in query_capabilities()["operations"]:
            for backend in op["backends"]:
                self.assertIn(op["name"], result["components"][backend]["operations"])
        self.assertIn("reach-all", result["components"]["kusto"]["check"])
        self.assertIn("graph-search", result["components"]["kusto"]["operations"])
        self.assertIn("dev/check", result["qa"])
        turso.assert_not_called()
        kusto.assert_not_called()
        load.assert_not_called()

    def test_wrapper_reads_current_catalog_compactly(self):
        env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [str(root / "tools/repo-search"), "query", "startup"],
            env=env, cwd="/tmp", check=True, capture_output=True, text=True, timeout=10,
        )
        payload = json.loads(result.stdout)
        catalog = load_accepted_catalog(root / "producer/accepted-corpora.json")
        self.assertEqual(payload["catalog"]["accepted"], len(catalog.corpora))
        self.assertEqual(payload["catalog"]["split"], sum(
            c.split_projection_sha256 is not None for c in catalog.corpora
        ))
        self.assertEqual(payload["availability"], "NOT_CHECKED")
        self.assertLess(len(result.stdout.encode()), 2000)
        self.assertEqual(result.stderr, "")

    def test_invalid_catalog_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "invalid.json"
            p.write_text("{}")
            result = subprocess.run(
                [sys.executable, "-m", "theseus_repo_search", "query", "--catalog", str(p), "startup"],
                env={"PATH": os.environ["PATH"], "PYTHONPATH": "src"},
                check=False, capture_output=True, text=True, timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED_ACCEPTED_CATALOG", result.stderr)
