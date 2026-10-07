import tempfile
import unittest
from dataclasses import asdict, replace
from hashlib import sha256
from math import ceil
from pathlib import Path
from unittest.mock import patch

from theseus_repo_search.artifact import write_archive_artifact_v2, write_artifact
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.graph import GraphResult
from theseus_repo_search.model import (
    ArchiveAuthority,
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
        self, root: Path, *, authoritative: bool = False
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
                producer="cameronfreer/LeanDepViz@deadbeef",
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

    def test_v2_retrieval_exposes_generic_archive_provenance_without_git_terms(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = root / "artifact-v2"
            db = root / "projection-v2.db"
            revision = "a" * 64
            text = "theorem ok : True := by trivial -- archivequery\n"
            node = replace(
                Node.from_lean(
                    full_name="Pkg.Main.ok", name="ok", kind="thm",
                    module="Pkg.Main", source_commit=revision,
                ),
                source_path="Pkg/Main.lean", source_start_line=1, source_end_line=1,
            )
            source = SourceChunk(
                id="src:Pkg/Main.lean:1:1",
                source_commit=revision,
                source_path="Pkg/Main.lean",
                source_start_line=1,
                source_end_line=1,
                declaration_hint="ok",
                text=text,
                content_sha256=sha256(text.encode()).hexdigest(),
            )
            authority = ArchiveAuthority(
                url="https://example.invalid/source.zip",
                sha256=revision,
                format="zip",
                subdir=".",
            )
            write_archive_artifact_v2(
                artifact,
                nodes=[node], edges=[], sources=[source],
                source_authority=authority,
                producer=ProducerPin(
                    kind="lean-dep-viz",
                    tool_repo="cameronfreer/LeanDepViz",
                    tool_commit="deadbeef", tool_hash="f" * 64,
                ),
                scope=ArtifactScope(root_modules=("Pkg",), dependency_boundary="internal_only"),
            )
            build_projection(artifact, db)
            hit = search(db, "archivequery")[0]
            payload = asdict(hit)

            self.assertEqual(payload["source_kind"], "archive")
            self.assertEqual(payload["source_revision"], revision)
            self.assertEqual(
                payload["source_authority"],
                {
                    "kind": "archive",
                    "url": authority.url,
                    "sha256": authority.sha256,
                    "format": authority.format,
                    "subdir": authority.subdir,
                },
            )
            self.assertFalse(payload["created_from_authoritative_source"])
            self.assertNotIn("source_commit", payload)
            self.assertNotIn("created_from_authoritative_commit", payload)

            result = context(db, "ok", depth=1, token_budget=100)
            self.assertEqual(result["source_kind"], "archive")
            self.assertEqual(result["source_revision"], revision)
            self.assertEqual(
                result["source_authority"],
                {
                    "kind": "archive",
                    "url": authority.url,
                    "sha256": authority.sha256,
                    "format": authority.format,
                    "subdir": authority.subdir,
                },
            )
            self.assertFalse(result["created_from_authoritative_source"])
            self.assertNotIn("created_from_authoritative_commit", result)
            self.assertNotIn("source_commit", result["chunks"][0])
            self.assertEqual(result["chunks"][0]["source_revision"], revision)

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

    def test_multiterm_discovery_keeps_weak_or_hits_but_evidence_requires_all_terms(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = root / "artifact"
            db = root / "projection.db"
            truth_text = "theorem truth_only : True := by trivial -- truth\n"
            predicate_text = "theorem predicate_only : True := by trivial -- predicate\n"
            sources = [
                SourceChunk(
                    id="src:Pkg/Truth.lean:1:1",
                    source_commit=COMMIT,
                    source_path="Pkg/Truth.lean",
                    source_start_line=1,
                    source_end_line=1,
                    declaration_hint="truth_only",
                    text=truth_text,
                    content_sha256=sha256(truth_text.encode()).hexdigest(),
                ),
                SourceChunk(
                    id="src:Pkg/Predicate.lean:1:1",
                    source_commit=COMMIT,
                    source_path="Pkg/Predicate.lean",
                    source_start_line=1,
                    source_end_line=1,
                    declaration_hint="predicate_only",
                    text=predicate_text,
                    content_sha256=sha256(predicate_text.encode()).hexdigest(),
                ),
            ]
            write_artifact(
                artifact,
                nodes=[],
                edges=[],
                sources=sources,
                source_repo="example/repo",
                source_commit=COMMIT,
                source_subdir="",
                producer=ProducerPin(
                    kind="lexical_only",
                    tool_repo="TeaShaman-cyber/theseus-repo-search-lab",
                    tool_commit="deadbeef",
                    tool_hash="f" * 64,
                ),
                scope=ArtifactScope(root_modules=("Pkg",), dependency_boundary="internal_only"),
                created_from_authoritative_commit=False,
            )
            build_projection(artifact, db)

            discovery = search(db, "truth predicate")
            self.assertEqual(len(discovery), 2)
            self.assertTrue(all(hit.query_mode == "discovery" for hit in discovery))
            self.assertTrue(all(hit.match_mode == "any_terms" for hit in discovery))

            evidence = search(db, "truth predicate", mode="evidence")
            self.assertEqual(evidence, [])

    def test_unicode_query_reaches_fts5_unicode61(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = self.build_db(Path(d))
            hits = search(db, "ξ")
            self.assertTrue(hits)
            self.assertEqual(hits[0].declaration_hint, "lemmaR_tight_two")

    def test_lexical_hit_uses_source_location_to_resolve_duplicate_short_name(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = root / "artifact"
            db = root / "projection.db"
            text_a = "theorem foo : True := by trivial -- alphaunique\n"
            text_b = "theorem foo : True := by trivial -- betaunique\n"
            sources = [
                SourceChunk(
                    id="src:Zeta23/A.lean:1:1",
                    source_commit=COMMIT,
                    source_path="Zeta23/A.lean",
                    source_start_line=1,
                    source_end_line=1,
                    declaration_hint="foo",
                    text=text_a,
                    content_sha256=sha256(text_a.encode()).hexdigest(),
                ),
                SourceChunk(
                    id="src:Zeta23/B.lean:1:1",
                    source_commit=COMMIT,
                    source_path="Zeta23/B.lean",
                    source_start_line=1,
                    source_end_line=1,
                    declaration_hint="foo",
                    text=text_b,
                    content_sha256=sha256(text_b.encode()).hexdigest(),
                ),
            ]
            nodes = [
                replace(
                    Node.from_lean(
                        full_name="Zeta23.A.foo", name="foo", kind="thm",
                        module="Zeta23.A", source_commit=COMMIT,
                    ),
                    source_path="Zeta23/A.lean", source_start_line=1, source_end_line=1,
                ),
                replace(
                    Node.from_lean(
                        full_name="Zeta23.B.foo", name="foo", kind="thm",
                        module="Zeta23.B", source_commit=COMMIT,
                    ),
                    source_path="Zeta23/B.lean", source_start_line=1, source_end_line=1,
                ),
            ]
            write_artifact(
                artifact,
                nodes=nodes,
                edges=[],
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
                scope=ArtifactScope(root_modules=("Zeta23",), dependency_boundary="internal_only"),
                created_from_authoritative_commit=False,
            )
            build_projection(artifact, db)
            hits = search(db, "alphaunique")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].declaration_id, "lean:Zeta23.A.foo")

    def test_unlocated_node_does_not_bind_global_short_name_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = root / "artifact"
            db = root / "projection.db"
            foreign_text = "theorem foo : True := by trivial -- belongs to B\n"
            write_artifact(
                artifact,
                nodes=[
                    Node.from_lean(
                        full_name="Zeta23.A.foo",
                        name="foo",
                        kind="thm",
                        module="Zeta23.A",
                        source_commit=COMMIT,
                    )
                ],
                edges=[],
                sources=[
                    SourceChunk(
                        id="src:Zeta23/B.lean:1:1",
                        source_commit=COMMIT,
                        source_path="Zeta23/B.lean",
                        source_start_line=1,
                        source_end_line=1,
                        declaration_hint="foo",
                        text=foreign_text,
                        content_sha256=sha256(foreign_text.encode()).hexdigest(),
                    )
                ],
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
                    root_modules=("Zeta23",), dependency_boundary="internal_only"
                ),
                created_from_authoritative_commit=False,
            )
            build_projection(artifact, db)
            hit = search(db, "Zeta23.A.foo")[0]
            self.assertEqual(hit.declaration_id, "lean:Zeta23.A.foo")
            self.assertIsNone(hit.source_path)
            self.assertIsNone(hit.text)

    def test_no_hit_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = self.build_db(Path(d))
            self.assertEqual(search(db, "definitely_not_in_corpus"), [])

    def test_context_rejects_non_integer_graph_depth_as_projection_integrity_error(self):
        with tempfile.TemporaryDirectory() as d:
            db, _ = self.build_db(Path(d))
            malformed = GraphResult(
                query="deps:lemmaR_tight_two",
                edges=(
                    {
                        "source_id": "lean:Zeta23.Tiny.lemmaR_tight_two",
                        "target_id": "lean:Zeta23.Tiny.N0star_lower_moment",
                        "relation": "value_dependency",
                        "evidence_grade": EvidenceGrade.ELABORATED_VALUE_DEPENDENCY.value,
                        "producer": "cameronfreer/LeanDepViz@deadbeef",
                        "depth": "not-an-int",
                    },
                ),
                scope_root_modules=("Zeta23",),
                dependency_boundary="internal_only",
                complete_within_scope=True,
                source_kind="git",
                source_revision=COMMIT,
                source_authority=None,
                created_from_authoritative_source=False,
                found=True,
            )

            with patch(
                "theseus_repo_search.retrieval.dependencies", return_value=malformed
            ), self.assertRaises(RepoSearchError) as caught:
                context(db, "lemmaR_tight_two", depth=1, token_budget=30)

            self.assertEqual(caught.exception.code, "BLOCKED_PROJECTION_INTEGRITY")
            self.assertIn("graph edge depth", str(caught.exception))

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
