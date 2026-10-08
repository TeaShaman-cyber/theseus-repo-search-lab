"""Fail-closed Marton source/hosted-run/accepted-artifact evidence bridge."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.marton_axiom_witness import (
    ARTIFACT_IDENTITY,
    DECLARATION,
    SOURCE_SHA256,
    capture_marton_witness,
    consume_marton_witness,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_HEAD = "a" * 40
RUN_ID = "987654"
SOURCE_COMMIT = "e32eb1411db0d700ca874dd695aea92f78699db8"
RAW = f"'{DECLARATION}' depends on axioms: [sorryAx]\n".encode()


class MartonWitnessTests(unittest.TestCase):
    def _fixture(self, root: Path):
        source = root / "source"
        target = (
            source
            / "AnnalsChallenge/AnnalsOfMathematics/2025-201-2-ConjectureOfMarton.lean"
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(
            (ROOT / "tests/fixtures/lean_axioms/MartonSourcePinned.lean").read_bytes()
        )
        (source / "lean-toolchain").write_text("leanprover/lean4:v4.33.0-rc1\n")
        witness = root / "witness"

        class Proc:
            returncode = 0
            stderr = b""
            stdout = RAW

        with patch("scripts.marton_axiom_witness.subprocess.run", return_value=Proc()):
            capture_marton_witness(
                source_root=source,
                probe=ROOT / "tests/fixtures/lean_axioms/MartonProbe.lean",
                out=witness,
                repository="TeaShaman-cyber/theseus-repo-search-lab",
                github_head=RUN_HEAD,
                run_id=RUN_ID,
                run_attempt="1",
                lean_version="Lean (version 4.33.0-rc1, mocked-only)",
                lean_executable_sha256="b" * 64,
            )
        return witness

    def test_marton_hosted_workflow_is_one_source_not_matrix(self):
        text = (ROOT / ".github/workflows/lean-marton-axiom-witness.yml").read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("producer_guard.py checkout", text)
        self.assertIn("lake exe cache get", text)
        self.assertIn("lake build", text)
        self.assertIn("scripts/marton_axiom_witness.py capture", text)
        self.assertIn("actions/upload-artifact@", text)
        self.assertNotIn("strategy:\n      matrix:", text)
        self.assertNotIn("scripts/producer_guard.py extract-source", text)

    def test_witness_binds_source_and_hosted_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = self._fixture(root)
            payload = json.loads((p / "receipt.json").read_text())
            self.assertEqual(payload["artifact_identity"], ARTIFACT_IDENTITY)
            self.assertEqual(payload["source"]["sha256"], SOURCE_SHA256)
            self.assertEqual(payload["run"]["head"], RUN_HEAD)
            self.assertEqual(
                payload["execution"]["stdout_sha256"], hashlib.sha256(RAW).hexdigest()
            )

    def test_fresh_consumer_rejects_wrong_run_or_missing_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = self._fixture(root)
            with self.assertRaisesRegex(ValueError, "RUN_ID_MISMATCH"):
                consume_marton_witness(
                    p,
                    artifact=root / "missing",
                    expected_head=RUN_HEAD,
                    expected_run_id="1",
                    expected_run_attempt="1",
                    out=root / "result",
                )

    def test_witness_rejects_mismatched_original_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = self._fixture(root)
            payload = json.loads((p / "receipt.json").read_text())
            payload["source"]["sha256"] = "0" * 64
            (p / "receipt.json").write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "SOURCE_DIGEST_MISMATCH"):
                consume_marton_witness(
                    p,
                    artifact=root / "missing",
                    expected_head=RUN_HEAD,
                    expected_run_id=RUN_ID,
                    expected_run_attempt="1",
                    out=root / "result",
                )

    def test_witness_rejects_tampered_stdout_and_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = self._fixture(root)
            (p / "stdout.txt").write_bytes(RAW.replace(b"sorryAx", b"propext"))
            with self.assertRaisesRegex(ValueError, "STDOUT_DIGEST_MISMATCH"):
                consume_marton_witness(
                    p,
                    artifact=root / "missing",
                    expected_head=RUN_HEAD,
                    expected_run_id=RUN_ID,
                    expected_run_attempt="1",
                    out=root / "result",
                )
            (p / "stdout.txt").write_bytes(RAW)
            with self.assertRaisesRegex(ValueError, "RUN_ATTEMPT_MISMATCH"):
                consume_marton_witness(
                    p,
                    artifact=root / "missing",
                    expected_head=RUN_HEAD,
                    expected_run_id=RUN_ID,
                    expected_run_attempt="2",
                    out=root / "result",
                )

    def test_real_sidecar_requires_exact_artifact(self):
        # The synthetic witness is not independently hosted evidence; even a
        # perfectly matching transcript cannot be accepted without the artifact.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = self._fixture(root)
            with self.assertRaisesRegex(ValueError, "ARTIFACT_MISSING"):
                consume_marton_witness(
                    p,
                    artifact=root / "missing",
                    expected_head=RUN_HEAD,
                    expected_run_id=RUN_ID,
                    expected_run_attempt="1",
                    out=root / "result",
                )


if __name__ == "__main__":
    unittest.main()
