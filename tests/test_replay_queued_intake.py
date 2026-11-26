import json
import tempfile
import unittest
from pathlib import Path

from scripts.replay_queued_intake import run_replay
from theseus_repo_search.model import ProducerPin
from theseus_repo_search.producer_config import load_runner_pins
from tests.test_replay_ten_proofs_multicolor import DESCRIPTOR, TARGET, write_fixture


class QueuedReplayTests(unittest.TestCase):
    def test_exact_witness_and_wrong_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = write_fixture(root)
            witness = root / "witness.json"
            entry = {"declaration": TARGET, "path": "MulticolorTriangleRamsey.lean",
                     "corpus_class": "PROOF_CORPUS"}
            def save():
                witness.write_text(json.dumps({"openai-ten-proofs-multicolor": entry}))
            save()
            result = run_replay(root / "db.sqlite", artifact, DESCRIPTOR, witnesses=witness)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["proof_evidence"], "UNKNOWN_NOT_PROOF_EVIDENCE")
            entry["path"] = "Wrong.lean"
            save()
            with self.assertRaises(AssertionError):
                run_replay(root / "db.sqlite", artifact, DESCRIPTOR, witnesses=witness)

    def test_explicit_runner_pin_required_for_adapted_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Path("producer/runners/leandepviz-lean436-adapted.json")
            pin = load_runner_pins(runner)
            artifact = write_fixture(root, producer=ProducerPin(
                kind="lean-dep-viz", tool_repo=pin.extractor_repo,
                tool_commit=pin.extractor_commit, tool_hash=pin.extractor_main_sha256))
            witness = root / "witness.json"
            witness.write_text(json.dumps({"openai-ten-proofs-multicolor": {
                "declaration": TARGET, "path": "MulticolorTriangleRamsey.lean",
                "corpus_class": "PROOF_CORPUS"}}))
            with self.assertRaisesRegex(AssertionError, "producer pin"):
                run_replay(root / "db.sqlite", artifact, DESCRIPTOR, witnesses=witness)
            result = run_replay(root / "db.sqlite", artifact, DESCRIPTOR,
                                witnesses=witness, runner_path=runner)
            self.assertEqual(result["status"], "PASS")
