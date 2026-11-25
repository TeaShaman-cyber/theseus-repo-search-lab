import tempfile
import unittest
from pathlib import Path

from theseus_repo_search.artifact import write_artifact
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.graph import dependencies, path
from theseus_repo_search.model import ArtifactScope, EvidenceGrade, ProducerPin
from theseus_repo_search.olean_projection import normalize_olean_references
from theseus_repo_search.projection import build_projection
from theseus_repo_search.retrieval import search
from theseus_repo_search.sources import _chunks_from_text, bind_node_sources


class OleanProjectionTests(unittest.TestCase):
    def test_unique_graph_preserves_untyped_grade_and_marks_partial(self):
        rows = [
            {
                "module": "A",
                "name": "A.start",
                "kind": "theorem",
                "uses": ["B.end", "shared", "External.x"],
            },
            {"module": "B", "name": "B.end", "kind": "theorem", "uses": []},
            {"module": "A", "name": "shared", "kind": "theorem", "uses": ["B.end"]},
            {"module": "B", "name": "shared", "kind": "theorem", "uses": []},
        ]
        nodes, edges, report = normalize_olean_references(
            rows,
            source_commit="a" * 40,
            modules=("A", "B"),
            producer_ref="sdiehl/olean-export@pin",
        )
        self.assertEqual([n.full_name for n in nodes], ["A.start", "B.end"])
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].evidence_grade, EvidenceGrade.COMPILED_REFERENCE)
        self.assertEqual(report["ambiguous_names"], {"shared": ["A", "B"]})
        self.assertEqual(report["omitted_ambiguous_references"], 1)
        self.assertEqual(report["omitted_unresolved_references"], 1)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_artifact(
                root / "artifact",
                nodes=nodes,
                edges=edges,
                sources=[],
                source_repo="example/repo",
                source_commit="a" * 40,
                source_subdir="src",
                producer=ProducerPin(
                    "olean-export-deps-preview", "sdiehl/olean-export", "pin", "b" * 64
                ),
                scope=ArtifactScope(("A", "B"), "internal_only"),
                created_from_authoritative_commit=False,
            )
            build_projection(root / "artifact", root / "index.sqlite")
            self.assertEqual(
                search(root / "index.sqlite", "A.start")[0].declaration_id,
                "lean:A.start",
            )
            graph = dependencies(root / "index.sqlite", "A.start")
            self.assertFalse(graph.complete_within_scope)
            self.assertEqual(graph.edges[0]["relation"], "compiled_reference")
            self.assertFalse(
                path(root / "index.sqlite", "B.end", "A.start").complete_within_scope
            )

    def test_scope_summary_does_not_claim_full_listing(self):
        import hashlib
        import json

        from theseus_repo_search.cli import _compact_graph_scope

        roots = [f"M{i}" for i in range(1740)]
        result = _compact_graph_scope(
            {"scope_root_modules": roots, "complete_within_scope": False}
        )
        self.assertEqual(result["scope_root_module_count"], len(roots))
        self.assertEqual(result["scope_root_modules"], roots[:32])
        self.assertTrue(result["scope_root_modules_truncated"])
        self.assertEqual(
            result["scope_root_modules_sha256"],
            hashlib.sha256(
                json.dumps(roots, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        self.assertFalse(result["complete_within_scope"])

    def test_repeated_occurrence_is_rejected(self):
        row = {"module": "A", "name": "A.x", "kind": "theorem", "uses": []}
        with self.assertRaises(RepoSearchError):
            normalize_olean_references(
                [row, row], source_commit="a" * 40, modules=("A",), producer_ref="p"
            )

    def test_lexical_occurrence_does_not_bind_to_unrelated_surviving_name(self):
        rows = [
            {"module": "A", "name": "shared", "kind": "theorem", "uses": []},
            {"module": "B", "name": "shared", "kind": "theorem", "uses": []},
            {"module": "C", "name": "C.shared", "kind": "theorem", "uses": []},
        ]
        nodes, edges, _ = normalize_olean_references(
            rows,
            source_commit="a" * 40,
            modules=("A", "B", "C"),
            producer_ref="sdiehl/olean-export@pin",
        )
        chunks = _chunks_from_text(
            "A.lean",
            "theorem shared : True := by trivial -- alphawitness\n",
            source_revision="a" * 40,
        )
        chunks += _chunks_from_text(
            "C.lean", "theorem shared : True := by trivial\n", source_revision="a" * 40
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_artifact(
                root / "artifact",
                nodes=bind_node_sources(nodes, chunks),
                edges=edges,
                sources=chunks,
                source_repo="example/repo",
                source_commit="a" * 40,
                source_subdir="src",
                producer=ProducerPin(
                    "olean-export-deps-preview", "sdiehl/olean-export", "pin", "b" * 64
                ),
                scope=ArtifactScope(("A", "B", "C"), "internal_only"),
                created_from_authoritative_commit=False,
            )
            build_projection(root / "artifact", root / "index.sqlite")
            hit = search(root / "index.sqlite", "alphawitness")[0]
            self.assertEqual(hit.source_path, "A.lean")
            self.assertIsNone(hit.declaration_id)

    def test_preview_cannot_claim_authoritative_source(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(RepoSearchError):
            write_artifact(
                Path(d) / "artifact",
                nodes=[],
                edges=[],
                sources=[],
                source_repo="example/repo",
                source_commit="a" * 40,
                source_subdir="src",
                producer=ProducerPin(
                    "olean-export-deps-preview", "sdiehl/olean-export", "pin", "b" * 64
                ),
                scope=ArtifactScope(("A",), "internal_only"),
                created_from_authoritative_commit=True,
            )
