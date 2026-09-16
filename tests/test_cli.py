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


    def test_authoritative_readback_rejects_non_git_source_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "zeta23"
            source.mkdir()
            (source / "Tiny.lean").write_text("theorem a : True := by trivial\n", encoding="utf-8")
            out = root / "artifact"
            result = self.run_cli(
                "build-artifact",
                "--source-root", source,
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
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stderr)
            self.assertEqual(payload["code"], "BLOCKED_SOURCE_BINDING")
            self.assertFalse(out.exists())

    def test_authoritative_readback_requires_clean_checkout_at_exact_commit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            source = repo / "zeta23"
            source.mkdir(parents=True)
            (source / "Tiny.lean").write_text("theorem a : True := by trivial\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Repo Search Test"], check=True)
            subprocess.run(["git", "-C", repo, "remote", "add", "origin", "https://github.com/example/repo.git"], check=True)
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True)
            commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
            generated = source / ".lake" / "packages" / "Fake" / "Fake.lean"
            generated.parent.mkdir(parents=True)
            generated.write_text("theorem generated : True := by trivial\n", encoding="utf-8")
            out = root / "artifact"
            result = self.run_cli(
                "build-artifact",
                "--source-root", source,
                "--source-repo", "example/repo",
                "--source-commit", commit,
                "--source-subdir", "zeta23",
                "--root-module", "Zeta23",
                "--producer-kind", "lexical-only",
                "--producer-tool-repo", "TeaShaman-cyber/theseus-repo-search-lab",
                "--producer-tool-commit", TOOL_COMMIT,
                "--producer-tool-hash", TOOL_HASH,
                "--authoritative-readback",
                "--lexical-only",
                "--out", out,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["sources"], 1)
            manifest, _, _, _ = load_artifact(out)
            self.assertTrue(manifest.created_from_authoritative_commit)

            (source / "Tiny.lean").write_text("theorem a : False := by trivial\n", encoding="utf-8")
            dirty_out = root / "dirty-artifact"
            dirty = self.run_cli(
                "build-artifact",
                "--source-root", source,
                "--source-repo", "example/repo",
                "--source-commit", commit,
                "--source-subdir", "zeta23",
                "--root-module", "Zeta23",
                "--producer-kind", "lexical-only",
                "--producer-tool-repo", "TeaShaman-cyber/theseus-repo-search-lab",
                "--producer-tool-commit", TOOL_COMMIT,
                "--producer-tool-hash", TOOL_HASH,
                "--authoritative-readback",
                "--lexical-only",
                "--out", dirty_out,
            )
            self.assertNotEqual(dirty.returncode, 0)
            self.assertEqual(json.loads(dirty.stderr)["code"], "BLOCKED_SOURCE_MISMATCH")
            self.assertFalse(dirty_out.exists())

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

    def test_missing_projection_emits_machine_readable_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "missing.sqlite"
            result = self.run_cli("search", "--db", missing, "--query", "zeta")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            payload = json.loads(result.stderr)
            self.assertEqual(payload["code"], "UNAVAILABLE_PROJECTION")
            self.assertNotIn("Traceback", result.stderr)

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
            self.assertEqual(payload["status"], "BLOCKED")
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
