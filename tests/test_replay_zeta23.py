import json
import os
import sys
import tempfile
import subprocess
import unittest
from hashlib import sha256
from pathlib import Path

from scripts.replay_zeta23 import run_replay
from theseus_repo_search.artifact import write_artifact
from theseus_repo_search.model import ArtifactScope, Edge, EvidenceGrade, Node, ProducerPin, SourceChunk
from theseus_repo_search.producer_config import load_lean_git_source, load_runner_pins
from theseus_repo_search.projection import build_projection
from tests.raw_fixture import raw_depgraph_bytes, receipt_bytes


DESCRIPTOR = Path("producer/sources/zeta23.json")
RUNNER = load_runner_pins(Path("producer/runner.json"))
COMMIT = "fbdc36bbf17d20af3fd0447c6d1a8a02773c9844"


def node(full_name: str) -> Node:
    return Node.from_lean(
        full_name=full_name,
        name=full_name.rsplit(".", 1)[-1],
        kind="thm",
        module=full_name.rsplit(".", 1)[0],
        source_commit=COMMIT,
    )


def edge(source: str, target: str) -> Edge:
    return Edge(
        source_id=f"lean:{source}",
        target_id=f"lean:{target}",
        relation="value_dependency",
        evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
        producer=f"{RUNNER.extractor_repo}@{RUNNER.extractor_commit}",
    )


def chunk(hint: str, path: str, text: str, line: int) -> SourceChunk:
    return SourceChunk(
        id=f"src:{path}:{line}:{line}",
        source_commit=COMMIT,
        source_path=path,
        source_start_line=line,
        source_end_line=line,
        declaration_hint=hint,
        text=text,
        content_sha256=sha256(text.encode()).hexdigest(),
    )



def make_source_root(root: Path) -> Path:
    source_root = root / "source"
    source_root.mkdir()
    (source_root / "one.lean").write_text("trace moment\n", encoding="utf-8")
    (source_root / "two.lean").write_text("certificate\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", source_root], check=True)
    subprocess.run(["git", "-C", source_root, "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", source_root, "config", "user.name", "Repo Search Test"], check=True)
    subprocess.run(["git", "-C", source_root, "add", "one.lean", "two.lean"], check=True)
    subprocess.run(["git", "-C", source_root, "commit", "-qm", "fixture"], check=True)
    return source_root

def write_fixture_artifact(root: Path, source, name: str = "artifact") -> Path:
    artifact = root / name
    producer = ProducerPin(
        kind="lean-dep-viz",
        tool_repo=RUNNER.extractor_repo,
        tool_commit=RUNNER.extractor_commit,
        tool_hash=RUNNER.extractor_main_sha256,
    )
    names = [
        "Zeta23.ZeroSide.TightMult.lemmaR_tight_two",
        "Zeta23.ZeroSide.TightMult.lemmaR_tight",
        "Zeta23.ZeroSide.RankTraceMult.rank_trace_mult_k",
        "Zeta23.ZeroSide.RankTraceMult.rank_trace_mult_k_le",
        "Zeta23.Assembly.count_certificate",
        "Zeta23.Assembly.N0star_lower_moment",
        "Zeta23.Hypotheses.ChebyshevMertens",
    ]
    nodes = [Node.from_lean(
        full_name=full_name,
        name=full_name.rsplit(".", 1)[-1],
        kind="thm",
        module=full_name.rsplit(".", 1)[0],
        source_commit=source.source_commit,
    ) for full_name in names]
    edges = [
        edge(names[0], names[1]),
        edge(names[3], names[2]),
        edge(names[4], names[5]),
    ]
    raw = raw_depgraph_bytes(nodes, edges)
    write_artifact(
        artifact,
        nodes=nodes,
        edges=edges,
        sources=[
            SourceChunk(
                id="src:Tight.lean:1:1", source_commit=source.source_commit, source_path="Tight.lean",
                source_start_line=1, source_end_line=1, declaration_hint="lemmaR_tight_two",
                text="simples doubles tight pairs extremal\n",
                content_sha256=sha256(b"simples doubles tight pairs extremal\n").hexdigest(),
            ),
            SourceChunk(
                id="src:Hypotheses.lean:1:1", source_commit=source.source_commit, source_path="Hypotheses.lean",
                source_start_line=1, source_end_line=1, declaration_hint="ChebyshevMertens",
                text="Chebyshev Mertens arithmetic interface\n",
                content_sha256=sha256(b"Chebyshev Mertens arithmetic interface\n").hexdigest(),
            ),
            SourceChunk(
                id="src:Certificate.lean:1:1", source_commit=source.source_commit, source_path="Certificate.lean",
                source_start_line=1, source_end_line=1, declaration_hint="count_certificate",
                text="certificate trace moment\n",
                content_sha256=sha256(b"certificate trace moment\n").hexdigest(),
            ),
        ],
        source_repo=source.source_repo,
        source_commit=source.source_commit,
        source_subdir=source.source_subdir,
        producer=producer,
        scope=ArtifactScope(root_modules=source.root_modules, dependency_boundary="internal_only"),
        created_from_authoritative_commit=True,
        authority_receipt=receipt_bytes(source, producer, raw),
        raw_depgraph=raw,
    )
    return artifact


def build_fixture(root: Path) -> tuple[Path, Path]:
    source = load_lean_git_source(DESCRIPTOR)
    artifact = write_fixture_artifact(root, source)
    db = root / "index.sqlite"
    build_projection(artifact, db)
    return artifact, db


