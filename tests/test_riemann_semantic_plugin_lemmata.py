import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/riemann-semantic-plugin-lemmata.yml")
PLUGIN = Path("producer/lean/RepoSemanticPlugin.lean")


class RiemannSemanticPluginLemmataTests(unittest.TestCase):
    def test_plugin_uses_stdout_only_when_sidecar_transport_is_disabled(self):
        text = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("writeModuleSidecar (moduleName : Name) (fact : Json) : IO Bool", text)
        self.assertIn("return false", text)
        self.assertIn("return true", text)
        self.assertIn("unless wroteSidecar do", text)

    def test_plugin_tracks_environment_constant_delta_not_only_primary_syntax_name(self):
        text = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("registerStatefulLinter", text)
        self.assertIn("constAddedGens.foldlNewEntries", text)
        self.assertNotIn("declarationName?", text)
        self.assertNotIn("initialize addLinter", text)

    def test_workflow_uses_existing_lemmata_descriptor_and_lazy_cache_mapping(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("producer/pilots/riemann-hypothesis-lemmata.json", text)
        self.assertIn("lake cache get", text)
        self.assertIn("--mappings-only", text)
        self.assertIn('lake --file "$OVERLAY_PATH" build "$BUILD_TARGET"', text)
        self.assertNotIn("lake build Main", text)

    def test_workflow_requires_all_descriptor_roots_in_sidecar_fragments(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("missing = expected - observed", text)
        self.assertIn("missing semantic sidecars for descriptor roots", text)
        self.assertIn("plugin-fact-sidecars", text)
        self.assertIn("sidecar-metrics.json", text)

    def test_workflow_compares_against_bounded_leandepviz_and_uploads_evidence(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("LeanDepVizMain.lean", text)
        self.assertIn("--roots \"$ROOT_MODULES_CSV\"", text)
        self.assertIn("riemann_semantic_plugin_pilot.py compare", text)
        self.assertIn("--require-exact", text)
        self.assertIn("comparison.json", text)
        self.assertIn("pilot-metrics.json", text)
        self.assertIn("_out/plugin-fact-sidecars/*.jsonl", text)


if __name__ == "__main__":
    unittest.main()
