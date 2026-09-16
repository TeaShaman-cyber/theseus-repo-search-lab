import tempfile
import unittest
from hashlib import sha256
from math import ceil
from pathlib import Path

from theseus_repo_search.artifact import write_artifact
from theseus_repo_search.model import (
    ArtifactScope,
    Edge,
    EvidenceGrade,
    Node,
    ProducerPin,
    SourceChunk,
)
from theseus_repo_search.projection import build_projection
from theseus_repo_search.retrieval import context, search


COMMIT = "abc123"


class RetrievalTests(unittest.TestCase):
    def build_db(
        self, root: Path, *, authoritative: bool = True
    ) -> tuple[Path, str]:
        artifact = root / "artifact"
        db = root / "projection.db"
        target_text = (
            "/-- Equality-family witness for rank trace tightness and ξ symmetry. -/\n"
            "lemma lemmaR_tight_two : True := by trivial\n"
        )
        neighbor_text = (
            "/-- Moment lower bound used by the certificate. -/\n"
            "theorem N0star_lower_moment : True := by trivial\n"
        )
        nodes = [
            Node.from_lean(
                full_name="Zeta23.Tiny.lemmaR_tight_two",
                name="lemmaR_tight_two",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit=COMMIT,
            ),
            Node.from_lean(
                full_name="Zeta23.Tiny.N0star_lower_moment",
                name="N0star_lower_moment",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit=COMMIT,
            ),
        ]
        edges = [
            Edge(
                source_id="lean:Zeta23.Tiny.lemmaR_tight_two",
                target_id="lean:Zeta23.Tiny.N0star_lower_moment",
                relation="value_dependency",
                evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                producer="LeanDepViz@deadbeef",
            )
        ]
        sources = [
            SourceChunk(
                id="src:Zeta23/Tiny.lean:1:2",
                source_commit=COMMIT,
                source_path="Zeta23/Tiny.lean",
                source_start_line=1,
                source_end_line=2,
                declaration_hint="lemmaR_tight_two",
                text=target_text,
                content_sha256=sha256(target_text.encode()).hexdigest(),
            ),
            SourceChunk(
                id="src:Zeta23/Tiny.lean:3:4",
                source_commit=COMMIT,
                source_path="Zeta23/Tiny.lean",
                source_start_line=3,
                source_end_line=4,
                declaration_hint="N0star_lower_moment",
                text=neighbor_text,
                content_sha256=sha256(neighbor_text.encode()).hexdigest(),
            ),
        ]
        write_artifact(
            artifact,
            nodes=nodes,
            edges=edges,
            sources=sources,
            source_repo="example/repo",
            source_commit=COMMIT,
            source_subdir="",
            producer=ProducerPin(
                kind="lean-dep-viz",
                tool_repo="cameronfreer/LeanDepViz",
                tool_commit="deadbeef",
                tool_hash="f" * 64,
            ),
            scope=ArtifactScope(
                root_modules=("Zeta23",),
                dependency_boundary="internal_only",
            ),
            created_from_authoritative_commit=authoritative,
        )
        build_projection(artifact, db)
        return db, target_text

    def test_exact_identifier_prefers_declaration_and_preserves_source_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = self.build_db(Path(d))
            hits = search(db, "lemmaR_tight_two")
            self.assertEqual(hits[0].declaration_id, "lean:Zeta23.Tiny.lemmaR_tight_two")
            self.assertEqual(hits[0].declaration_hint, "lemmaR_tight_two")
            self.assertEqual(hits[0].source_path, "Zeta23/Tiny.lean")
            self.assertEqual(hits[0].evidence_grade, EvidenceGrade.LEXICAL_HIT)

    def test_query_results_preserve_authoritative_readback_attestation(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = self.build_db(Path(d), authoritative=False)
            hits = search(db, "lemmaR_tight_two")
            self.assertFalse(hits[0].created_from_authoritative_commit)
            result = context(db, "lemmaR_tight_two", depth=1, token_budget=30)
            self.assertFalse(result["created_from_authoritative_commit"])

    def test_lexical_query_finds_role_description(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = self.build_db(Path(d))
            hits = search(db, "rank trace tightness")
            self.assertEqual(hits[0].declaration_hint, "lemmaR_tight_two")
            self.assertEqual(hits[0].evidence_grade, EvidenceGrade.LEXICAL_HIT)
            self.assertIn("rank trace tightness", hits[0].text)

    def test_unicode_query_reaches_fts5_unicode61(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = self.build_db(Path(d))
            hits = search(db, "ξ")
            self.assertTrue(hits)
            self.assertEqual(hits[0].declaration_hint, "lemmaR_tight_two")

    def test_no_hit_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = self.build_db(Path(d))
            self.assertEqual(search(db, "definitely_not_in_corpus"), [])

    def test_context_respects_budget_and_reports_scope(self):
        with tempfile.TemporaryDirectory() as d:
            db, target_text = self.build_db(Path(d))
            result = context(db, "lemmaR_tight_two", depth=1, token_budget=30)
            self.assertEqual(result["dependency_boundary"], "internal_only")
            self.assertEqual(result["scope_root_modules"], ["Zeta23"])
            self.assertTrue(result["complete_within_scope"])
            self.assertEqual(result["token_estimate_method"], "ceil(chars/4)")
            self.assertLessEqual(result["estimated_tokens"], 30)
            self.assertEqual(result["estimated_tokens"], ceil(len(target_text) / 4))
            self.assertEqual(len(result["chunks"]), 1)
            self.assertEqual(result["chunks"][0]["declaration_hint"], "lemmaR_tight_two")
            self.assertEqual(result["chunks"][0]["distance"], 0)
