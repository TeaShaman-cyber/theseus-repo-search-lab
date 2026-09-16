import json
import tempfile
import unittest
from pathlib import Path
from hashlib import sha256

from theseus_repo_search.artifact import artifact_identity, load_artifact, write_artifact
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.model import (
    ArtifactScope,
    Edge,
    EvidenceGrade,
    Node,
    ProducerPin,
    SourceChunk,
)


SOURCE_COMMIT = "abc123"
PRODUCER = ProducerPin(
    kind="lean-dep-viz",
    tool_repo="cameronfreer/LeanDepViz",
    tool_commit="deadbeef",
    tool_hash="f" * 64,
)
SCOPE = ArtifactScope(root_modules=("Zeta23",), dependency_boundary="internal_only")


def sample_nodes():
    return [
        Node.from_lean(
            full_name="Zeta23.Tiny.b",
            name="b",
            kind="thm",
            module="Zeta23.Tiny",
            source_commit=SOURCE_COMMIT,
        ),
        Node.from_lean(
            full_name="Zeta23.Tiny.a",
            name="a",
            kind="thm",
            module="Zeta23.Tiny",
            source_commit=SOURCE_COMMIT,
        ),
    ]


def sample_edges():
    return [
        Edge(
            source_id="lean:Zeta23.Tiny.b",
            target_id="lean:Zeta23.Tiny.a",
            relation="value_dependency",
            evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
            producer="LeanDepViz@deadbeef",
        )
    ]


def sample_sources():
    text = "lemma a : True := by trivial\n"
    return [
        SourceChunk(
            id="src:Zeta23/Tiny.lean:1:1",
            source_commit=SOURCE_COMMIT,
            source_path="Zeta23/Tiny.lean",
            source_start_line=1,
            source_end_line=1,
            declaration_hint="a",
            text=text,
            content_sha256=sha256(text.encode()).hexdigest(),
        )
    ]


_DEFAULT = object()


def write_sample(path: Path, *, nodes=None, edges=None, sources=_DEFAULT, scope=SCOPE):
    return write_artifact(
        path,
        nodes=sample_nodes() if nodes is None else nodes,
        edges=sample_edges() if edges is None else edges,
        sources=sample_sources() if sources is _DEFAULT else sources,
        source_repo="anthropics/formal-math",
        source_commit=SOURCE_COMMIT,
        source_subdir="zeta23",
        producer=PRODUCER,
        scope=scope,
        created_from_authoritative_commit=True,
    )


class ArtifactTests(unittest.TestCase):
    def test_write_is_deterministic_across_input_order(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            p1, p2 = Path(d1), Path(d2)
            m1 = write_sample(p1)
            m2 = write_sample(
                p2,
                nodes=list(reversed(sample_nodes())),
                edges=list(reversed(sample_edges())),
                sources=list(reversed(sample_sources())),
            )
            self.assertEqual((p1 / "nodes.jsonl").read_bytes(), (p2 / "nodes.jsonl").read_bytes())
            self.assertEqual((p1 / "edges.jsonl").read_bytes(), (p2 / "edges.jsonl").read_bytes())
            self.assertEqual((p1 / "sources.jsonl").read_bytes(), (p2 / "sources.jsonl").read_bytes())
            self.assertEqual(artifact_identity(m1), artifact_identity(m2))

    def test_load_round_trips_written_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            manifest = write_sample(path)
            loaded_manifest, nodes, edges, sources = load_artifact(path)
            self.assertEqual(loaded_manifest, manifest)
            self.assertEqual([n.id for n in nodes], ["lean:Zeta23.Tiny.a", "lean:Zeta23.Tiny.b"])
            self.assertEqual(edges, sample_edges())
            self.assertEqual(sources, sample_sources())

    def test_tampered_member_hash_blocks_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_sample(path)
            (path / "edges.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_missing_edge_endpoint_blocks_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = Edge(
                source_id="lean:Zeta23.Tiny.b",
                target_id="lean:Zeta23.Tiny.missing",
                relation="value_dependency",
                evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                producer="LeanDepViz@deadbeef",
            )
            write_sample(path, edges=[edge])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_wrong_node_source_commit_blocks_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            nodes[0] = Node.from_lean(
                full_name="Zeta23.Tiny.b",
                name="b",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit="wrong",
            )
            write_sample(path, nodes=nodes)
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_manifest_schema_and_boundary_are_verified(self):
        for field, value in (("schema", "wrong.schema"), ("boundary", "full_environment")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as d:
                path = Path(d)
                write_sample(path)
                manifest_path = path / "manifest.json"
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if field == "schema":
                    data["schema"] = value
                else:
                    data["scope"]["dependency_boundary"] = value
                manifest_path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(RepoSearchError) as caught:
                    load_artifact(path)
                self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_none_sources_omits_optional_member_and_loads_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            manifest = write_sample(path, sources=None)
            self.assertFalse((path / "sources.jsonl").exists())
            self.assertIsNone(manifest.sources_sha256)
            _, _, _, sources = load_artifact(path)
            self.assertEqual(sources, [])

class SourceIntegrityTests(unittest.TestCase):
    def test_source_commit_must_match_manifest_commit(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            original = sample_sources()[0]
            bad = SourceChunk(
                id=original.id,
                source_commit="wrong",
                source_path=original.source_path,
                source_start_line=original.source_start_line,
                source_end_line=original.source_end_line,
                declaration_hint=original.declaration_hint,
                text=original.text,
                content_sha256=original.content_sha256,
            )
            write_sample(path, sources=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("source chunk commit mismatch", str(caught.exception))

    def test_source_content_hash_must_match_text(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            original = sample_sources()[0]
            bad = SourceChunk(
                id=original.id,
                source_commit=original.source_commit,
                source_path=original.source_path,
                source_start_line=original.source_start_line,
                source_end_line=original.source_end_line,
                declaration_hint=original.declaration_hint,
                text=original.text,
                content_sha256="0" * 64,
            )
            write_sample(path, sources=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("source chunk content hash mismatch", str(caught.exception))
