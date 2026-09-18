from dataclasses import replace
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from hashlib import sha256

from theseus_repo_search.artifact import (
    _write_artifact_contents,
    artifact_identity,
    load_artifact,
    write_artifact,
)
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.model import (
    ArtifactManifest,
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
            producer="cameronfreer/LeanDepViz@deadbeef",
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


def write_sample_raw(path: Path, *, nodes=None, edges=None, sources=_DEFAULT, scope=SCOPE):
    return _write_artifact_contents(
        path,
        nodes=sample_nodes() if nodes is None else nodes,
        edges=sample_edges() if edges is None else edges,
        sources=sample_sources() if sources is _DEFAULT else sources,
        source_repo="anthropics/formal-math",
        source_commit=SOURCE_COMMIT,
        source_subdir="zeta23",
        producer=PRODUCER,
        scope=scope,
        created_from_authoritative_commit=False,
    )


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
        created_from_authoritative_commit=False,
    )


class ArtifactTests(unittest.TestCase):
    def test_authoritative_artifact_without_receipt_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "artifact"
            with self.assertRaisesRegex(RepoSearchError, "authoritative artifact requires authority receipt"):
                write_artifact(
                    path,
                    nodes=sample_nodes(),
                    edges=sample_edges(),
                    sources=sample_sources(),
                    source_repo="anthropics/formal-math",
                    source_commit=SOURCE_COMMIT,
                    source_subdir="zeta23",
                    producer=PRODUCER,
                    scope=SCOPE,
                    created_from_authoritative_commit=True,
                )

    def test_authority_bit_participates_in_artifact_identity(self):
        manifest = ArtifactManifest(
            schema="theseus.repo-index.v1",
            source_repo="anthropics/formal-math",
            source_commit=SOURCE_COMMIT,
            source_subdir="zeta23",
            producer=PRODUCER,
            scope=SCOPE,
            nodes_sha256="n" * 64,
            edges_sha256="e" * 64,
            sources_sha256=None,
            nodes_count=1,
            edges_count=1,
            created_from_authoritative_commit=False,
            authority_receipt_sha256=None,
        )
        promoted = replace(manifest, created_from_authoritative_commit=True)
        self.assertNotEqual(artifact_identity(manifest), artifact_identity(promoted))

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

    def test_published_artifact_is_immutable_and_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "artifact"
            old_manifest = write_sample(path)
            old_identity = artifact_identity(old_manifest)
            extra = Node.from_lean(
                full_name="Zeta23.Tiny.c",
                name="c",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit=SOURCE_COMMIT,
            )

            with self.assertRaises(RepoSearchError) as caught:
                write_sample(path, nodes=sample_nodes() + [extra])
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("immutable", str(caught.exception))

            manifest, _, _, _ = load_artifact(path)
            self.assertEqual(artifact_identity(manifest), old_identity)

    def test_load_round_trips_written_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            manifest = write_sample(path)
            loaded_manifest, nodes, edges, sources = load_artifact(path)
            self.assertEqual(loaded_manifest, manifest)
            self.assertEqual([n.id for n in nodes], ["lean:Zeta23.Tiny.a", "lean:Zeta23.Tiny.b"])
            self.assertEqual(edges, sample_edges())
            self.assertEqual(sources, sample_sources())

    def test_authority_receipt_must_match_manifest_semantics(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            receipt = {
                "schema": "theseus.raw-depgraph-receipt.v2",
                "source": {"repo": "anthropics/formal-math", "commit": SOURCE_COMMIT, "subdir": "zeta23"},
                "scope": {"root_modules": ["Zeta23"]},
                "producer": {
                    "kind": PRODUCER.kind,
                    "tool_repo": PRODUCER.tool_repo,
                    "tool_commit": PRODUCER.tool_commit,
                    "tool_hash": PRODUCER.tool_hash,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.33.0"},
                "raw_depgraph": {"sha256": "0" * 64},
            }
            receipt_bytes = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
            write_artifact(
                path,
                nodes=sample_nodes(),
                edges=sample_edges(),
                sources=sample_sources(),
                source_repo="anthropics/formal-math",
                source_commit=SOURCE_COMMIT,
                source_subdir="zeta23",
                producer=PRODUCER,
                scope=SCOPE,
                created_from_authoritative_commit=True,
                authority_receipt=receipt_bytes,
            )
            receipt["source"]["commit"] = "wrong-commit"
            tampered = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
            (path / "authority-receipt.json").write_bytes(tampered)
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["members"]["authority_receipt"]["sha256"] = hashlib.sha256(tampered).hexdigest()
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RepoSearchError, "authority receipt"):
                load_artifact(path)

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
                producer="cameronfreer/LeanDepViz@deadbeef",
            )
            write_sample_raw(path, edges=[edge])
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
            write_sample_raw(path, nodes=nodes)
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
            write_sample_raw(path, sources=[bad])
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
            write_sample_raw(path, sources=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("source chunk content hash mismatch", str(caught.exception))

class StructuralIntegrityTests(unittest.TestCase):
    def test_duplicate_node_ids_are_blocked_before_projection(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            write_sample_raw(path, nodes=[nodes[0], nodes[0], nodes[1]])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("duplicate node id", str(caught.exception))

    def test_out_of_scope_node_is_blocked_for_internal_only_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes() + [
                Node.from_lean(
                    full_name="Mathlib.Algebra.outside",
                    name="outside",
                    kind="thm",
                    module="Mathlib.Algebra",
                    source_commit=SOURCE_COMMIT,
                )
            ]
            edges = sample_edges() + [
                Edge(
                    source_id="lean:Zeta23.Tiny.b",
                    target_id="lean:Mathlib.Algebra.outside",
                    relation="value_dependency",
                    evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                    producer="cameronfreer/LeanDepViz@deadbeef",
                )
            ]
            write_sample_raw(path, nodes=nodes, edges=edges)
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("node outside declared root-module scope", str(caught.exception))


    def test_node_source_location_must_match_unique_source_chunk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            bad = Node(
                id=nodes[0].id,
                full_name=nodes[0].full_name,
                name=nodes[0].name,
                kind=nodes[0].kind,
                module=nodes[0].module,
                source_path="Zeta23/Tiny.lean",
                source_start_line=1,
                source_end_line=1,
                source_commit=nodes[0].source_commit,
            )
            write_sample_raw(path, nodes=[bad, nodes[1]])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("node source location mismatch", str(caught.exception))

    def test_duplicate_short_names_cannot_swap_module_source_locations(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            text_a = "theorem foo : True := by trivial\n"
            text_b = "theorem foo : True := by trivial\n"
            sources = [
                SourceChunk(
                    id="src:Zeta23/A.lean:1:1",
                    source_commit=SOURCE_COMMIT,
                    source_path="Zeta23/A.lean",
                    source_start_line=1,
                    source_end_line=1,
                    declaration_hint="foo",
                    text=text_a,
                    content_sha256=sha256(text_a.encode()).hexdigest(),
                ),
                SourceChunk(
                    id="src:Zeta23/B.lean:1:1",
                    source_commit=SOURCE_COMMIT,
                    source_path="Zeta23/B.lean",
                    source_start_line=1,
                    source_end_line=1,
                    declaration_hint="foo",
                    text=text_b,
                    content_sha256=sha256(text_b.encode()).hexdigest(),
                ),
            ]
            nodes = [
                Node(
                    id="lean:Zeta23.A.foo",
                    full_name="Zeta23.A.foo",
                    name="foo",
                    kind="thm",
                    module="Zeta23.A",
                    source_path="Zeta23/B.lean",
                    source_start_line=1,
                    source_end_line=1,
                    source_commit=SOURCE_COMMIT,
                ),
                Node(
                    id="lean:Zeta23.B.foo",
                    full_name="Zeta23.B.foo",
                    name="foo",
                    kind="thm",
                    module="Zeta23.B",
                    source_path="Zeta23/A.lean",
                    source_start_line=1,
                    source_end_line=1,
                    source_commit=SOURCE_COMMIT,
                ),
            ]
            write_sample_raw(path, nodes=nodes, edges=[], sources=sources)
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("node source location mismatch", str(caught.exception))

    def test_partial_node_source_location_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            bad = Node(
                id=nodes[0].id,
                full_name=nodes[0].full_name,
                name=nodes[0].name,
                kind=nodes[0].kind,
                module=nodes[0].module,
                source_path="Zeta23/Tiny.lean",
                source_start_line=None,
                source_end_line=None,
                source_commit=nodes[0].source_commit,
            )
            write_sample_raw(path, nodes=[bad, nodes[1]])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("partial node source location", str(caught.exception))

    def test_noncanonical_lean_node_id_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            bad = Node(
                id="python:Zeta23.Tiny.b",
                full_name=nodes[0].full_name,
                name=nodes[0].name,
                kind=nodes[0].kind,
                module=nodes[0].module,
                source_path=nodes[0].source_path,
                source_start_line=nodes[0].source_start_line,
                source_end_line=nodes[0].source_end_line,
                source_commit=nodes[0].source_commit,
            )
            write_sample_raw(path, nodes=[bad, nodes[1]], edges=[])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("non-canonical Lean node id", str(caught.exception))

    def test_lean_node_id_must_match_full_declaration_identity(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            bad = replace(nodes[0], id="lean:Zeta23.Forged.not_b")
            write_sample_raw(path, nodes=[bad, nodes[1]], edges=[])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("node id/full-name mismatch", str(caught.exception))

    def test_edge_producer_must_match_manifest_pin(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = sample_edges()[0]
            bad = Edge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                relation=edge.relation,
                evidence_grade=edge.evidence_grade,
                producer="other/extractor@badc0de",
            )
            write_sample_raw(path, edges=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("edge producer mismatch", str(caught.exception))

    def test_duplicate_edge_rows_are_blocked_before_projection(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = sample_edges()[0]
            write_sample_raw(path, edges=[edge, edge])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("duplicate edge", str(caught.exception))

    def test_duplicate_source_ids_are_blocked_before_projection(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            source = sample_sources()[0]
            write_sample_raw(path, sources=[source, source])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("duplicate source id", str(caught.exception))

    def test_dependency_relation_must_match_evidence_grade(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = Edge(
                source_id="lean:Zeta23.Tiny.b",
                target_id="lean:Zeta23.Tiny.a",
                relation="value_dependency",
                evidence_grade=EvidenceGrade.ELABORATED_TYPE_DEPENDENCY,
                producer="cameronfreer/LeanDepViz@deadbeef",
            )
            write_sample_raw(path, edges=[edge])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("relation/evidence mismatch", str(caught.exception))

    def test_unknown_dependency_relation_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = Edge(
                source_id="lean:Zeta23.Tiny.b",
                target_id="lean:Zeta23.Tiny.a",
                relation="mystery_dependency",
                evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                producer="cameronfreer/LeanDepViz@deadbeef",
            )
            write_sample_raw(path, edges=[edge])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("unsupported dependency relation", str(caught.exception))

    def test_invalid_source_line_range_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            original = sample_sources()[0]
            bad = SourceChunk(
                id="src:Zeta23/Tiny.lean:2:1",
                source_commit=original.source_commit,
                source_path=original.source_path,
                source_start_line=2,
                source_end_line=1,
                declaration_hint=original.declaration_hint,
                text=original.text,
                content_sha256=original.content_sha256,
            )
            write_sample_raw(path, sources=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("invalid source range", str(caught.exception))

    def test_null_node_id_is_blocked_instead_of_coerced_to_string(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_sample(path)
            nodes_path = path / "nodes.jsonl"
            rows = [json.loads(line) for line in nodes_path.read_text(encoding="utf-8").splitlines()]
            rows[0]["id"] = None
            data = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
            nodes_path.write_text(data, encoding="utf-8")
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["members"]["nodes"]["sha256"] = hashlib.sha256(data.encode()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("invalid artifact record", str(caught.exception))
