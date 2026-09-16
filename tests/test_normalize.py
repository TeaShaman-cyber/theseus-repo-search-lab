import json
import unittest
from pathlib import Path

from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.model import EvidenceGrade
from theseus_repo_search.normalize import normalize_leandepviz


FIXTURE = Path(__file__).parent / "fixtures" / "raw_leandepviz.json"


class NormalizeLeanDepVizTests(unittest.TestCase):
    def load_fixture(self):
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_filters_external_nodes_and_inverts_native_edge_direction(self):
        nodes, edges = normalize_leandepviz(
            self.load_fixture(),
            source_commit="abc123",
            root_modules=("Zeta23",),
            producer_ref="LeanDepViz@7859d91",
        )
        self.assertEqual(
            [node.id for node in nodes],
            ["lean:Zeta23.Tiny.a", "lean:Zeta23.Tiny.b"],
        )
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].source_id, "lean:Zeta23.Tiny.b")
        self.assertEqual(edges[0].target_id, "lean:Zeta23.Tiny.a")
        self.assertEqual(edges[0].relation, "value_dependency")
        self.assertEqual(
            edges[0].evidence_grade,
            EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
        )

    def test_sorts_nodes_and_edges_and_removes_duplicate_edges(self):
        raw = self.load_fixture()
        raw["nodes"] = list(reversed(raw["nodes"]))
        raw["edges"].extend([
            {"source":"Zeta23.Tiny.a","target":"Zeta23.Tiny.b","kind":"value"},
            {"source":"Zeta23.Tiny.b","target":"Zeta23.Tiny.a","kind":"type"},
        ])
        nodes, edges = normalize_leandepviz(
            raw,
            source_commit="abc123",
            root_modules=("Zeta23",),
            producer_ref="LeanDepViz@7859d91",
        )
        self.assertEqual([n.id for n in nodes], sorted(n.id for n in nodes))
        self.assertEqual(len(edges), 2)
        self.assertEqual(
            [(e.source_id, e.target_id, e.relation, e.producer) for e in edges],
            sorted((e.source_id, e.target_id, e.relation, e.producer) for e in edges),
        )


    def test_non_list_collections_fail_closed(self):
        for key in ("nodes", "edges"):
            with self.subTest(key=key):
                raw = self.load_fixture()
                raw[key] = None
                with self.assertRaises(RepoSearchError) as caught:
                    normalize_leandepviz(
                        raw,
                        source_commit="abc123",
                        root_modules=("Zeta23",),
                        producer_ref="LeanDepViz@7859d91",
                    )
                self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_malformed_node_fields_fail_closed_without_string_coercion(self):
        raw = self.load_fixture()
        raw["nodes"] = [
            {"module": "Zeta23.Tiny", "fullName": None, "name": None, "kind": None}
        ]
        raw["edges"] = []
        with self.assertRaises(RepoSearchError) as caught:
            normalize_leandepviz(
                raw,
                source_commit="abc123",
                root_modules=("Zeta23",),
                producer_ref="LeanDepViz@7859d91",
            )
        self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
        self.assertIn("node[0].fullName", str(caught.exception))

    def test_duplicate_full_name_fails_closed(self):
        raw = self.load_fixture()
        raw["nodes"] = [raw["nodes"][0], dict(raw["nodes"][0])]
        raw["edges"] = []
        with self.assertRaises(RepoSearchError) as caught:
            normalize_leandepviz(
                raw,
                source_commit="abc123",
                root_modules=("Zeta23",),
                producer_ref="LeanDepViz@7859d91",
            )
        self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
        self.assertIn("duplicate LeanDepViz fullName", str(caught.exception))

    def test_malformed_edge_fields_fail_closed(self):
        raw = self.load_fixture()
        raw["edges"] = [{"source": None, "target": "Zeta23.Tiny.b", "kind": "value"}]
        with self.assertRaises(RepoSearchError) as caught:
            normalize_leandepviz(
                raw,
                source_commit="abc123",
                root_modules=("Zeta23",),
                producer_ref="LeanDepViz@7859d91",
            )
        self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
        self.assertIn("edge[0].source", str(caught.exception))

    def test_unknown_edge_kind_blocks_normalization(self):
        raw = self.load_fixture()
        raw["edges"] = [
            {"source":"Zeta23.Tiny.a","target":"Zeta23.Tiny.b","kind":"mystery"}
        ]
        with self.assertRaises(RepoSearchError) as caught:
            normalize_leandepviz(
                raw,
                source_commit="abc123",
                root_modules=("Zeta23",),
                producer_ref="LeanDepViz@7859d91",
            )
        self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
        self.assertIn("unknown LeanDepViz edge kind: mystery", str(caught.exception))
