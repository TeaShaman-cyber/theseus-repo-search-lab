import json
import tempfile
import unittest
from pathlib import Path

from scripts.replay_queued_intake import run_replay
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
