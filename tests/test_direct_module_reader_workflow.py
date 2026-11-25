import unittest
from pathlib import Path


class DirectModuleReaderWorkflowTests(unittest.TestCase):
    def test_hosted_regression_runs_pinned_native_reader_without_corpus_build(self):
        workflow = Path('.github/workflows/lean-direct-module-reader.yml').read_text()
        self.assertIn('pull_request:', workflow)
        self.assertIn('producer/lean/ReadModuleFacts.lean', workflow)
        self.assertIn('tools/dev/check-direct-module-reader', workflow)
        self.assertIn('producer/runner.json', workflow)
        self.assertIn('producer/pilots/riemann-hypothesis-full.json', workflow)
        self.assertIn('sha256sum --check', workflow)
        self.assertIn('${SOURCE_REPO}/${SOURCE_COMMIT}/lean-toolchain', workflow)
        self.assertNotIn('lake build', workflow)
        self.assertNotIn('lake env', workflow)

    def test_closed_module_replay_runs_against_verified_pinned_control(self):
        workflow = Path('.github/workflows/lean-direct-module-reader.yml').read_text()
        self.assertIn("- 'scripts/direct_module_replay.py'", workflow)
        self.assertIn("- 'tools/dev/check-closed-module-replay'", workflow)
        self.assertIn('${EXTRACTOR_REPO}/${EXTRACTOR_COMMIT}/LeanDepViz/Main.lean', workflow)
        self.assertIn('${EXTRACTOR_MAIN_SHA256}', workflow)
        self.assertIn('scripts/adapt_leandepviz_lean436.py', workflow)
        self.assertIn('EXTRACTOR_MAIN="$RUNNER_TEMP/LeanDepVizMain.lean" tools/dev/check-closed-module-replay', workflow)

    def test_dependency_inventory_uses_native_control_and_bounded_rh_pilot(self):
        native = Path('.github/workflows/lean-direct-module-reader.yml').read_text()
        self.assertIn("- 'producer/lean/ReadDependencyNames.lean'", native)
        pilot = Path('.github/workflows/riemann-dependency-inventory-pilot.yml').read_text()
        self.assertIn('restore_riemann_ci_cache_subset.py', pilot)
        self.assertIn('ReadDependencyNames.lean', pilot)
        self.assertIn('ReadModuleFacts.lean', pilot)
        self.assertIn('PairOKOfInterlace', pilot)
        self.assertIn('GeneralBFactSecondVersion.Imports', pilot)
        self.assertIn('dependency-names.jsonl.gz', pilot)
        self.assertIn('normalize_leandepviz', pilot)
        self.assertIn('retention-days: 30', pilot)
        self.assertNotIn('lake build', pilot)
        self.assertNotIn('lean_project_import_closure.lean', pilot)

    def test_upstream_deps_pilot_compares_without_rebuilding_graph(self):
        pilot = Path('.github/workflows/riemann-olean-deps-pilot.yml').read_text()
        self.assertIn('8318f1d4e4fc5b530d34ff1ee3d73321587565b9', pilot)
        self.assertIn('cargo +1.100.0 build --locked --release', pilot)
        self.assertIn('restore_riemann_ci_cache_subset.py', pilot)
        self.assertIn('olean-export" deps', pilot)
        self.assertIn('ReadModuleFacts.lean', pilot)
        self.assertIn('missing_edges', pilot)
        self.assertIn('extra_edges', pilot)
        self.assertNotIn('replay_closed_modules', pilot)
        self.assertNotIn('lake build', pilot)

    def test_upstream_deps_pilot_restores_project_closure_and_reports_scale(self):
        pilot = Path('.github/workflows/riemann-olean-deps-pilot.yml').read_text()
        self.assertIn('lean_project_import_closure.lean', pilot)
        self.assertIn('--modules-file _out/project-closure.txt', pilot)
        self.assertIn('MAX_PROJECT_MODULES: 4096', pilot)
        self.assertIn('project_cross_module_edges', pilot)
        self.assertIn('project_duplicate_names', pilot)
        self.assertIn('project_missing_modules', pilot)
        self.assertNotIn('replay_closed_modules', pilot)

    def test_headline_cone_requires_actual_compressed_graph_parity(self):
        pilot = Path('.github/workflows/riemann-theorem-cone.yml').read_text()
        self.assertIn('lean_project_import_closure.lean', pilot)
        self.assertIn('scripts/riemann_compressed_cache.py', pilot)
        self.assertIn('--modules-file "$root/_out/project-closure.txt"', pilot)
        self.assertIn('--constant riemannHypothesis', pilot)
        self.assertIn('8318f1d4e4fc5b530d34ff1ee3d73321587565b9', pilot)
        self.assertIn('--compare-plain', pilot)
        self.assertIn('GENERATED_DEPS_COMMAND_PASS', pilot)
        self.assertLess(pilot.index('GENERATED_DEPS_COMMAND_PASS'), pilot.index('Restore Mathlib cache'))
        self.assertIn('REAL_RH_COMPRESSED_GRAPH_PARITY_PASS', pilot)
        self.assertLess(pilot.index('Preserve control evidence'), pilot.index('Restore compressed Main closure'))
        self.assertIn('ReadModuleFacts.lean', pilot)
        self.assertIn('mount_cleanup_verified', pilot)
        self.assertNotIn('replay_closed_modules', pilot)
        self.assertNotIn('lake build', pilot)
