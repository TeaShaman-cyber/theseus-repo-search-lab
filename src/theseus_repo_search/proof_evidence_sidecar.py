"""Read a detached, digest-bound *reported* Lean axiom audit, fail closed.

Neither JSON metadata nor a matching transcript digest proves the transcript was
produced by Lean. The caller must independently authenticate the expected
sidecar digest, Lean execution/toolchain, source and target declaration before
any mathematical proof authority is considered. The strongest result here is
CANDIDATE_FOR_PROOF_VERIFICATION, never a verified theorem.

Detached files deliberately leave accepted artifact v1/v2 identities unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .artifact import artifact_identity, load_artifact
from .evidence_class import (
    CorpusEvidenceClass,
    ProofEligibility,
    evaluate_proof_eligibility,
)

SCHEMA = "theseus.repo-search-proof-evidence-sidecar.v1"
MAX_TRANSCRIPT_BYTES = 64 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _object(value: object, keys: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"invalid {label} fields")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid {label}")
    return value


def _names(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"invalid {label}")
    names = tuple(_string(item, label) for item in value)
    if len(set(names)) != len(names) or any(item.strip() != item for item in names):
        raise ValueError(f"invalid {label} names")
    return names


def _axioms_from_exact_report(raw: bytes, declaration: str) -> tuple[str, ...]:
    if not raw or len(raw) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("invalid Lean audit transcript size")
    try:
        report = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid Lean audit transcript UTF-8") from exc
    if not report.endswith("\n") or "\r" in report or len(report.splitlines()) != 1:
        raise ValueError("expected one exact Lean axiom report line")
    line = report.removesuffix("\n")
    prefix = f"'{declaration}' "
    if not line.startswith(prefix):
        raise ValueError("Lean report declaration mismatch")
    details = line[len(prefix) :]
    if details == "does not depend on any axioms":
        return ()
    marker = "depends on axioms: ["
    if not details.startswith(marker) or not details.endswith("]"):
        raise ValueError("invalid Lean axiom report syntax")
    content = details[len(marker) : -1]
    if not content:
        return ()
    names = tuple(content.split(", "))
    if any(not name or not name.strip() == name for name in names):
        raise ValueError("invalid Lean axiom report names")
    if len(set(names)) != len(names):
        raise ValueError("duplicate Lean axiom names")
    return names


def verify_bound_proof_evidence(
    *,
    artifact: Path,
    sidecar: Path | None,
    transcript: Path | None,
    expected_sidecar_sha256: str | None,
) -> ProofEligibility:
    """Verify coherence of an externally pinned, detached axiom report.

    An externally passed digest is an expected *byte identity*, not by itself
    an independent authentication or an attestation of Lean execution.
    """
    if sidecar is None:
        if transcript is not None or expected_sidecar_sha256 is not None:
            raise ValueError("INCOMPLETE_EVIDENCE: sidecar is missing")
        return ProofEligibility.UNKNOWN_NOT_PROOF_EVIDENCE
    if transcript is None:
        raise ValueError("INCOMPLETE_EVIDENCE: transcript is missing")
    if not _is_sha256(expected_sidecar_sha256):
        raise ValueError("UNPINNED_EVIDENCE: missing exact trusted sidecar digest")
    try:
        sidecar_bytes = sidecar.read_bytes()
        transcript_bytes = transcript.read_bytes()
    except OSError as exc:
        raise ValueError(f"MISSING_EVIDENCE: {exc}") from exc
    if _sha256(sidecar_bytes) != expected_sidecar_sha256:
        raise ValueError("SIDECAR_DIGEST_MISMATCH")
    try:
        payload = json.loads(sidecar_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid sidecar JSON") from exc

    root = _object(
        payload,
        {"schema", "artifact", "corpus_class", "declaration", "lean_audit"},
        "sidecar",
    )
    if root["schema"] != SCHEMA:
        raise ValueError("unsupported sidecar schema")
    source = _object(root["artifact"], {"identity", "source_revision"}, "artifact")
    declared_identity = _string(source["identity"], "artifact identity")
    declared_revision = _string(source["source_revision"], "source revision")
    declaration = _string(root["declaration"], "declaration")
    try:
        klass = CorpusEvidenceClass(_string(root["corpus_class"], "corpus class"))
    except ValueError as exc:
        raise ValueError("unsupported corpus evidence class") from exc

    audit = _object(
        root["lean_audit"],
        {"toolchain", "command", "transcript_sha256", "observed_axioms", "allowed_axioms"},
        "Lean audit",
    )
    _string(audit["toolchain"], "Lean toolchain")
    if audit["command"] != f"#print axioms {declaration}":
        raise ValueError("Lean audit command/declaration mismatch")
    if not _is_sha256(audit["transcript_sha256"]):
        raise ValueError("invalid transcript digest")
    if _sha256(transcript_bytes) != audit["transcript_sha256"]:
        raise ValueError("Lean transcript digest mismatch")
    observed = _names(audit["observed_axioms"], "observed axioms")
    allowed = _names(audit["allowed_axioms"], "allowed axioms")
    if observed != _axioms_from_exact_report(transcript_bytes, declaration):
        raise ValueError("reported axioms mismatch transcript")

    manifest, nodes, _, _ = load_artifact(artifact)
    if artifact_identity(manifest) != declared_identity:
        raise ValueError("artifact identity mismatch")
    if manifest.source_revision != declared_revision:
        raise ValueError("source revision mismatch")
    if declaration not in {node.full_name for node in nodes}:
        raise ValueError("declaration absent from accepted artifact")
    return evaluate_proof_eligibility(
        corpus_class=klass,
        observed_axioms=observed,
        allowed_axioms=allowed,
    )
