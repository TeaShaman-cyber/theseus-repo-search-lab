"""The Annals Marton statement is an indexed candidate, never proof evidence."""

import unittest
from pathlib import Path

from theseus_repo_search.evidence_class import (
    CorpusEvidenceClass,
    ProofEligibility,
    evaluate_proof_eligibility,
)
from theseus_repo_search.producer_config import load_lean_git_source

SOURCE = Path(__file__).parent.parent / "producer/sources/annals-challenge-marton.json"
MODULE = "AnnalsChallenge.AnnalsOfMathematics.«2025-201-2-ConjectureOfMarton»"


class AnnalsStatementSliceTests(unittest.TestCase):
    def test_pinned_single_statement_module_uses_existing_git_source_contract(self):
        source = load_lean_git_source(SOURCE)
        self.assertEqual(source.source_id, "annals-challenge-marton")
        self.assertEqual(source.source_repo, "ImperialCollegeLondon/AnnalsChallenge")
        self.assertEqual(source.source_commit, "e32eb1411db0d700ca874dd695aea92f78699db8")
        self.assertEqual(source.root_modules, (MODULE,))
        self.assertEqual(source.build_target, MODULE)
        self.assertEqual(source.source_subdir, "")

    def test_sorry_and_statement_category_cannot_be_promoted(self):
        self.assertEqual(
            evaluate_proof_eligibility(
                corpus_class=CorpusEvidenceClass.STATEMENT_CORPUS,
                observed_axioms=("sorryAx",),
            ),
            ProofEligibility.NOT_PROOF_EVIDENCE,
        )
        self.assertEqual(
            evaluate_proof_eligibility(
                corpus_class=CorpusEvidenceClass.STATEMENT_CORPUS,
                observed_axioms=(),
            ),
            ProofEligibility.NOT_PROOF_EVIDENCE,
        )

    def test_absent_audit_stays_unknown(self):
        self.assertEqual(
            evaluate_proof_eligibility(
                corpus_class=None,
                observed_axioms=None,
            ),
            ProofEligibility.UNKNOWN_NOT_PROOF_EVIDENCE,
        )


if __name__ == "__main__":
    unittest.main()
