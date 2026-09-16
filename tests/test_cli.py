import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from theseus_repo_search.artifact import load_artifact


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "raw_leandepviz.json"
SOURCE_ROOT = ROOT / "tests" / "fixtures" / "lean_src"
SOURCE_COMMIT = "a" * 40
TOOL_COMMIT = "b" * 40
TOOL_HASH = "c" * 64


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "theseus_repo_search", *map(str, args)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def build_exact(self, out: Path):
        result = self.run_cli(
            "build-artifact",
            "--source-root", SOURCE_ROOT,
            "--source-repo", "anthropics/formal-math",
            "--source-commit", SOURCE_COMMIT,
            "--source-subdir", "zeta23",
            "--root-module", "Zeta23",
            "--producer-kind", "lean-dep-viz",
            "--producer-tool-repo", "cameronfreer/LeanDepViz",
            "--producer-tool-commit", TOOL_COMMIT,
            "--producer-tool-hash", TOOL_HASH,
            "--authoritative-readback",
            "--raw-depgraph", RAW,
            "--out", out,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def build_lexical(self, out: Path):
        result = self.run_cli(
            "build-artifact",
            "--source-root", SOURCE_ROOT,
            "--source-repo", "anthropics/formal-math",
            "--source-commit", SOURCE_COMMIT,
            "--source-subdir", "zeta23",
            "--root-module", "Zeta23",
            "--producer-kind", "ignored-in-lexical-mode",
            "--producer-tool-repo", "TeaShaman-cyber/theseus-repo-search-lab",
            "--producer-tool-commit", TOOL_COMMIT,
            "--producer-tool-hash", TOOL_HASH,
            "--lexical-only",
            "--out", out,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_exact_build_writes_four_members_and_verify_returns_verified(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = Path(d) / "artifact"
            built = self.build_exact(artifact)
            self.assertEqual(built["status"], "BUILT")
            self.assertEqual(
                sorted(path.name for path in artifact.iterdir()),
                ["edges.jsonl", "manifest.json", "nodes.jsonl", "sources.jsonl"],
            )
            verified = self.run_cli("verify-artifact", "--artifact", artifact)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(json.loads(verified.stdout)["status"], "VERIFIED")

    def test_build_index_returns_fingerprint_and_deps_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = root / "artifact", root / "index.sqlite"
            self.build_exact(artifact)
            indexed = self.run_cli("build-index", "--artifact", artifact, "--db", db)
            self.assertEqual(indexed.returncode, 0, indexed.stderr)
            self.assertTrue(json.loads(indexed.stdout)["fingerprint"])
            deps = self.run_cli("deps", "--db", db, "--name", "Zeta23.Tiny.b")
            self.assertEqual(deps.returncode, 0, deps.stderr)
            payload = json.loads(deps.stdout)
            self.assertEqual(payload["dependency_boundary"], "internal_only")
            self.assertEqual(payload["scope_root_modules"], ["Zeta23"])

    def test_search_no_hit_is_unknown_but_successful_process(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = root / "artifact", root / "index.sqlite"
            self.build_exact(artifact)
            self.run_cli("build-index", "--artifact", artifact, "--db", db)
            result = self.run_cli("search", "--db", db, "--query", "nothing_like_this_token")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "UNKNOWN")
            self.assertEqual(payload["hits"], [])

    def test_tampered_artifact_emits_machine_error(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = Path(d) / "artifact"
            self.build_exact(artifact)
            (artifact / "edges.jsonl").write_text("{}\n", encoding="utf-8")
            result = self.run_cli("verify-artifact", "--artifact", artifact)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            payload = json.loads(result.stderr)
            self.assertEqual(payload["status"], "ERROR")
            self.assertEqual(payload["code"], "BLOCKED_ARTIFACT_INTEGRITY")

    def test_lexical_only_artifact_is_searchable_and_has_zero_graph(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = root / "artifact", root / "index.sqlite"
            self.build_lexical(artifact)
            manifest, nodes, edges, sources = load_artifact(artifact)
            self.assertEqual(manifest.producer.kind, "lexical_only")
            self.assertEqual(nodes, [])
            self.assertEqual(edges, [])
            self.assertTrue(sources)
            self.run_cli("build-index", "--artifact", artifact, "--db", db)
            result = self.run_cli("search", "--db", db, "--query", "rank trace tightness")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FOUND")
            self.assertEqual(payload["hits"][0]["declaration_hint"], "lemmaR_tight_two")

    def test_deps_on_lexical_only_projection_returns_unavailable_grade(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = root / "artifact", root / "index.sqlite"
            self.build_lexical(artifact)
            self.run_cli("build-index", "--artifact", artifact, "--db", db)
            result = self.run_cli("deps", "--db", db, "--name", "anything")
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stderr)
            self.assertEqual(payload["code"], "UNAVAILABLE_EVIDENCE_GRADE")
