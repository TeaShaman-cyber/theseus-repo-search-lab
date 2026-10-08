"""Fixed historical provider evidence; these tests do not attest hosted origin."""
from __future__ import annotations

import hashlib
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "qa/evidence/marton-theorem-1-2-v0"


class MartonEvidenceSnapshotTests(unittest.TestCase):
    def test_exact_provider_zip_is_preserved_with_declared_external_digest(self):
        provenance = json.loads((E / "provenance.json").read_text())
        raw = (E / "marton-axiom-witness.zip").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), provenance["provider_artifact"]["sha256"])
        self.assertEqual(provenance["provider_artifact"]["id"], 11554180914)
        self.assertEqual(provenance["run"]["id"], 37784601371)
        self.assertEqual(provenance["run"]["attempt"], 1)
        self.assertEqual(provenance["run"]["head_sha"], "b41bd07d876f42e818d121c8b466bc77024e8903")
        with zipfile.ZipFile(E / "marton-axiom-witness.zip") as archive:
            self.assertEqual(sorted(archive.namelist()), ["receipt.json", "stderr.txt", "stdout.txt"])
            witness = json.loads(archive.read("receipt.json"))
            self.assertEqual(witness["run"]["head"], provenance["run"]["head_sha"])
            self.assertEqual(witness["run"]["id"], str(provenance["run"]["id"]))
            self.assertEqual(witness["run"]["attempt"], str(provenance["run"]["attempt"]))
            self.assertEqual(witness["source"]["sha256"], provenance["source"]["sha256"])
            stdout = archive.read("stdout.txt")
            self.assertEqual(stdout, (E / "axiom-report.txt").read_bytes())
            self.assertEqual(archive.read("stderr.txt"), b"")
            self.assertEqual(hashlib.sha256(stdout).hexdigest(), witness["execution"]["stdout_sha256"])

    def test_report_is_bound_to_accepted_statement_corpus_not_proof(self):
        provenance = json.loads((E / "provenance.json").read_text())
        sidecar = json.loads((E / "sidecar.json").read_text())
        result = json.loads((E / "verification.json").read_text())
        self.assertEqual(sidecar["artifact"]["identity"], provenance["accepted_artifact_identity"])
        self.assertEqual(sidecar["artifact"]["source_revision"], provenance["source"]["commit"])
        self.assertEqual(sidecar["corpus_class"], "STATEMENT_CORPUS")
        self.assertEqual(sidecar["declaration"], "ConjectureOfMarton.theorem_1_2")
        self.assertIn("sorryAx", sidecar["lean_audit"]["observed_axioms"])
        self.assertEqual(sidecar["lean_audit"]["toolchain"], provenance["source"]["toolchain"])
        self.assertEqual(
            sidecar["lean_audit"]["transcript_sha256"],
            hashlib.sha256((E / "axiom-report.txt").read_bytes()).hexdigest(),
        )
        self.assertEqual(result["proof_eligibility"], "NOT_PROOF_EVIDENCE")
        self.assertEqual(provenance["proof_eligibility"], "NOT_PROOF_EVIDENCE")
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["sidecar_sha256"], hashlib.sha256((E / "sidecar.json").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