class ReplayZeta23Tests(unittest.TestCase):
    def test_registered_replay_passes_on_matching_graph_and_retrieval_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source_root = root / "source"
            source_root.mkdir()
            (source_root / "one.lean").write_text("trace moment\n", encoding="utf-8")
            (source_root / "two.lean").write_text("certificate\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", source_root], check=True)
            subprocess.run(["git", "-C", source_root, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", source_root, "config", "user.name", "Repo Search Test"], check=True)
            subprocess.run(["git", "-C", source_root, "add", "one.lean", "two.lean"], check=True)
            subprocess.run(["git", "-C", source_root, "commit", "-qm", "fixture"], check=True)
            generated = source_root / ".lake" / "packages" / "Fake.lean"
            generated.parent.mkdir(parents=True)
            generated.write_text("certificate trace moment\n", encoding="utf-8")
            (source_root / "scratch.lean").write_text("certificate trace moment\n", encoding="utf-8")

            artifact = root / "artifact"
            db = root / "index.sqlite"
            names = [
                "Zeta23.ZeroSide.TightMult.lemmaR_tight_two",
                "Zeta23.ZeroSide.TightMult.lemmaR_tight",
                "Zeta23.ZeroSide.RankTraceMult.rank_trace_mult_k",
                "Zeta23.ZeroSide.RankTraceMult.rank_trace_mult_k_le",
                "Zeta23.Assembly.count_certificate",
                "Zeta23.Assembly.N0star_lower_moment",
                "Zeta23.Hypotheses.ChebyshevMertens",
            ]
            source = load_lean_git_source(DESCRIPTOR)
            producer = ProducerPin(
                kind="lean-dep-viz",
                tool_repo=RUNNER.extractor_repo,
                tool_commit=RUNNER.extractor_commit,
                tool_hash=RUNNER.extractor_main_sha256,
            )
            nodes = [node(name) for name in names]
            edges = [
                edge(names[0], names[1]),
                edge(names[3], names[2]),
                edge(names[4], names[5]),
            ]
            raw = raw_depgraph_bytes(nodes, edges)
            write_artifact(
                artifact,
                nodes=nodes,
                edges=edges,
                sources=[
                    chunk("lemmaR_tight_two", "Tight.lean", "simples doubles tight pairs extremal\n", 1),
                    chunk("ChebyshevMertens", "Hypotheses.lean", "Chebyshev Mertens arithmetic interface\n", 1),
                    chunk("count_certificate", "Certificate.lean", "certificate trace moment\n", 1),
                ],
                source_repo="anthropics/formal-math",
                source_commit=COMMIT,
                source_subdir="zeta23",
                producer=producer,
                scope=ArtifactScope(root_modules=("Zeta23",), dependency_boundary="internal_only"),
                created_from_authoritative_commit=True,
                authority_receipt=receipt_bytes(source, producer, raw),
                raw_depgraph=raw,
            )
            build_projection(artifact, db)

            result = run_replay(db, artifact, DESCRIPTOR, source_root)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["provenance"], {
                "repo": "anthropics/formal-math",
                "commit": COMMIT,
                "subdir": "zeta23",
            })
            self.assertEqual(result["lexical"]["tight_query"], "tight pairs extremal")
            self.assertEqual(result["metrics"]["baseline_unique_paths"], 2)
            self.assertGreater(
                result["metrics"]["baseline_unique_paths"],
                result["metrics"]["fts_unique_paths"],
            )

            out = root / "replay.json"
            env = os.environ.copy()
            env["PYTHONPATH"] = "src:."
            proc = subprocess.run(
                [
                    sys.executable, "scripts/replay_zeta23.py",
                    "--db", str(db),
                    "--artifact", str(artifact),
                    "--descriptor", str(DESCRIPTOR),
                    "--source-root", str(source_root),
                    "--out", str(out),
                ],
                cwd=Path.cwd(), env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["provenance"]["repo"], "anthropics/formal-math")

    def test_stale_projection_is_rebuilt_for_selected_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, db = build_fixture(root)
            payload = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
            payload["source_commit"] = "d" * 40
            other_descriptor = root / "other-source.json"
            other_descriptor.write_text(json.dumps(payload), encoding="utf-8")
            other_source = load_lean_git_source(other_descriptor)
            other_artifact = write_fixture_artifact(root, other_source, "other-artifact")
            result = run_replay(db, other_artifact, other_descriptor, make_source_root(root))
            self.assertEqual(result["status"], "PASS")
            import sqlite3
            with sqlite3.connect(db) as conn:
                source_commit = dict(conn.execute("SELECT key, value FROM meta"))["source_commit"]
            self.assertEqual(source_commit, "d" * 40)

    def test_rejects_descriptor_provenance_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_fixture(root)
            payload = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
            payload["source_repo"] = "example/other-repo"
            descriptor = root / "mismatch.json"
            descriptor.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "artifact provenance/scope"):
                run_replay(db, artifact, descriptor, make_source_root(root))

    def test_rejects_descriptor_scope_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_fixture(root)
            payload = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
            payload["root_modules"] = ["DifferentRoot"]
            descriptor = root / "scope-mismatch.json"
            descriptor.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "artifact provenance/scope"):
                run_replay(db, artifact, descriptor, make_source_root(root))
    def test_artifact_only_replay_skips_source_scan_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_fixture(root)
            artifact_only = run_replay(db, artifact, DESCRIPTOR, None)
            self.assertFalse(artifact_only["metrics"]["baseline_checked"])
            self.assertIsNone(artifact_only["metrics"]["baseline_unique_paths"])
