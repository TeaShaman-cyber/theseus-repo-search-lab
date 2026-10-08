"""Detached evidence receipt cannot give legacy artifacts new proof authority."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_artifact import (
    ARCHIVE_SHA256,
    SOURCE_COMMIT,
    write_sample,
    write_v2_archive_fixture,
)
from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.evidence_class import ProofEligibility
from theseus_repo_search.proof_evidence_sidecar import verify_bound_proof_evidence


def _prepare(root: Path, *, archive: bool = False, axioms: tuple[str, ...] = ()):
    artifact = root / "artifact"
    if archive:
        write_v2_archive_fixture(artifact)
        decl, rev = "Regular.Main.demo", ARCHIVE_SHA256
    else:
        write_sample(artifact)
        decl, rev = "Zeta23.Tiny.a", SOURCE_COMMIT
    identity = artifact_identity(load_artifact(artifact)[0])
    if axioms:
        report = f"'{decl}' depends on axioms: [{', '.join(axioms)}]\n"
    else:
        report = f"'{decl}' does not depend on any axioms\n"
    report_path = root / "lean-axioms.txt"
    report_path.write_text(report, encoding="utf-8")
    receipt = {
        "schema": "theseus.repo-search-proof-evidence-sidecar.v1",
        "artifact": {"identity": identity, "source_revision": rev},
        "corpus_class": "PROOF_CORPUS",
        "declaration": decl,
        "lean_audit": {
            "toolchain": "leanprover/lean4:v4.33.1",
            "command": f"#print axioms {decl}",
            "transcript_sha256": hashlib.sha256(report.encode()).hexdigest(),
            "observed_axioms": list(axioms),
            "allowed_axioms": [],
        },
    }
    sidecar = root / "proof-evidence.json"
    _save(sidecar, receipt)
    return artifact, sidecar, report_path, identity


def _save(path: Path, obj: dict):
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _check(artifact: Path, sidecar: Path | None, report: Path | None, *, pin: str | None):
    return verify_bound_proof_evidence(
        artifact=artifact,
        sidecar=sidecar,
        transcript=report,
        expected_sidecar_sha256=pin,
    )


class BoundProofEvidenceTests(unittest.TestCase):
    def test_no_sidecar_on_legacy_artifact_is_unknown_not_proof(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, _, _, identity = _prepare(Path(d))
            before = artifact_identity(load_artifact(artifact)[0])
            result = _check(artifact, None, None, pin=None)
            self.assertEqual(result, ProofEligibility.UNKNOWN_NOT_PROOF_EVIDENCE)
            self.assertEqual(artifact_identity(load_artifact(artifact)[0]), identity)
            self.assertEqual(before, identity)

    def test_unpinned_sidecar_cannot_promote_even_a_clean_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, sidecar, transcript, _ = _prepare(Path(d))
            with self.assertRaisesRegex(ValueError, "UNPINNED_EVIDENCE"):
                _check(artifact, sidecar, transcript, pin=None)

    def test_pinned_clean_transcript_is_only_candidate_not_proof(self):
        for archive in (False, True):
            with self.subTest(archive=archive), tempfile.TemporaryDirectory() as d:
                artifact, sidecar, transcript, identity = _prepare(Path(d), archive=archive)
                before = artifact_identity(load_artifact(artifact)[0])
                pin = hashlib.sha256(sidecar.read_bytes()).hexdigest()
                self.assertEqual(
                    _check(artifact, sidecar, transcript, pin=pin),
                    ProofEligibility.CANDIDATE_FOR_PROOF_VERIFICATION,
                )
                self.assertEqual(artifact_identity(load_artifact(artifact)[0]), before)
                self.assertEqual(before, identity)

    def test_sorry_dep_is_not_proof_despite_self_declared_proof_class(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, sidecar, transcript, _ = _prepare(Path(d), axioms=("sorryAx",))
            self.assertEqual(
                _check(artifact, sidecar, transcript, pin=hashlib.sha256(sidecar.read_bytes()).hexdigest()),
                ProofEligibility.NOT_PROOF_EVIDENCE,
            )

    def test_statement_corpus_cannot_be_upgraded_by_clean_audit(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, sidecar, transcript, _ = _prepare(Path(d))
            payload = json.loads(sidecar.read_text())
            payload["corpus_class"] = "STATEMENT_CORPUS"
            _save(sidecar, payload)
            self.assertEqual(
                _check(artifact, sidecar, transcript, pin=hashlib.sha256(sidecar.read_bytes()).hexdigest()),
                ProofEligibility.NOT_PROOF_EVIDENCE,
            )

    def test_forged_or_drifted_evidence_is_rejected(self):
        for mutation in ("artifact", "revision", "declaration", "axioms", "raw", "receipt", "missing_raw"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as d:
                artifact, sidecar, transcript, _ = _prepare(Path(d))
                pinned = hashlib.sha256(sidecar.read_bytes()).hexdigest()
                if mutation in ("artifact", "revision", "declaration", "axioms"):
                    payload = json.loads(sidecar.read_text())
                    if mutation == "artifact":
                        payload["artifact"]["identity"] = "f" * 64
                    elif mutation == "revision":
                        payload["artifact"]["source_revision"] = "wrong"
                    elif mutation == "declaration":
                        payload["declaration"] = "Fake.Forgotten"
                    else:
                        payload["lean_audit"]["observed_axioms"] = ["sorryAx"]
                    _save(sidecar, payload)
                    # Even re-pinning tampered content cannot break artifact/report binding.
                    pinned = hashlib.sha256(sidecar.read_bytes()).hexdigest()
                elif mutation == "raw":
                    transcript.write_text("'Fake.Forgotten' does not depend on any axioms\n")
                elif mutation == "receipt":
                    sidecar.write_bytes(sidecar.read_bytes() + b" ")
                else:
                    transcript.unlink()
                with self.assertRaises(ValueError):
                    _check(artifact, sidecar, transcript, pin=pinned)

    def test_empty_or_invalid_json_and_wrong_class_fail_closed(self):
        for variant in ("[]", "{}", '{"corpus_class":"SOMETHING_ELSE"}'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as d:
                artifact, sidecar, transcript, _ = _prepare(Path(d))
                sidecar.write_text(variant, encoding="utf-8")
                with self.assertRaises((ValueError, TypeError)):
                    _check(artifact, sidecar, transcript, pin=hashlib.sha256(sidecar.read_bytes()).hexdigest())

    def test_evidence_files_are_never_read_unbounded(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, sidecar, transcript, _ = _prepare(Path(d))
            sidecar_pin = hashlib.sha256(sidecar.read_bytes()).hexdigest()
            original = Path.read_bytes

            def reject_unbounded_read(path):
                if path in (sidecar, transcript):
                    raise AssertionError("unbounded read_bytes on untrusted evidence")
                return original(path)

            with patch.object(Path, "read_bytes", reject_unbounded_read):
                result = _check(artifact, sidecar, transcript, pin=sidecar_pin)
            self.assertEqual(result, ProofEligibility.CANDIDATE_FOR_PROOF_VERIFICATION)

    def test_oversized_sidecar_fails_closed_even_if_valid_json_and_re_pinned(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, sidecar, transcript, _ = _prepare(Path(d))
            sidecar.write_bytes(sidecar.read_bytes() + b" " * (64 * 1024))
            sidecar_pin = hashlib.sha256(sidecar.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "EVIDENCE_TOO_LARGE"):
                _check(artifact, sidecar, transcript, pin=sidecar_pin)

    def test_oversized_transcript_checks_length_before_digest(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, sidecar, transcript, _ = _prepare(Path(d))
            transcript.write_bytes(transcript.read_bytes() + b" " * (64 * 1024))
            sidecar_pin = hashlib.sha256(sidecar.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "EVIDENCE_TOO_LARGE"):
                _check(artifact, sidecar, transcript, pin=sidecar_pin)


if __name__ == "__main__":
    unittest.main()
