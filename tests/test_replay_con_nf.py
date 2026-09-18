import json
import sqlite3
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from scripts.replay_con_nf import run_replay
from theseus_repo_search.artifact import write_artifact
from theseus_repo_search.model import ArtifactScope, Edge, EvidenceGrade, Node, ProducerPin, SourceChunk
from theseus_repo_search.producer_config import load_lean_git_source, load_runner_pins
from theseus_repo_search.projection import build_projection
from tests.raw_fixture import raw_depgraph_bytes, receipt_bytes

DESCRIPTOR = Path("producer/sources/leanprover-community-con-nf.json")
RUNNER = load_runner_pins(Path("producer/runner.json"))


def node(full_name: str, module: str, commit: str) -> Node:
    return Node.from_lean(full_name=full_name, name=full_name.rsplit(".", 1)[-1], kind="thm", module=module, source_commit=commit)


def edge(source: str, target: str, producer_ref: str) -> Edge:
    return Edge(source_id=f"lean:{source}", target_id=f"lean:{target}", relation="value_dependency", evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY, producer=producer_ref)


def chunk(hint: str, text: str, path: str, line: int, commit: str) -> SourceChunk:
    end = line + text.count("\n") - 1
    return SourceChunk(id=f"src:{path}:{line}:{end}", source_commit=commit, source_path=path, source_start_line=line, source_end_line=end, declaration_hint=hint, text=text, content_sha256=sha256(text.encode()).hexdigest())


def write_fixture(root: Path, *, authoritative=True, producer=None, deps=("ConNF.subset'", "ConNF.TSet.exists_subset")) -> Path:
    source = load_lean_git_source(DESCRIPTOR)
    producer = producer or ProducerPin(kind="lean-dep-viz", tool_repo=RUNNER.extractor_repo, tool_commit=RUNNER.extractor_commit, tool_hash=RUNNER.extractor_main_sha256)
    producer_ref = f"{producer.tool_repo}@{producer.tool_commit}"
    names = ["ConNF.subset'_spec", "ConNF.subset'", "ConNF.TSet.exists_subset"]
    nodes = [
        node(names[0], "ConNF.Model.Result", source.source_commit),
        node(names[1], "ConNF.Model.Result", source.source_commit),
        node(names[2], "ConNF.Model.Hailperin", source.source_commit),
    ]
    edges = [edge(names[0], d, producer_ref) for d in deps]
    raw = raw_depgraph_bytes(nodes, edges)
    artifact = root / "artifact"
    write_artifact(
        artifact,
        nodes=nodes,
        edges=edges,
        sources=[
            chunk("subset'_spec", "theorem subset'_spec : True := by trivial\n", "ConNF/Model/Result.lean", 137, source.source_commit),
            chunk("subset'", "def subset' : True := True\n", "ConNF/Model/Result.lean", 134, source.source_commit),
            chunk("exists_subset", "theorem exists_subset : True := by trivial\n", "ConNF/Model/Hailperin.lean", 385, source.source_commit),
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


def build_fixture(root: Path):
    artifact = write_fixture(root)
    db = root / "index.sqlite"
    build_projection(artifact, db)
    return artifact, db


class ReplayConNfTests(unittest.TestCase):
    def test_valid_fixture_passes(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, db = build_fixture(Path(d))
            result = run_replay(db, artifact, DESCRIPTOR)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["provenance"]["repo"], "leanprover-community/con-nf")
            self.assertEqual(result["exact"]["target"], "ConNF.subset'_spec")

    def test_rejects_incomplete_direct_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root, deps=("ConNF.subset'",))
            db = root / "index.sqlite"; build_projection(artifact, db)
            with self.assertRaisesRegex(AssertionError, "required direct dependencies"):
                run_replay(db, artifact, DESCRIPTOR)

    def test_rejects_non_authoritative_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); artifact = write_fixture(root, authoritative=False); db = root / "index.sqlite"; build_projection(artifact, db)
            with self.assertRaisesRegex(AssertionError, "authoritative"):
                run_replay(db, artifact, DESCRIPTOR)

    def test_rejects_wrong_runner_producer_pin(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wrong = ProducerPin(kind="lean-dep-viz", tool_repo=RUNNER.extractor_repo, tool_commit="e" * 40, tool_hash=RUNNER.extractor_main_sha256)
            artifact = write_fixture(root, producer=wrong); db = root / "index.sqlite"; build_projection(artifact, db)
            with self.assertRaisesRegex(AssertionError, "producer"):
                run_replay(db, artifact, DESCRIPTOR)

    def test_replay_rebuilds_disposable_projection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); artifact, db = build_fixture(root)
            with sqlite3.connect(db) as conn:
                conn.execute("INSERT INTO nodes(id,name,kind,module,source_commit) VALUES (?,?,?,?,?)", ("lean:Injected.fake","fake","thm","Injected","bad")); conn.commit()
            self.assertEqual(run_replay(db, artifact, DESCRIPTOR)["status"], "PASS")
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM nodes WHERE id='lean:Injected.fake'").fetchone()[0], 0)

    def test_rejects_descriptor_provenance_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); artifact, db = build_fixture(root)
            payload = json.loads(DESCRIPTOR.read_text()); payload["source_repo"] = "example/other"
            desc = root / "bad.json"; desc.write_text(json.dumps(payload))
            with self.assertRaisesRegex(AssertionError, "provenance/scope"):
                run_replay(db, artifact, desc)
