import json
import os
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from theseus_repo_search.artifact import load_artifact
from theseus_repo_search.cli import (
    _verify_archive_raw_depgraph_receipt,
    _verify_raw_depgraph_receipt,
)
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.producer_config import LeanArchiveSource

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "raw_leandepviz.json"
SOURCE_ROOT = ROOT / "tests" / "fixtures" / "lean_src"
SOURCE_COMMIT = "a" * 40
TOOL_COMMIT = "b" * 40
TOOL_HASH = "c" * 64


class RawDepgraphReceiptTests(unittest.TestCase):
    def test_receipt_binds_graph_source_scope_and_producer(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw.json"
            raw.write_bytes(b'{"nodes":[],"edges":[]}\n')
            receipt = root / "receipt.json"
            base = {
                "schema": "theseus.raw-depgraph-receipt.v2",
                "source": {"repo": "example/repo", "commit": "a" * 40, "subdir": "zeta23"},
                "scope": {"root_modules": ["Zeta23"]},
                "producer": {
                    "kind": "lean-dep-viz",
                    "tool_repo": "cameronfreer/LeanDepViz",
                    "tool_commit": "b" * 40,
                    "tool_hash": "c" * 64,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.33.0"},
                "raw_depgraph": {"sha256": sha256(raw.read_bytes()).hexdigest()},
            }

            def verify(payload):
                receipt.write_text(json.dumps(payload), encoding="utf-8")
                _verify_raw_depgraph_receipt(
                    receipt,
                    raw,
                    source_repo="example/repo",
                    source_commit="a" * 40,
                    source_subdir="zeta23",
                    root_modules=("Zeta23",),
                    producer_kind="lean-dep-viz",
                    producer_tool_repo="cameronfreer/LeanDepViz",
                    producer_tool_commit="b" * 40,
                    producer_tool_hash="c" * 64,
                )

            verify(base)
            for label, mutate, code in (
                ("commit", lambda x: x["source"].__setitem__("commit", "d" * 40), "BLOCKED_SOURCE_MISMATCH"),
                ("producer", lambda x: x["producer"].__setitem__("tool_commit", "d" * 40), "BLOCKED_SOURCE_MISMATCH"),
                ("hash", lambda x: x["raw_depgraph"].__setitem__("sha256", "0" * 64), "BLOCKED_SOURCE_MISMATCH"),
                ("observed-missing", lambda x: x.pop("observed"), "BLOCKED_SOURCE_BINDING"),
                ("observed-empty", lambda x: x.__setitem__("observed", {"lean_toolchain": ""}), "BLOCKED_SOURCE_BINDING"),
                ("observed-malformed", lambda x: x.__setitem__("observed", "v4.33.0"), "BLOCKED_SOURCE_BINDING"),
                ("observed-extra", lambda x: x["observed"].__setitem__("publisher", "example"), "BLOCKED_SOURCE_BINDING"),
            ):
                with self.subTest(label=label):
                    payload = json.loads(json.dumps(base))
                    mutate(payload)
                    with self.assertRaises(RepoSearchError) as caught:
                        verify(payload)
                    self.assertEqual(caught.exception.code, code)
    def test_archive_v3_receipt_binds_structured_authority_and_materialization(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw.json"
            raw.write_bytes(b'{"nodes":[],"edges":[]}\n')
            receipt = root / "receipt.json"
            source = LeanArchiveSource.from_dict({
                "schema": "theseus.lean-archive-source.v1",
                "source_id": "fixture-archive",
                "archive_url": "https://example.invalid/source.zip",
                "archive_sha256": "a" * 64,
                "archive_format": "zip",
                "source_subdir": "pkg",
                "root_modules": ["Main"],
                "build_target": "Main",
                "exclude_source_prefixes": [],
            })
            base = {
                "schema": "theseus.raw-depgraph-receipt.v3",
                "source": {
                    "kind": "archive",
                    "url": source.archive_url,
                    "sha256": source.archive_sha256,
                    "format": source.archive_format,
                    "subdir": source.source_subdir,
                },
                "materialization": {
                    "tree_sha256": "d" * 64,
                    "member_manifest_sha256": "e" * 64,
                },
                "scope": {"root_modules": ["Main"]},
                "producer": {
                    "kind": "lean-dep-viz",
                    "tool_repo": "cameronfreer/LeanDepViz",
                    "tool_commit": "b" * 40,
                    "tool_hash": "c" * 64,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.30.0"},
                "raw_depgraph": {"sha256": sha256(raw.read_bytes()).hexdigest()},
            }

            def verify(payload):
                receipt.write_text(json.dumps(payload), encoding="utf-8")
                _verify_archive_raw_depgraph_receipt(
                    receipt,
                    raw,
                    source=source,
                    tree_sha256="d" * 64,
                    member_manifest_sha256="e" * 64,
                    producer_kind="lean-dep-viz",
                    producer_tool_repo="cameronfreer/LeanDepViz",
                    producer_tool_commit="b" * 40,
                    producer_tool_hash="c" * 64,
                )

            verify(base)
            for label, mutate, code in (
                ("fake-git", lambda x: x["source"].__setitem__("commit", "f" * 40), "BLOCKED_SOURCE_MISMATCH"),
                ("archive-sha", lambda x: x["source"].__setitem__("sha256", "f" * 64), "BLOCKED_SOURCE_MISMATCH"),
                ("tree", lambda x: x["materialization"].__setitem__("tree_sha256", "f" * 64), "BLOCKED_SOURCE_MISMATCH"),
                ("members", lambda x: x["materialization"].__setitem__("member_manifest_sha256", "f" * 64), "BLOCKED_SOURCE_MISMATCH"),
                ("producer", lambda x: x["producer"].__setitem__("tool_commit", "f" * 40), "BLOCKED_SOURCE_MISMATCH"),
                ("raw", lambda x: x["raw_depgraph"].__setitem__("sha256", "0" * 64), "BLOCKED_SOURCE_MISMATCH"),
                ("observed-missing", lambda x: x.pop("observed"), "BLOCKED_SOURCE_BINDING"),
                ("observed-extra", lambda x: x["observed"].__setitem__("publisher", "example"), "BLOCKED_SOURCE_BINDING"),
            ):
                with self.subTest(label=label):
                    payload = json.loads(json.dumps(base))
                    mutate(payload)
                    with self.assertRaises(RepoSearchError) as caught:
                        verify(payload)
                    self.assertEqual(caught.exception.code, code)



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
            check=False,
        )

    def write_archive_build_fixture(
        self,
        root: Path,
        *,
        unbacked_node: bool = False,
        generated_descendant: bool = False,
        structure_driver: bool = False,
    ):
        materialized = root / "materialized"
        (materialized / "Pkg").mkdir(parents=True)
        main_bytes = (
            b"structure Driver where\n  aleph0_le : True\n"
            if structure_driver
            else b"theorem ok : True := by trivial\n"
        )
        toolchain_bytes = b"leanprover/lean4:v4.30.0\n"
        (materialized / "Pkg" / "Main.lean").write_bytes(main_bytes)
        (materialized / "lean-toolchain").write_bytes(toolchain_bytes)
        (materialized / "Pkg" / "Generated.lean").write_text(
            "theorem injected : True := by trivial\n", encoding="utf-8"
        )

        archive_sha = "a" * 64
        source_payload = {
            "schema": "theseus.lean-archive-source.v1",
            "source_id": "task-five-fixture",
            "archive_url": "https://example.invalid/source.zip",
            "archive_sha256": archive_sha,
            "archive_format": "zip",
            "source_subdir": ".",
            "root_modules": ["Pkg"],
            "build_target": "Pkg",
            "exclude_source_prefixes": [],
        }
        descriptor = root / "source.json"
        descriptor.write_text(json.dumps(source_payload), encoding="utf-8")
        source_identity = {
            "kind": "archive",
            "url": source_payload["archive_url"],
            "sha256": archive_sha,
            "format": "zip",
            "subdir": ".",
        }
        file_hashes = sorted(
            [
                ("Pkg/Main.lean", sha256(main_bytes).hexdigest()),
                ("lean-toolchain", sha256(toolchain_bytes).hexdigest()),
            ]
        )
        tree_sha = sha256(
            json.dumps(file_hashes, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        member_payload = {
            "schema": "theseus.archive-member-manifest.v1",
            "source": source_identity,
            "source_root_relative": ".",
            "members": [
                {
                    "path": "Pkg/Main.lean",
                    "source_path": "Pkg/Main.lean",
                    "sha256": sha256(main_bytes).hexdigest(),
                },
                {
                    "path": "lean-toolchain",
                    "source_path": "lean-toolchain",
                    "sha256": sha256(toolchain_bytes).hexdigest(),
                },
            ],
        }
        member_bytes = (
            json.dumps(member_payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        member_manifest = root / "members.json"
        member_manifest.write_bytes(member_bytes)
        member_sha = sha256(member_bytes).hexdigest()
        materialization_receipt = root / "materialization.json"
        materialization_receipt.write_text(
            json.dumps(
                {
                    "schema": "theseus.archive-materialization-receipt.v1",
                    "source": source_identity,
                    "materialized": {
                        "file_count": 2,
                        "tree_sha256": tree_sha,
                        "member_manifest_sha256": member_sha,
                        "source_root_relative": ".",
                    },
                }
            ),
            encoding="utf-8",
        )
        raw_nodes = [
            (
                {
                    "module": "Pkg.Main",
                    "fullName": "Pkg.Driver.aleph0_le",
                    "name": "aleph0_le",
                    "kind": "def",
                }
                if structure_driver
                else {
                    "module": "Pkg.Main",
                    "fullName": "Pkg.Main.ok",
                    "name": "ok",
                    "kind": "thm",
                }
            )
        ]
        authoritative_name = (
            "Pkg.Driver.aleph0_le" if structure_driver else "Pkg.Main.ok"
        )
        raw_edges = []
        if unbacked_node:
            raw_nodes.append(
                {
                    "module": "Pkg.Generated",
                    "fullName": "Pkg.Generated.injected",
                    "name": "injected",
                    "kind": "thm",
                }
            )
            raw_edges.append(
                {
                    "kind": "value",
                    "source": "Pkg.Generated.injected",
                    "target": authoritative_name,
                }
            )
        if generated_descendant:
            generated_name = (
                "Pkg.Driver.mk.inj" if structure_driver else "Pkg.Main.ok._proof_1"
            )
            raw_nodes.append(
                {
                    "module": "Pkg.Main",
                    "fullName": generated_name,
                    "name": "inj" if structure_driver else "_proof_1",
                    "kind": "thm",
                }
            )
            raw_edges.append(
                {
                    "kind": "value",
                    "source": generated_name,
                    "target": authoritative_name,
                }
            )
        raw = root / "raw.json"
        raw_bytes = (
            json.dumps(
                {"nodes": raw_nodes, "edges": raw_edges},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        raw.write_bytes(raw_bytes)
        raw_receipt = root / "raw-receipt.json"
        raw_receipt.write_text(
            json.dumps(
                {
                    "schema": "theseus.raw-depgraph-receipt.v3",
                    "source": source_identity,
                    "materialization": {
                        "tree_sha256": tree_sha,
                        "member_manifest_sha256": member_sha,
                    },
                    "scope": {"root_modules": ["Pkg"]},
                    "producer": {
                        "kind": "lean-dep-viz",
                        "tool_repo": "cameronfreer/LeanDepViz",
                        "tool_commit": TOOL_COMMIT,
                        "tool_hash": TOOL_HASH,
                    },
                    "observed": {"lean_toolchain": "leanprover/lean4:v4.30.0"},
                    "raw_depgraph": {"sha256": sha256(raw_bytes).hexdigest()},
                }
            ),
            encoding="utf-8",
        )
        return {
            "materialized": materialized,
            "descriptor": descriptor,
            "materialization_receipt": materialization_receipt,
            "member_manifest": member_manifest,
            "raw": raw,
            "raw_receipt": raw_receipt,
        }

    def build_archive(self, fixture, out: Path):
        return self.run_cli(
            "build-archive-artifact",
            "--source", fixture["descriptor"],
            "--materialization-root", fixture["materialized"],
            "--materialization-receipt", fixture["materialization_receipt"],
            "--member-manifest", fixture["member_manifest"],
            "--raw-depgraph", fixture["raw"],
            "--raw-depgraph-receipt", fixture["raw_receipt"],
            "--producer-kind", "lean-dep-viz",
            "--producer-tool-repo", "cameronfreer/LeanDepViz",
            "--producer-tool-commit", TOOL_COMMIT,
            "--producer-tool-hash", TOOL_HASH,
            "--out", out,
        )

    def test_build_source_artifact_dispatches_archive_without_git_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture = self.write_archive_build_fixture(root)
            out = root / "generic-artifact"
            result = self.run_cli(
                "build-source-artifact",
                "--source", fixture["descriptor"],
                "--source-root", fixture["materialized"],
                "--source-container", fixture["materialized"],
                "--materialization-receipt", fixture["materialization_receipt"],
                "--member-manifest", fixture["member_manifest"],
                "--raw-depgraph", fixture["raw"],
                "--raw-depgraph-receipt", fixture["raw_receipt"],
                "--producer-kind", "lean-dep-viz",
                "--producer-tool-repo", "cameronfreer/LeanDepViz",
                "--producer-tool-commit", TOOL_COMMIT,
                "--producer-tool-hash", TOOL_HASH,
                "--out", out,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest, *_ = load_artifact(out)
            self.assertEqual(manifest.schema, "theseus.repo-index.v2")
            self.assertNotIn("source_commit", (out / "manifest.json").read_text())

    def test_build_archive_artifact_uses_only_manifest_backed_sources(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture = self.write_archive_build_fixture(root)
            out = root / "artifact"
            result = self.build_archive(fixture, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest, nodes, edges, sources = load_artifact(out)
            self.assertEqual(manifest.schema, "theseus.repo-index.v2")
            self.assertTrue(manifest.created_from_authoritative_source)
            self.assertEqual(manifest.source_revision, "a" * 64)
            self.assertEqual([chunk.source_path for chunk in sources], ["Pkg/Main.lean"])
            self.assertEqual([node.source_path for node in nodes], ["Pkg/Main.lean"])
            self.assertEqual(edges, [])
            self.assertNotIn("Generated.lean", (out / "sources.jsonl").read_text())
            self.assertNotIn("source_commit", (out / "nodes.jsonl").read_text())

    def test_authoritative_v2_loader_rejects_node_without_source_binding(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture = self.write_archive_build_fixture(root)
            out = root / "artifact"
            result = self.build_archive(fixture, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            nodes_path = out / "nodes.jsonl"
            node = json.loads(nodes_path.read_text())
            node["source_path"] = None
            node["source_start_line"] = None
            node["source_end_line"] = None
            nodes_bytes = (
                json.dumps(node, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            nodes_path.write_bytes(nodes_bytes)
            manifest_path = out / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["members"]["nodes"]["sha256"] = sha256(nodes_bytes).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RepoSearchError, "lacks source binding"):
                load_artifact(out)

    def test_build_archive_artifact_drops_generated_descendant_and_edges(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture = self.write_archive_build_fixture(
                root, generated_descendant=True, structure_driver=True
            )
            out = root / "artifact"
            result = self.build_archive(fixture, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            _manifest, nodes, edges, _sources = load_artifact(out)
            self.assertEqual(
                [node.full_name for node in nodes], ["Pkg.Driver.aleph0_le"]
            )
            self.assertEqual([node.source_path for node in nodes], ["Pkg/Main.lean"])
            self.assertEqual(edges, [])

    def test_build_archive_artifact_rejects_source_mutation_after_raw_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture = self.write_archive_build_fixture(root)
            (fixture["materialized"] / "Pkg" / "Main.lean").write_text(
                "theorem ok : False := by contradiction\n", encoding="utf-8"
            )
            out = root / "artifact"
            result = self.build_archive(fixture, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stderr)["code"], "BLOCKED_SOURCE_MISMATCH")
            self.assertFalse(out.exists())

    def test_build_archive_artifact_rejects_unmanifested_node_and_edge(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture = self.write_archive_build_fixture(root, unbacked_node=True)
            out = root / "artifact"
            result = self.build_archive(fixture, out)
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stderr)
            self.assertEqual(payload["code"], "BLOCKED_SOURCE_MISMATCH")
            self.assertIn("Pkg.Generated.injected", payload["message"])
            self.assertFalse(out.exists())

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

    def test_authoritative_exact_build_requires_bound_raw_graph_receipt(self):
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
            out = root / "artifact"
            result = self.run_cli(
                "build-artifact",
                "--source-root", source,
                "--source-repo", "example/repo",
                "--source-commit", commit,
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
            self.assertIn("receipt", payload["message"])
            self.assertFalse(out.exists())

    def test_authoritative_exact_build_rejects_mismatched_raw_graph_receipt(self):
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
            receipt = root / "receipt.json"
            receipt.write_text(json.dumps({
                "schema": "theseus.raw-depgraph-receipt.v2",
                "source": {"repo": "example/repo", "commit": commit, "subdir": "zeta23"},
                "scope": {"root_modules": ["Zeta23"]},
                "producer": {
                    "kind": "lean-dep-viz",
                    "tool_repo": "cameronfreer/LeanDepViz",
                    "tool_commit": TOOL_COMMIT,
                    "tool_hash": TOOL_HASH,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.33.0"},
                "raw_depgraph": {"sha256": "0" * 64},
            }), encoding="utf-8")
            out = root / "artifact"
            result = self.run_cli(
                "build-artifact",
                "--source-root", source,
                "--source-repo", "example/repo",
                "--source-commit", commit,
                "--source-subdir", "zeta23",
                "--root-module", "Zeta23",
                "--producer-kind", "lean-dep-viz",
                "--producer-tool-repo", "cameronfreer/LeanDepViz",
                "--producer-tool-commit", TOOL_COMMIT,
                "--producer-tool-hash", TOOL_HASH,
                "--authoritative-readback",
                "--raw-depgraph", RAW,
                "--raw-depgraph-receipt", receipt,
                "--out", out,
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stderr)
            self.assertEqual(payload["code"], "BLOCKED_SOURCE_MISMATCH")
            self.assertIn("raw dependency graph receipt", payload["message"])
            self.assertFalse(out.exists())

    def test_authoritative_exact_build_binds_receipt_inside_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            source = repo / "zeta23"
            (source / "Zeta23").mkdir(parents=True)
            (source / "Zeta23" / "Tiny.lean").write_text(
                (SOURCE_ROOT / "Zeta23" / "Tiny.lean").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Repo Search Test"], check=True)
            subprocess.run(["git", "-C", repo, "remote", "add", "origin", "https://github.com/example/repo.git"], check=True)
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True)
            commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
            receipt = root / "receipt.json"
            receipt.write_text(json.dumps({
                "schema": "theseus.raw-depgraph-receipt.v2",
                "source": {"repo": "example/repo", "commit": commit, "subdir": "zeta23"},
                "scope": {"root_modules": ["Zeta23"]},
                "producer": {
                    "kind": "lean-dep-viz",
                    "tool_repo": "cameronfreer/LeanDepViz",
                    "tool_commit": TOOL_COMMIT,
                    "tool_hash": TOOL_HASH,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.33.0"},
                "raw_depgraph": {"sha256": sha256(RAW.read_bytes()).hexdigest()},
            }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            artifact = root / "artifact"
            result = self.run_cli(
                "build-artifact",
                "--source-root", source,
                "--source-repo", "example/repo",
                "--source-commit", commit,
                "--source-subdir", "zeta23",
                "--root-module", "Zeta23",
                "--producer-kind", "lean-dep-viz",
                "--producer-tool-repo", "cameronfreer/LeanDepViz",
                "--producer-tool-commit", TOOL_COMMIT,
                "--producer-tool-hash", TOOL_HASH,
                "--authoritative-readback",
                "--raw-depgraph", RAW,
                "--raw-depgraph-receipt", receipt,
                "--out", artifact,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((artifact / "authority-receipt.json").is_file())
            manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            expected = sha256((artifact / "authority-receipt.json").read_bytes()).hexdigest()
            self.assertEqual(manifest["members"]["authority_receipt"]["sha256"], expected)

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

    def test_path_without_bounded_connection_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = root / "artifact", root / "index.sqlite"
            self.build_exact(artifact)
            self.run_cli("build-index", "--artifact", artifact, "--db", db)
            result = self.run_cli(
                "path", "--db", db,
                "--source", "Zeta23.Tiny.a",
                "--target", "Zeta23.Tiny.b",
                "--max-depth", "1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "UNKNOWN")
            self.assertEqual(payload["edges"], [])

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
