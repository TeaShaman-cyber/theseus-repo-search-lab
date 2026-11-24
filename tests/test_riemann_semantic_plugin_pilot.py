import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.riemann_semantic_plugin_pilot import (
    compare_plugin_facts_to_leandepviz,
    load_fact_sidecar_dir,
    make_overlay,
    parse_fact_log,
)

WORKFLOW = Path(".github/workflows/riemann-semantic-plugin-pilot.yml")


class RiemannSemanticPluginPilotTests(unittest.TestCase):
    def test_make_overlay_adds_plugin_without_rewriting_other_config(self):
        canonical = (
            'name = "tree"\n'
            'srcDir = "src"\n'
            'moreLeanArgs = ["--tstack=400000"]\n'
            '\n'
            '[[lean_lib]]\n'
            'name = "Challenge"\n'
        )

        overlay = make_overlay(canonical, "/tmp/RepoSemanticPlugin.so")

        self.assertEqual(
            overlay,
            canonical.replace(
                'moreLeanArgs = ["--tstack=400000"]',
                'moreLeanArgs = ["--tstack=400000", "--plugin=/tmp/RepoSemanticPlugin.so"]',
            ),
        )

    def test_make_overlay_fails_closed_when_package_args_shape_changes(self):
        with self.assertRaisesRegex(ValueError, "expected exactly one package moreLeanArgs"):
            make_overlay('name = "tree"\n', "/tmp/plugin.so")

    def test_parse_fact_log_accepts_lake_prefixed_and_continuation_lines(self):
        a = {
            "schema": "theseus.lean-semantic-fact.v1",
            "module": "Challenge",
            "full_name": "riemannZeta_eq_zero_re",
            "kind": "theorem",
            "type_dependencies": ["RiemannZetaZerosOnLine"],
            "value_dependencies": ["sorryAx"],
        }
        b = {
            "schema": "theseus.lean-semantic-fact.v1",
            "module": "Challenge",
            "full_name": "RiemannZetaZerosOnLine",
            "kind": "definition",
            "type_dependencies": ["Prop"],
            "value_dependencies": [],
        }
        log = (
            "info: src/Challenge.lean:1:0: THESEUS_FACT\t"
            + json.dumps(a, separators=(",", ":"))
            + "\n"
            + "THESEUS_FACT\t"
            + json.dumps(b, separators=(",", ":"))
            + "\n"
        )

        facts = parse_fact_log(log)

        self.assertEqual([fact["full_name"] for fact in facts], [
            "RiemannZetaZerosOnLine",
            "riemannZeta_eq_zero_re",
        ])

    def test_parse_fact_log_rejects_conflicting_duplicate_declaration(self):
        first = {
            "schema": "theseus.lean-semantic-fact.v1",
            "module": "Challenge",
            "full_name": "x",
            "kind": "definition",
            "type_dependencies": [],
            "value_dependencies": [],
        }
        second = dict(first, kind="theorem")
        log = "\n".join(
            "THESEUS_FACT\t" + json.dumps(row, separators=(",", ":"))
            for row in (first, second)
        )

        with self.assertRaisesRegex(ValueError, "conflicting semantic fact"):
            parse_fact_log(log)

    def test_compare_normalizes_leandepviz_edge_orientation_and_internal_scope(self):
        facts = [
            {
                "schema": "theseus.lean-semantic-fact.v1",
                "module": "Challenge",
                "full_name": "RiemannZetaZerosOnLine",
                "kind": "definition",
                "type_dependencies": [],
                "value_dependencies": [],
            },
            {
                "schema": "theseus.lean-semantic-fact.v1",
                "module": "Challenge",
                "full_name": "riemannZeta_eq_zero_re",
                "kind": "theorem",
                "type_dependencies": ["RiemannZetaZerosOnLine", "Prop"],
                "value_dependencies": ["sorryAx"],
            },
        ]
        raw = {
            "nodes": [
                {
                    "module": "Challenge",
                    "fullName": "Challenge.RiemannZetaZerosOnLine",
                    "name": "RiemannZetaZerosOnLine",
                    "kind": "def",
                },
                {
                    "module": "Challenge",
                    "fullName": "Challenge.riemannZeta_eq_zero_re",
                    "name": "riemannZeta_eq_zero_re",
                    "kind": "theorem",
                },
            ],
            "edges": [
                {
                    "kind": "type",
                    "source": "RiemannZetaZerosOnLine",
                    "target": "riemannZeta_eq_zero_re",
                }
            ],
        }

        result = compare_plugin_facts_to_leandepviz(
            facts,
            raw,
            source_commit="a" * 40,
            root_modules=("Challenge",),
        )

        self.assertEqual(result["plugin_internal_edges"], 1)
        self.assertEqual(result["leandepviz_internal_edges"], 1)
        self.assertEqual(result["common_edges"], 1)
        self.assertEqual(result["plugin_only_edges"], [])
        self.assertEqual(result["leandepviz_only_edges"], [])


