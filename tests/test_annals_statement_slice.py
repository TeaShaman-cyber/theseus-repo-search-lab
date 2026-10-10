"""The Annals Marton statement is an indexed candidate, never proof evidence."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_live_replay_refuses_missing_or_ambiguous_statement_source(self):
        from scripts.replay_annals_marton import run_replay

        source = SimpleNamespace(source_id="annals-challenge-marton")
        hit = SimpleNamespace(
            declaration_hint="theorem_1_2",
            source_path="Other/Shadow.lean",
            source_start_line=1,
            source_end_line=2,
        )
        with patch(
            "scripts.replay_annals_marton.prepare_registered_replay",
            return_value=(object(), source),
        ), patch("scripts.replay_annals_marton.search", return_value=[hit]), self.assertRaisesRegex(
            AssertionError, "not resolved exactly"
        ):
            run_replay(Path("empty.sqlite"), Path("artifact"), SOURCE)

    def test_live_replay_keeps_statement_and_proof_distinct(self):
        from scripts.replay_annals_marton import run_replay

        source = SimpleNamespace(source_id="annals-challenge-marton")
        hit = SimpleNamespace(
            declaration_hint="theorem_1_2",
            source_path="AnnalsChallenge/AnnalsOfMathematics/2025-201-2-ConjectureOfMarton.lean",
            source_start_line=35,
            source_end_line=36,
        )
        ctx = {
            "estimated_tokens": 100,
            "chunks": [{"declaration_hint": "theorem_1_2"}],
        }
        with patch(
            "scripts.replay_annals_marton.prepare_registered_replay",
            return_value=(object(), source),
        ), patch("scripts.replay_annals_marton.search", return_value=[hit]), patch(
            "scripts.replay_annals_marton.context", return_value=ctx,
        ), patch("scripts.replay_annals_marton.artifact_identity", return_value="id"), patch(
            "scripts.replay_annals_marton.registered_replay_provenance", return_value={"repo": "AnnalsChallenge"},
        ):
            receipt = run_replay(Path("empty.sqlite"), Path("artifact"), SOURCE)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["corpus_class"], "STATEMENT_CORPUS")
        self.assertEqual(receipt["proof_evidence"], "NOT_PROOF_EVIDENCE")
        self.assertEqual(receipt["scientific_authority"], "NONE")

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
