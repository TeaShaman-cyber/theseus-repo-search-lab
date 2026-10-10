import unittest

from theseus_repo_search.evidence_class import (
    CorpusEvidenceClass,
    ProofEligibility,
    evaluate_proof_eligibility,
    release_replay_evidence,
)


class ProofEligibilityTests(unittest.TestCase):
    def test_release_requires_declared_class_without_promoting_proof(self):
        for klass in CorpusEvidenceClass:
            expected = (
                ProofEligibility.UNKNOWN_NOT_PROOF_EVIDENCE
                if klass is CorpusEvidenceClass.PROOF_CORPUS
                else ProofEligibility.NOT_PROOF_EVIDENCE
            )
            with self.subTest(klass=klass):
                evidence = release_replay_evidence({
                    "corpus_class": klass.value,
                    "proof_evidence": expected.value,
                })
                self.assertEqual(evidence, {
                    "declared_corpus_class": klass.value,
                    "proof_eligibility": expected.value,
                })

    def test_proof_class_with_no_axiom_audit_does_not_claim_proof(self):
        verdict = evaluate_proof_eligibility(
            corpus_class=CorpusEvidenceClass.PROOF_CORPUS,
            observed_axioms=None,
        )
        self.assertEqual(verdict, ProofEligibility.UNKNOWN_NOT_PROOF_EVIDENCE)

    def test_sorry_axiom_is_not_proof_even_when_source_claims_proof_corpus(self):
        verdict = evaluate_proof_eligibility(
            corpus_class=CorpusEvidenceClass.PROOF_CORPUS,
            observed_axioms=("sorryAx",),
        )
        self.assertEqual(verdict, ProofEligibility.NOT_PROOF_EVIDENCE)

    def test_clean_audit_makes_proof_corpus_eligible_for_further_verification(self):
        verdict = evaluate_proof_eligibility(
            corpus_class=CorpusEvidenceClass.PROOF_CORPUS,
            observed_axioms=(),
        )
        self.assertEqual(verdict, ProofEligibility.CANDIDATE_FOR_PROOF_VERIFICATION)

    def test_unapproved_axioms_cannot_be_approved_by_build_success(self):
        verdict = evaluate_proof_eligibility(
            corpus_class=CorpusEvidenceClass.PROOF_CORPUS,
            observed_axioms=("unreviewed.customAxiom",),
        )
        self.assertEqual(verdict, ProofEligibility.NOT_PROOF_EVIDENCE)

    def test_explicitly_declared_axiom_scope_only_enables_further_verification(self):
        verdict = evaluate_proof_eligibility(
            corpus_class=CorpusEvidenceClass.PROOF_CORPUS,
            observed_axioms=("Classical.choice", "propext"),
            allowed_axioms=("Classical.choice", "propext"),
        )
        self.assertEqual(verdict, ProofEligibility.CANDIDATE_FOR_PROOF_VERIFICATION)

    def test_sorry_cannot_be_allowlisted_even_if_explicitly_passed(self):
        verdict = evaluate_proof_eligibility(
            corpus_class=CorpusEvidenceClass.PROOF_CORPUS,
            observed_axioms=("sorryAx",),
            allowed_axioms=("sorryAx",),
        )
        self.assertEqual(verdict, ProofEligibility.NOT_PROOF_EVIDENCE)

    def test_nonproof_corpus_cannot_upgrade_even_with_empty_axiom_audit(self):
        for klass in (
            CorpusEvidenceClass.PARTIAL_PROOF_CORPUS,
            CorpusEvidenceClass.STATEMENT_CORPUS,
            CorpusEvidenceClass.BENCHMARK_CORPUS,
        ):
            with self.subTest(klass=klass):
                verdict = evaluate_proof_eligibility(
                    corpus_class=klass,
                    observed_axioms=(),
                )
                self.assertEqual(verdict, ProofEligibility.NOT_PROOF_EVIDENCE)

    def test_missing_corpus_class_is_unknown_and_not_proof(self):
        verdict = evaluate_proof_eligibility(
            corpus_class=None,
            observed_axioms=(),
        )
        self.assertEqual(verdict, ProofEligibility.UNKNOWN_NOT_PROOF_EVIDENCE)

    def test_unknown_class_value_does_not_default_to_proof(self):
        with self.assertRaises(ValueError):
            evaluate_proof_eligibility(corpus_class="PROOF_CORPUS", observed_axioms=())


if __name__ == "__main__":
    unittest.main()
