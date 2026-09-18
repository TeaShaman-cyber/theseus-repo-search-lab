import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from scripts.replay_flt_regular import run_replay
from theseus_repo_search.artifact import write_artifact
from theseus_repo_search.model import ArtifactScope, Edge, EvidenceGrade, Node, ProducerPin, SourceChunk
from theseus_repo_search.producer_config import load_lean_git_source, load_runner_pins
from theseus_repo_search.projection import build_projection
from tests.raw_fixture import raw_depgraph_bytes, receipt_bytes


DESCRIPTOR = Path("producer/sources/leanprover-community-flt-regular.json")
RUNNER = load_runner_pins(Path("producer/runner.json"))


def node(full_name: str, module: str, commit: str) -> Node:
    return Node.from_lean(
        full_name=full_name,
        name=full_name.rsplit(".", 1)[-1],
        kind="thm",
        module=module,
        source_commit=commit,
    )


def edge(source: str, target: str, producer_ref: str) -> Edge:
    return Edge(
        source_id=f"lean:{source}",
        target_id=f"lean:{target}",
        relation="value_dependency",
        evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
        producer=producer_ref,
    )


def chunk(hint: str, text: str, path: str, line: int, commit: str) -> SourceChunk:
    end = line + text.count("\n") - 1
    return SourceChunk(
        id=f"src:{path}:{line}:{end}",
        source_commit=commit,
        source_path=path,
        source_start_line=line,
        source_end_line=end,
        declaration_hint=hint,
        text=text,
        content_sha256=sha256(text.encode()).hexdigest(),
    )


def write_fixture_artifact(
    root: Path,
    source,
    name: str = "artifact",
    *,
    authoritative: bool = True,
    producer: ProducerPin | None = None,
) -> Path:
    artifact = root / name
    producer = producer or ProducerPin(
        kind="lean-dep-viz",
        tool_repo=RUNNER.extractor_repo,
        tool_commit=RUNNER.extractor_commit,
        tool_hash=RUNNER.extractor_main_sha256,
    )
    producer_ref = f"{producer.tool_repo}@{producer.tool_commit}"
    nodes = [
        node("flt_regular", "FltRegular.FltRegular", source.source_commit),
        node("FltRegular.caseI", "FltRegular.CaseI.Statement", source.source_commit),
        node("FltRegular.caseII", "FltRegular.CaseII.Statement", source.source_commit),
        node("FltRegular.IsRegularPrime", "FltRegular.NumberTheory.RegularPrimes", source.source_commit),
    ]
    edges = [
        edge("flt_regular", "FltRegular.caseI", producer_ref),
        edge("flt_regular", "FltRegular.caseII", producer_ref),
    ]
    raw = raw_depgraph_bytes(nodes, edges)
    write_artifact(
        artifact,
        nodes=nodes,
        edges=edges,
        sources=[
            chunk(
                "flt_regular",
                "/-- Fermat's last theorem for regular primes. -/\n"
                "theorem flt_regular : True := by trivial\n",
                "FltRegular/FltRegular.lean",
                18,
                source.source_commit,
            ),
            chunk("caseI", "theorem caseI : True := by trivial\n", "FltRegular/CaseI/Statement.lean", 1, source.source_commit),
            chunk("caseII", "theorem caseII : True := by trivial\n", "FltRegular/CaseII/Statement.lean", 1, source.source_commit),
        ],
        source_repo=source.source_repo,
        source_commit=source.source_commit,
        source_subdir=source.source_subdir,
        producer=producer,
        scope=ArtifactScope(root_modules=source.root_modules, dependency_boundary="internal_only"),
        created_from_authoritative_commit=authoritative,
        authority_receipt=receipt_bytes(source, producer, raw) if authoritative else None,
        raw_depgraph=raw if authoritative else None,
    )
    return artifact


def build_fixture(root: Path) -> tuple[Path, Path]:
    source = load_lean_git_source(DESCRIPTOR)
    artifact = write_fixture_artifact(root, source)
    db = root / "index.sqlite"
    build_projection(artifact, db)
    return artifact, db


class ReplayFltRegularTests(unittest.TestCase):
    def test_valid_fixture_passes_callable_and_script_entrypoint(self):
        source = load_lean_git_source(DESCRIPTOR)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_fixture(root)
            result = run_replay(db, artifact, DESCRIPTOR)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["provenance"]["repo"], source.source_repo)
            self.assertEqual(result["provenance"]["commit"], source.source_commit)
            self.assertEqual(result["exact"]["target"], "flt_regular")
            self.assertGreater(result["graph"]["edge_count"], 0)
            self.assertGreater(len(result["context"]["chunks"]), 0)

            out = root / "receipt.json"
            env = os.environ.copy()
            env["PYTHONPATH"] = "src:."
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/replay_flt_regular.py",
                    "--db", str(db),
                    "--artifact", str(artifact),
                    "--descriptor", str(DESCRIPTOR),
                    "--out", str(out),
                ],
                cwd=Path.cwd(), env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["status"], "PASS")

    def test_rejects_non_authoritative_artifact(self):
        source = load_lean_git_source(DESCRIPTOR)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture_artifact(root, source, authoritative=False)
            db = root / "index.sqlite"
            build_projection(artifact, db)
            with self.assertRaisesRegex(AssertionError, "authoritative"):
                run_replay(db, artifact, DESCRIPTOR)

    def test_rejects_wrong_runner_producer_pin(self):
        source = load_lean_git_source(DESCRIPTOR)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wrong = ProducerPin(
                kind="lean-dep-viz",
                tool_repo=RUNNER.extractor_repo,
                tool_commit="d" * 40,
                tool_hash=RUNNER.extractor_main_sha256,
            )
            artifact = write_fixture_artifact(root, source, producer=wrong)
            db = root / "index.sqlite"
            build_projection(artifact, db)
            with self.assertRaisesRegex(AssertionError, "producer"):
                run_replay(db, artifact, DESCRIPTOR)

    def test_replay_rebuilds_disposable_projection_from_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_fixture(root)
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO nodes(id, name, kind, module, source_commit) VALUES (?, ?, ?, ?, ?)",
                    ("lean:Injected.fake", "fake", "thm", "Injected", "not-from-artifact"),
                )
                conn.commit()
            result = run_replay(db, artifact, DESCRIPTOR)
            self.assertEqual(result["status"], "PASS")
            with sqlite3.connect(db) as conn:
                count = conn.execute("SELECT count(*) FROM nodes WHERE id='lean:Injected.fake'").fetchone()[0]
            self.assertEqual(count, 0)

    def test_rejects_descriptor_provenance_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_fixture(root)
            payload = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
            payload["source_repo"] = "example/other-repo"
            descriptor = root / "mismatch.json"
            descriptor.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "artifact provenance/scope"):
                run_replay(db, artifact, descriptor)

    def test_rejects_descriptor_scope_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_fixture(root)
            payload = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
            payload["root_modules"] = ["DifferentRoot"]
            descriptor = root / "scope-mismatch.json"
            descriptor.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "artifact provenance/scope"):
                run_replay(db, artifact, descriptor)