class RiemannSemanticPluginPilotWorkflowTests(unittest.TestCase):
    def test_workflow_uses_two_mathlib_only_rh_modules_and_no_rh_cache_restore(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Challenge", text)
        self.assertIn(
            "CompanionTheory.AutomorphicTheory.BesselWaveGrids.InterlacingGrids.PairOKOfInterlace",
            text,
        )
        self.assertIn("mathlib/cache get", text)
        self.assertNotIn("restore_riemann_ci_cache_subset.py", text)
        self.assertNotIn("lake build Main", text)

    def test_workflow_builds_source_transparent_plugin_overlay_and_verifies_source_bytes(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("RepoSemanticPlugin.lean", text)
        self.assertIn("riemann_semantic_plugin_pilot.py overlay", text)
        self.assertIn("--file", text)
        self.assertIn("--plugin=", text)
        self.assertIn('git -C "$SOURCE_ROOT" diff --exit-code', text)
        self.assertIn("sha256sum --check", text)

    def test_plugin_build_runs_inside_runner_temp_root(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        step = text.split("- name: Build Lean 4.36 semantic plugin", 1)[1].split(
            "- name: Generate external producer overlay", 1
        )[0]
        self.assertIn('cd "$RUNNER_TEMP"', step)
        self.assertIn('lean -c RepoSemanticPlugin.c RepoSemanticPlugin.lean', step)
        self.assertIn('leanc', step)
        self.assertNotIn('lean \\n            -c "$RUNNER_TEMP/RepoSemanticPlugin.c"', step)

    def test_workflow_compares_same_slice_with_leandepviz_and_uploads_metrics(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("LeanDepVizMain.lean", text)
        self.assertIn("riemann_semantic_plugin_pilot.py compare", text)
        self.assertIn("comparison.json", text)
        self.assertIn("pilot-metrics.json", text)


class RiemannSemanticSidecarTransportTests(unittest.TestCase):
    def _fact(self, module: str, full_name: str):
        return {
            "schema": "theseus.lean-semantic-fact.v1",
            "module": module,
            "full_name": full_name,
            "kind": "theorem",
            "type_dependencies": [],
            "value_dependencies": [],
        }

    def test_sidecar_loader_merges_modules_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "B.Mod.jsonl").write_text(
                json.dumps(self._fact("B.Mod", "B.Mod.z")) + "\n",
                encoding="utf-8",
            )
            (root / "A.Mod.jsonl").write_text(
                json.dumps(self._fact("A.Mod", "A.Mod.a")) + "\n",
                encoding="utf-8",
            )

            facts, fragments = load_fact_sidecar_dir(root)

        self.assertEqual(
            [(row["module"], row["full_name"]) for row in facts],
            [("A.Mod", "A.Mod.a"), ("B.Mod", "B.Mod.z")],
        )
        self.assertEqual([row["module"] for row in fragments], ["A.Mod", "B.Mod"])
        self.assertTrue(all(len(row["sha256"]) == 64 for row in fragments))

    def test_sidecar_loader_canonicalizes_fragment_before_hashing(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "A.jsonl"
            path.write_text(
                json.dumps(self._fact("A", "z")) + "\n"
                + json.dumps(self._fact("A", "a")) + "\n",
                encoding="utf-8",
            )

            facts, fragments = load_fact_sidecar_dir(root)
            canonical = path.read_bytes()

        self.assertEqual([row["full_name"] for row in facts], ["a", "z"])
        lines = canonical.decode("utf-8").splitlines()
        self.assertEqual([json.loads(line)["full_name"] for line in lines], ["a", "z"])
        self.assertEqual(fragments[0]["sha256"], hashlib.sha256(canonical).hexdigest())

    def test_sidecar_loader_rejects_filename_module_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Wrong.jsonl").write_text(
                json.dumps(self._fact("Right", "Right.x")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "sidecar module mismatch"):
                load_fact_sidecar_dir(root)

    def test_sidecar_loader_rejects_duplicate_declaration_across_fragments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fact = self._fact("A", "shared")
            (root / "A.jsonl").write_text(json.dumps(fact) + "\n", encoding="utf-8")
            other = dict(fact, module="B")
            (root / "B.jsonl").write_text(json.dumps(other) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate declaration across sidecars"):
                load_fact_sidecar_dir(root)

    def test_workflow_uses_sidecar_transport_not_build_log_as_fact_source(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("THESEUS_REPO_FACT_DIR", text)
        self.assertIn("riemann_semantic_plugin_pilot.py sidecars", text)
        self.assertIn("_out/plugin-fact-sidecars", text)
        self.assertNotIn("riemann_semantic_plugin_pilot.py facts \\\n            --log _out/plugin-build.log", text)

    def test_plugin_uses_locked_append_for_module_sidecars(self):
        plugin = Path("producer/lean/RepoSemanticPlugin.lean").read_text(encoding="utf-8")
        self.assertIn("THESEUS_REPO_FACT_DIR", plugin)
        self.assertIn("IO.FS.Handle.mk", plugin)
        self.assertIn(".append", plugin)
        self.assertIn("h.lock (exclusive := true)", plugin)

if __name__ == "__main__":
    unittest.main()
