import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

from theseus_repo_search import cli


class QueryCapabilitiesTests(unittest.TestCase):
    def test_discovery_without_profile_or_backend_access(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(cli, "load_accepted_catalog") as catalog, patch.object(cli, "_turso_client") as turso, patch.object(cli, "_agent_kusto_client") as kusto, patch.object(cli, "_emit") as emit:
            try:
                rc = cli.main(["query", "capabilities"])
            except SystemExit as exc:
                self.fail(f"capability discovery rejected: {exc}")
        self.assertEqual(rc, 0)
        result = emit.call_args.args[0]
        self.assertEqual(result["schema"], "theseus.repo-search.query-capabilities.v1")
        self.assertEqual(result["availability"], "NOT_CHECKED")
        self.assertTrue(result["read_only"])
        catalog.assert_not_called()
        turso.assert_not_called()
        kusto.assert_not_called()
        operations = {op["name"]: op for op in result["operations"]}
        self.assertEqual(set(operations), {"capabilities", "corpora", "search", "graph-search", "reach", "reach-all", "deps", "rdeps", "path", "context", "overlap"})
        self.assertEqual(result["research_routes"]["discover_structure"], ["graph-search", "context"])
        self.assertEqual(result["research_routes"]["compare_known_declarations"], ["reach-all", "context"])
        self.assertEqual(operations["graph-search"]["backends"], ["turso", "kusto"])
        self.assertEqual(operations["context"]["backends"], ["turso", "local-duckdb"])
        self.assertIn("storage", result["motherduck_role"])

    def test_help_exposes_all_research_operations(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as exc:
            cli.main(["query", "--help"])
        self.assertEqual(exc.exception.code, 0)
        help_text = out.getvalue()
        for phrase in ("capabilities", "Discover mathematical structure", "Typed dependency", "Bounded source context", "Compare known declarations"):
            self.assertIn(phrase, help_text)

    def test_each_advertised_example_parses(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(cli, "_emit") as emit:
            try:
                rc = cli.main(["query", "capabilities"])
            except SystemExit as exc:
                self.fail(f"capability discovery rejected: {exc}")
        self.assertEqual(rc, 0)
        for op in emit.call_args.args[0]["operations"]:
            with self.subTest(operation=op["name"]), contextlib.redirect_stdout(io.StringIO()) as help_out, self.assertRaises(SystemExit) as exc:
                cli.main(["query", op["name"], "--help"])
            self.assertEqual(exc.exception.code, 0)
            self.assertIn(op["name"], help_out.getvalue())
            argv = shlex.split(op["example"])
            self.assertEqual(argv[0], "repo-search")
            parsed = cli.build_parser().parse_args(argv[1:])
            self.assertEqual(parsed.operation, op["name"])

    def test_offline_json_wire_format(self):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "src"}
        result = subprocess.run([sys.executable, "-m", "theseus_repo_search", "query", "capabilities"],
                                env=env, check=True, capture_output=True, text=True, timeout=10)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "theseus.repo-search.query-capabilities.v1")
        self.assertEqual(result.stderr, "")
