"""Host witness receipt binding tests; synthetic transports do not attest Lean."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.lean_axiom_witness import capture_witness, consume_witness
from theseus_repo_search.evidence_class import ProofEligibility

SOURCE = Path(__file__).parent / "fixtures" / "lean_axioms" / "AxiomProbe.lean"
RAW = (SOURCE.parent / "actual-lean-4.33.0-rc2.stdout").read_bytes()
SHA = "d92d2482c12a116d254feba6e09c275dab183e4a3bf9d1ebf6aa3d48260342b7"
COMMIT = "a" * 40
RUN = "11777"
FAKE_BINARY_SHA256 = hashlib.sha256(b"fixture-only pretend executable").hexdigest()


class WitnessTests(unittest.TestCase):
    def _capture(self, root: Path):
        class Command:
            def __init__(self, code, out, err=b""):
                self.returncode = code
                self.stdout = out
                self.stderr = err

        def fake_run(args, **kwargs):
            if args[-1] == "--version":
                return Command(0, b"Lean (version 4.33.0-rc2, x86_64-unknown-linux-gnu)\n")
            self.assertEqual(Path(args[-1]).resolve(), SOURCE.resolve())
            return Command(0, RAW)

        fake_binary = root / "mock-lean"
        fake_binary.write_bytes(b"fixture-only pretend executable")
        with patch("scripts.lean_axiom_witness.subprocess.run", side_effect=fake_run):
            capture_witness(
                source=SOURCE, output_dir=root, lean_executable=fake_binary,
                repository="TeaShaman-cyber/theseus-repo-search-lab", source_commit=COMMIT,
                run_id=RUN, run_attempt="1",
            )

    def test_capture_and_fresh_consumer_preserve_sorry_negative_control(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._capture(root)
            receipt = json.loads((root / "receipt.json").read_text())
            self.assertEqual(receipt["source"]["sha256"], SHA)
            self.assertEqual(receipt["execution"]["stdout_sha256"], hashlib.sha256(RAW).hexdigest())
            result = consume_witness(root, source=SOURCE, expected_commit=COMMIT, expected_run_id=RUN, expected_run_attempt="1", expected_lean_binary_sha256=FAKE_BINARY_SHA256)
            self.assertEqual(result["EvidenceClassCanary.fully_proved"], ProofEligibility.CANDIDATE_FOR_PROOF_VERIFICATION)
            self.assertEqual(result["EvidenceClassCanary.admitted"], ProofEligibility.NOT_PROOF_EVIDENCE)

    def test_fresh_consumer_rejects_wrong_external_run_or_commit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._capture(root)
            for commit, run in (("b" * 40, RUN), (COMMIT, "99888")):
                with self.subTest(commit=commit, run=run), self.assertRaises(ValueError):
                    consume_witness(root, source=SOURCE, expected_commit=commit, expected_run_id=run, expected_run_attempt="1", expected_lean_binary_sha256=FAKE_BINARY_SHA256)

    def test_fresh_consumer_rejects_raw_stdout_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._capture(root)
            (root / "stdout.txt").write_bytes(RAW.replace(b"[sorryAx]", b"[]"))
            with self.assertRaises(ValueError):
                consume_witness(root, source=SOURCE, expected_commit=COMMIT, expected_run_id=RUN, expected_run_attempt="1", expected_lean_binary_sha256=FAKE_BINARY_SHA256)

    def test_fresh_consumer_rejects_re_pinned_synthetic_clean_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._capture(root)
            fake = RAW.replace(b"[sorryAx]", b"[propext]")
            (root / "stdout.txt").write_bytes(fake)
            receipt_path = root / "receipt.json"
            payload = json.loads(receipt_path.read_text())
            payload["execution"]["stdout_sha256"] = hashlib.sha256(fake).hexdigest()
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            # Self-consistent metadata is not independent proof; this test
            # protects known toy output ONLY. Run provenance remains external.
            with self.assertRaises(ValueError):
                consume_witness(root, source=SOURCE, expected_commit=COMMIT, expected_run_id=RUN, expected_run_attempt="1", expected_lean_binary_sha256=FAKE_BINARY_SHA256)

    def test_fresh_consumer_rejects_wrong_binary_pin(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._capture(root)
            with self.assertRaisesRegex(ValueError, "LEAN_BINARY_DIGEST_MISMATCH"):
                consume_witness(root, source=SOURCE, expected_commit=COMMIT,
                                expected_run_id=RUN, expected_run_attempt="1", expected_lean_binary_sha256="0" * 64)

    def test_fresh_consumer_rejects_previous_workflow_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._capture(root)
            with self.assertRaisesRegex(ValueError, "RUN_ATTEMPT_MISMATCH"):
                consume_witness(
                    root, source=SOURCE, expected_commit=COMMIT,
                    expected_run_id=RUN, expected_run_attempt="2",
                    expected_lean_binary_sha256=FAKE_BINARY_SHA256,
                )

    def test_fresh_consumer_rejects_missing_receipt_or_unknown_toolchain(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._capture(root)
            receipt_path = root / "receipt.json"
            payload = json.loads(receipt_path.read_text())
            payload["execution"]["lean_version"] = "Lean (version unknown)"
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                consume_witness(root, source=SOURCE, expected_commit=COMMIT, expected_run_id=RUN, expected_run_attempt="1", expected_lean_binary_sha256=FAKE_BINARY_SHA256)
            receipt_path.unlink()
            with self.assertRaises((ValueError, OSError)):
                consume_witness(root, source=SOURCE, expected_commit=COMMIT, expected_run_id=RUN, expected_run_attempt="1", expected_lean_binary_sha256=FAKE_BINARY_SHA256)


if __name__ == "__main__":
    unittest.main()
