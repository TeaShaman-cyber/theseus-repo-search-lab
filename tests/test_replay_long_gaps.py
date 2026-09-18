import json
import os
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from scripts.replay_long_gaps import run_replay
from theseus_repo_search.artifact import write_artifact
from theseus_repo_search.model import ArtifactScope, Edge, EvidenceGrade, Node, ProducerPin, SourceChunk
from theseus_repo_search.producer_config import load_lean_git_source, load_runner_pins
from theseus_repo_search.projection import build_projection


DESCRIPTOR = Path("producer/sources/openai-long-gaps.json")
RUNNER = load_runner_pins(Path("producer/runner.json"))


def node(full_name: str, commit: str) -> Node:
    return Node.from_lean(
        full_name=full_name,
        name=full_name.rsplit(".", 1)[-1],
        kind="thm",
        module=full_name.rsplit(".", 1)[0],
        source_commit=commit,
    )


def edge(source: str, target: str, producer_ref: str | None = None) -> Edge:
    return Edge(
        source_id=f"lean:{source}",
        target_id=f"lean:{target}",
        relation="value_dependency",
        evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
        producer=producer_ref or f"{RUNNER.extractor_repo}@{RUNNER.extractor_commit}",
    )


def chunk(hint: str, text: str, line: int, commit: str) -> SourceChunk:
    end = line + text.count("\n") - 1
    return SourceChunk(
        id=f"src:LongGapsBetweenPrimes.lean:{line}:{end}",
        source_commit=commit,
        source_path="LongGapsBetweenPrimes.lean",
        source_start_line=line,
        source_end_line=end,
        declaration_hint=hint,
        text=text,
        content_sha256=sha256(text.encode()).hexdigest(),
    )


def write_fixture_artifact(root: Path, source, name: str = "artifact", *, authoritative: bool = True, producer: ProducerPin | None = None) -> Path:
    artifact = root / name
    names = [
        "LongGapsBetweenPrimes.long_gap_theorem",
        "LongGapsBetweenPrimes.short_translates",
        "LongGapsBetweenPrimes.iteratedLog",
    ]
    producer = producer or ProducerPin(
        kind="lean-dep-viz",
        tool_repo=RUNNER.extractor_repo,
        tool_commit=RUNNER.extractor_commit,
        tool_hash=RUNNER.extractor_main_sha256,
    )
    producer_ref = f"{producer.tool_repo}@{producer.tool_commit}"
    write_artifact(
        artifact,
        nodes=[node(full_name, source.source_commit) for full_name in names],
        edges=[edge(names[0], names[1], producer_ref)],
        sources=[
            chunk(
                "long_gap_theorem",
                "/-- Theorem 1.1: the unconditional long-gap bound in the paper. -/\n"
                "theorem long_gap_theorem : True := by trivial\n",
                100,
                source.source_commit,
            ),
            chunk("short_translates", "lemma short_translates : True := by trivial\n", 120, source.source_commit),
            chunk("iteratedLog", "def iteratedLog : Nat := 0\n", 140, source.source_commit),
        ],
        source_repo=source.source_repo,
        source_commit=source.source_commit,
        source_subdir=source.source_subdir,
        producer=producer,
        scope=ArtifactScope(root_modules=source.root_modules, dependency_boundary="internal_only"),
        created_from_authoritative_commit=authoritative,
    )
    return artifact


def build_fixture(root: Path) -> tuple[Path, Path]:
    source = load_lean_git_source(DESCRIPTOR)
    artifact = write_fixture_artifact(root, source)
    db = root / "index.sqlite"
    build_projection(artifact, db)
    return artifact, db


class ReplayLongGapsTests(unittest.TestCase):
    def test_valid_fixture_passes_callable_and_script_entrypoint(self):
        source = load_lean_git_source(DESCRIPTOR)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_fixture(root)
            result = run_replay(db, artifact, DESCRIPTOR)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["provenance"]["repo"], source.source_repo)
            self.assertEqual(result["provenance"]["commit"], source.source_commit)
            self.assertEqual(result["exact"]["target"], "LongGapsBetweenPrimes.long_gap_theorem")
            self.assertGreater(result["graph"]["edge_count"], 0)
            self.assertGreater(len(result["context"]["chunks"]), 0)

            out = root / "receipt.json"
            source_root = root / "source"
            source_root.mkdir()
            env = os.environ.copy()
            env["PYTHONPATH"] = "src:."
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/replay_long_gaps.py",
                    "--db", str(db),
                    "--source-root", str(source_root),
                    "--artifact", str(artifact),
                    "--descriptor", str(DESCRIPTOR),
                    "--out", str(out),
                ],
                cwd=Path.cwd(),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.is_file())
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

    def test_rejects_projection_artifact_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, db = build_fixture(root)
            payload = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
            payload["source_commit"] = "d" * 40
            other_descriptor = root / "other-source.json"
            other_descriptor.write_text(json.dumps(payload), encoding="utf-8")
            other_source = load_lean_git_source(other_descriptor)
            other_artifact = write_fixture_artifact(root, other_source, "other-artifact")
            with self.assertRaisesRegex(AssertionError, "projection/artifact identity mismatch"):
                run_replay(db, other_artifact, other_descriptor)

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
