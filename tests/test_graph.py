import tempfile
import unittest
from pathlib import Path

from theseus_repo_search.artifact import write_artifact
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.graph import dependencies, path, reverse_dependencies
from theseus_repo_search.model import ArtifactScope, Edge, EvidenceGrade, Node, ProducerPin
from theseus_repo_search.projection import build_projection


COMMIT = "abc123"
SCOPE = ArtifactScope(root_modules=("Zeta23",), dependency_boundary="internal_only")


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
        producer="LeanDepViz@deadbeef",
    )


class GraphTests(unittest.TestCase):
    def build_db(self, root: Path, *, ambiguous_a: bool = False, lexical_only: bool = False) -> Path:
        artifact = root / "artifact"
        db = root / "projection.db"
        if lexical_only:
            nodes = []
            edges = []
            producer = ProducerPin(
                kind="lexical_only",
                tool_repo="theseus-repo-search-lab",
                tool_commit="local",
                tool_hash="0" * 64,
            )
        else:
            nodes = [node(f"Zeta23.G.{name}") for name in ("A", "B", "C", "D")]
            if ambiguous_a:
                nodes.append(node("Zeta23.Other.A"))
            edges = [
                edge("Zeta23.G.A", "Zeta23.G.B"),
                edge("Zeta23.G.A", "Zeta23.G.D"),
                edge("Zeta23.G.B", "Zeta23.G.C"),
                edge("Zeta23.G.D", "Zeta23.G.C"),
                edge("Zeta23.G.C", "Zeta23.G.A"),
            ]
            producer = ProducerPin(
                kind="lean-dep-viz",
                tool_repo="cameronfreer/LeanDepViz",
                tool_commit="deadbeef",
                tool_hash="f" * 64,
            )
        write_artifact(
            artifact,
            nodes=nodes,
            edges=edges,
            sources=None,
            source_repo="example/repo",
            source_commit=COMMIT,
            source_subdir="",
            producer=producer,
            scope=SCOPE,
            created_from_authoritative_commit=True,
        )
        build_projection(artifact, db)
        return db

    def test_dependencies_are_depth_bounded_and_cycle_safe(self):
        with tempfile.TemporaryDirectory() as d:
            db = self.build_db(Path(d))
            depth1 = dependencies(db, "Zeta23.G.A", depth=1)
            self.assertEqual(
                [(e["source_id"], e["target_id"]) for e in depth1.edges],
                [
                    ("lean:Zeta23.G.A", "lean:Zeta23.G.B"),
                    ("lean:Zeta23.G.A", "lean:Zeta23.G.D"),
                ],
            )
            depth2 = dependencies(db, "lean:Zeta23.G.A", depth=2)
            self.assertEqual(len(depth2.edges), 4)
            depth3 = dependencies(db, "Zeta23.G.A", depth=3)
            self.assertEqual(len(depth3.edges), 5)
            self.assertEqual(max(e["depth"] for e in depth3.edges), 3)

    def test_reverse_dependencies_follow_incoming_edges(self):
        with tempfile.TemporaryDirectory() as d:
            db = self.build_db(Path(d))
            result = reverse_dependencies(db, "C", depth=2)
            pairs = [(e["source_id"], e["target_id"]) for e in result.edges]
            self.assertEqual(
                pairs,
                [
                    ("lean:Zeta23.G.B", "lean:Zeta23.G.C"),
                    ("lean:Zeta23.G.D", "lean:Zeta23.G.C"),
                    ("lean:Zeta23.G.A", "lean:Zeta23.G.B"),
                    ("lean:Zeta23.G.A", "lean:Zeta23.G.D"),
                ],
            )

    def test_path_returns_deterministic_shortest_path(self):
        with tempfile.TemporaryDirectory() as d:
            db = self.build_db(Path(d))
            result = path(db, "A", "C", max_depth=5)
            self.assertEqual(
                [(e["source_id"], e["target_id"]) for e in result.edges],
                [
                    ("lean:Zeta23.G.A", "lean:Zeta23.G.B"),
                    ("lean:Zeta23.G.B", "lean:Zeta23.G.C"),
                ],
            )

    def test_scope_metadata_is_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            db = self.build_db(Path(d))
            result = dependencies(db, "B")
            self.assertEqual(result.scope_root_modules, ("Zeta23",))
            self.assertEqual(result.dependency_boundary, "internal_only")
            self.assertTrue(result.complete_within_scope)

    def test_depth_above_v1_limit_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            db = self.build_db(Path(d))
            with self.assertRaises(RepoSearchError) as caught:
                dependencies(db, "A", depth=6)
            self.assertEqual(caught.exception.code, "UNKNOWN")
            self.assertEqual(str(caught.exception), "depth exceeds v1 maximum of 5")

    def test_ambiguous_short_name_is_unknown_with_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            db = self.build_db(Path(d), ambiguous_a=True)
            with self.assertRaises(RepoSearchError) as caught:
                dependencies(db, "A")
            self.assertEqual(caught.exception.code, "UNKNOWN")
            self.assertIn("lean:Zeta23.G.A", str(caught.exception))
            self.assertIn("lean:Zeta23.Other.A", str(caught.exception))

    def test_exact_graph_query_rejects_lexical_only_projection(self):
        with tempfile.TemporaryDirectory() as d:
            db = self.build_db(Path(d), lexical_only=True)
            with self.assertRaises(RepoSearchError) as caught:
                dependencies(db, "A")
            self.assertEqual(caught.exception.code, "UNAVAILABLE_EVIDENCE_GRADE")
