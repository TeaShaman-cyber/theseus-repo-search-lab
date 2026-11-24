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
