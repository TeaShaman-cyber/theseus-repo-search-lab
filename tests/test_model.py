import unittest
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.model import Edge, EvidenceGrade, Node


class ModelTests(unittest.TestCase):
    def test_evidence_grades_are_stable_strings(self):
        self.assertEqual(
            EvidenceGrade.ELABORATED_VALUE_DEPENDENCY.value,
            "ELABORATED_VALUE_DEPENDENCY",
        )
        self.assertEqual(EvidenceGrade.LEXICAL_HIT.value, "LEXICAL_HIT")

    def test_node_lean_id_uses_canonical_full_name(self):
        node = Node.from_lean(
            full_name="Zeta23.Tiny.a",
            name="a",
            kind="thm",
            module="Zeta23.Tiny",
            source_commit="abc123",
        )
        self.assertEqual(node.id, "lean:Zeta23.Tiny.a")
        self.assertIsNone(node.source_start_line)

    def test_repo_search_error_keeps_machine_code(self):
        err = RepoSearchError("BLOCKED_ARTIFACT_INTEGRITY", "hash mismatch")
        self.assertEqual(err.code, "BLOCKED_ARTIFACT_INTEGRITY")
        self.assertEqual(str(err), "hash mismatch")

    def test_edge_serializes_grade_as_string(self):
        edge = Edge(
            source_id="lean:A",
            target_id="lean:B",
            relation="value_dependency",
            evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
            producer="LeanDepViz@deadbeef",
        )
        self.assertEqual(
            edge.to_dict()["evidence_grade"],
            "ELABORATED_VALUE_DEPENDENCY",
        )

class ManifestTests(unittest.TestCase):
    def test_manifest_round_trips_nested_scope_and_producer(self):
        from theseus_repo_search.model import ArtifactManifest, ArtifactScope, ProducerPin

        manifest = ArtifactManifest(
            schema="theseus.repo-index.v1",
            source_repo="anthropics/formal-math",
            source_commit="abc123",
            source_subdir="zeta23",
            producer=ProducerPin(
                kind="lean-dep-viz",
                tool_repo="cameronfreer/LeanDepViz",
                tool_commit="deadbeef",
                tool_hash="012345",
            ),
            scope=ArtifactScope(
                root_modules=("Zeta23",),
                dependency_boundary="internal_only",
            ),
            nodes_sha256="n" * 64,
            edges_sha256="e" * 64,
            sources_sha256=None,
            nodes_count=1,
            edges_count=2,
            created_from_authoritative_commit=True,
        )

        self.assertEqual(ArtifactManifest.from_dict(manifest.to_dict()), manifest)

    def test_manifest_rejects_string_boolean_authority_attestation(self):
        from theseus_repo_search.model import ArtifactManifest, ArtifactScope, ProducerPin

        manifest = ArtifactManifest(
            schema="theseus.repo-index.v1",
            source_repo="anthropics/formal-math",
            source_commit="abc123",
            source_subdir="zeta23",
            producer=ProducerPin(
                kind="lean-dep-viz",
                tool_repo="cameronfreer/LeanDepViz",
                tool_commit="deadbeef",
                tool_hash="012345",
            ),
            scope=ArtifactScope(
                root_modules=("Zeta23",),
                dependency_boundary="internal_only",
            ),
            nodes_sha256="n" * 64,
            edges_sha256="e" * 64,
            sources_sha256=None,
            nodes_count=1,
            edges_count=2,
            created_from_authoritative_commit=True,
        )
        payload = manifest.to_dict()
        payload["created_from_authoritative_commit"] = "false"
        with self.assertRaises(TypeError):
            ArtifactManifest.from_dict(payload)

class SerializationTests(unittest.TestCase):
    def test_node_to_dict_preserves_null_source_range(self):
        node = Node.from_lean(
            full_name="Zeta23.Tiny.a",
            name="a",
            kind="thm",
            module="Zeta23.Tiny",
            source_commit="abc123",
        )
        self.assertEqual(node.to_dict()["source_start_line"], None)
        self.assertEqual(node.to_dict()["id"], "lean:Zeta23.Tiny.a")

    def test_source_chunk_to_dict_preserves_provenance(self):
        from theseus_repo_search.model import SourceChunk

        chunk = SourceChunk(
            id="source:abc123:Zeta23/Tiny.lean:1-2",
            source_commit="abc123",
            source_path="Zeta23/Tiny.lean",
            source_start_line=1,
            source_end_line=2,
            declaration_hint="Zeta23.Tiny.a",
            text="theorem a : True := by trivial\n",
            content_sha256="f" * 64,
        )
        data = chunk.to_dict()
        self.assertEqual(data["source_path"], "Zeta23/Tiny.lean")
        self.assertEqual(data["declaration_hint"], "Zeta23.Tiny.a")
