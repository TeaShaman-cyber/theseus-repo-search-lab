"""Conservative proof-eligibility policy, not a proof or provenance verifier.

A class declaration and a reported Lean axiom list are insufficient to
establish theorem correctness. A CANDIDATE result requires independent,
source-bound audit evidence and statement review before any proof authority.
Existing artifact v1/v2 schemas and accepted identities are unaffected.
"""

from __future__ import annotations

from enum import Enum


class CorpusEvidenceClass(str, Enum):
    PROOF_CORPUS = "PROOF_CORPUS"
    PARTIAL_PROOF_CORPUS = "PARTIAL_PROOF_CORPUS"
    STATEMENT_CORPUS = "STATEMENT_CORPUS"
    BENCHMARK_CORPUS = "BENCHMARK_CORPUS"


class ProofEligibility(str, Enum):
    UNKNOWN_NOT_PROOF_EVIDENCE = "UNKNOWN_NOT_PROOF_EVIDENCE"
    NOT_PROOF_EVIDENCE = "NOT_PROOF_EVIDENCE"
    CANDIDATE_FOR_PROOF_VERIFICATION = "CANDIDATE_FOR_PROOF_VERIFICATION"


def _is_sorry_axiom(name: str) -> bool:
    return name.rsplit(".", 1)[-1] == "sorryAx"


def evaluate_proof_eligibility(
    *,
    corpus_class: CorpusEvidenceClass | None,
    observed_axioms: tuple[str, ...] | None,
    allowed_axioms: tuple[str, ...] = (),
) -> ProofEligibility:
    """Determine only whether a declaration may enter *further* proof review.

    ``observed_axioms`` denotes a caller-supplied claimed Lean axiom audit,
    not a trusted audit performed here. Its completeness and provenance must
    be independently verified before any mathematical proof acceptance.
    ``allowed_axioms`` is explicit review scope, not a default axiom whitelist.
    """
    if corpus_class is not None and not isinstance(corpus_class, CorpusEvidenceClass):
        raise ValueError("unrecognized corpus evidence class")
    if observed_axioms is not None and (
        not isinstance(observed_axioms, tuple)
        or any(not isinstance(name, str) or not name for name in observed_axioms)
    ):
        raise ValueError("invalid observed axioms")
    if not isinstance(allowed_axioms, tuple) or any(
        not isinstance(name, str) or not name for name in allowed_axioms
    ):
        raise ValueError("invalid allowed axioms")

    if corpus_class is None or observed_axioms is None:
        return ProofEligibility.UNKNOWN_NOT_PROOF_EVIDENCE
    if corpus_class != CorpusEvidenceClass.PROOF_CORPUS:
        return ProofEligibility.NOT_PROOF_EVIDENCE
    if any(_is_sorry_axiom(name) for name in observed_axioms):
        return ProofEligibility.NOT_PROOF_EVIDENCE
    if not set(observed_axioms).issubset(allowed_axioms):
        return ProofEligibility.NOT_PROOF_EVIDENCE
    return ProofEligibility.CANDIDATE_FOR_PROOF_VERIFICATION
