import json
import unittest

from theseus_repo_search.anthropic_flt_static import parse_static_export
from theseus_repo_search.errors import RepoSearchError


def meta(names, root):
    return "window.FLT_META=" + json.dumps({"v": 1, "commit": "the Lean sources in this repository", "names": names, "root": root}, separators=(",", ":")) + ";\n"


def edges(off, dst):
    return "window.FLT_EDGES={off:" + json.dumps(off, separators=(",", ":")) + ",dst:" + json.dumps(dst, separators=(",", ":")) + "};\n"


class AnthropicFltStaticExportTests(unittest.TestCase):
    def test_builds_static_theorem_dependency_graph_without_lean(self):
        raw = parse_static_export(
            meta(["dep", "fermat_last_theorem"], 1),
            edges([0, 0, 1], [0]),
        )
        self.assertEqual(
            raw["nodes"],
            [
                {"module": "Theorems.Thm_dep", "fullName": "dep", "name": "dep", "kind": "theorem"},
                {"module": "Theorems.Thm_fermat_last_theorem", "fullName": "fermat_last_theorem", "name": "fermat_last_theorem", "kind": "theorem"},
            ],
        )
        self.assertEqual(
            raw["edges"],
            [{"source": "dep", "target": "fermat_last_theorem", "kind": "static"}],
        )

    def test_rejects_wrong_root_theorem(self):
        with self.assertRaises(RepoSearchError) as caught:
            parse_static_export(meta(["dep", "not_flt"], 1), edges([0, 0, 1], [0]))
        self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_rejects_inconsistent_offsets(self):
        with self.assertRaises(RepoSearchError) as caught:
            parse_static_export(meta(["dep", "fermat_last_theorem"], 1), edges([0, 1], [0]))
        self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_rejects_out_of_range_dependency_index(self):
        with self.assertRaises(RepoSearchError) as caught:
            parse_static_export(meta(["dep", "fermat_last_theorem"], 1), edges([0, 0, 1], [3]))
        self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_rejects_duplicate_theorem_names(self):
        with self.assertRaises(RepoSearchError) as caught:
            parse_static_export(meta(["fermat_last_theorem", "fermat_last_theorem"], 0), edges([0, 0, 0], []))
        self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
