"""Narrow hosted Lean stdout witness, then fresh externally pinned consumer.

The workflow records runtime observations, not proof attestation or an accepted
source corpus. GitHub run metadata must be independently fetched and compared;
a self-written receipt alone has no authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from theseus_repo_search.evidence_class import (
    CorpusEvidenceClass,
    ProofEligibility,
    evaluate_proof_eligibility,
)
from theseus_repo_search.lean_axioms_output import extract_exact_axiom_reports
from theseus_repo_search.proof_evidence_sidecar import (
    _axioms_from_exact_report,
    _read_bounded_evidence,
)

SCHEMA = "theseus.repo-search-lean-axiom-witness.v1"
REPO = "TeaShaman-cyber/theseus-repo-search-lab"
SOURCE_PATH = "tests/fixtures/lean_axioms/AxiomProbe.lean"
SOURCE_SHA256 = "d92d2482c12a116d254feba6e09c275dab183e4a3bf9d1ebf6aa3d48260342b7"
VERSION = "4.33.0-rc2"
DECLARATIONS = ("EvidenceClassCanary.fully_proved", "EvidenceClassCanary.admitted")
MAX_RAW_BYTES = 1024 * 1024


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(path: Path, limit: int = MAX_RAW_BYTES) -> bytes:
    return _read_bounded_evidence(path, limit=limit, label=path.name)


def _require(condition: bool, why: str) -> None:
    if not condition:
        raise ValueError(why)


def capture_witness(
    *, source: Path, output_dir: Path, lean_executable: Path,
    repository: str, source_commit: str, run_id: str, run_attempt: str,
    lean_archive_sha256: str,
) -> None:
    """Observe genuine Lean process; the workflow must bind its own checkout."""
    _require(repository == REPO, "WRONG_REPOSITORY")
    _require(bool(re.fullmatch(r"[0-9a-f]{40}", source_commit)), "INVALID_SOURCE_SHA")
    _require(run_id.isdecimal() and run_attempt.isdecimal(), "INVALID_RUN_ID")
    _require(bool(re.fullmatch(r"[0-9a-f]{64}", lean_archive_sha256)), "INVALID_ARCHIVE_SHA256")
    _require(_digest(_read(source, 64 * 1024)) == SOURCE_SHA256, "UNEXPECTED_SOURCE_BYTES")
    version = subprocess.run(
        [str(lean_executable), "--version"], capture_output=True, timeout=30, check=False,
    )
    _require(version.returncode == 0, "LEAN_VERSION_FAILED")
    text = version.stdout.decode("utf-8")
    _require(text.startswith(f"Lean (version {VERSION}, "), "UNEXPECTED_LEAN_VERSION")
    proc = subprocess.run(
        [str(lean_executable), str(source.resolve())], capture_output=True,
        timeout=90, check=False,
    )
    _require(proc.returncode == 0, "LEAN_EXIT_NONZERO")
    _require(not proc.stderr, "LEAN_STDERR_NONEMPTY")
    _require(0 < len(proc.stdout) <= MAX_RAW_BYTES, "INVALID_LEAN_STDOUT_SIZE")
    raw = proc.stdout
    # This deliberately includes a negative control: a sorry-backed declaration.
    parsed = extract_exact_axiom_reports(
        raw, expected_declarations=DECLARATIONS, exit_code=0,
        expected_stdout_sha256=_digest(raw),
    )
    _require(
        _axioms_from_exact_report(parsed[DECLARATIONS[0]], DECLARATIONS[0]) == (),
        "MISSING_POSITIVE_CONTROL",
    )
    _require(
        "sorryAx" in _axioms_from_exact_report(parsed[DECLARATIONS[1]], DECLARATIONS[1]),
        "MISSING_SORRY_NEGATIVE_CONTROL",
    )
    bin_hash = _digest(_read(lean_executable, 4 * 1024 * 1024))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stdout.txt").write_bytes(raw)
    (output_dir / "stderr.txt").write_bytes(proc.stderr)
    receipt = {
        "schema": SCHEMA,
        "source": {
            "repository": repository, "commit": source_commit,
            "path": SOURCE_PATH, "sha256": SOURCE_SHA256,
        },
        "run": {"id": run_id, "attempt": run_attempt},
        "execution": {
            "lean_version": text.strip(),
            "lean_binary_sha256": bin_hash,
            "lean_archive_sha256": lean_archive_sha256,
            "exit_code": proc.returncode,
            "stdout_sha256": _digest(raw),
            "stderr_sha256": _digest(proc.stderr),
        },
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    print(f"hosted_stdout_sha256={_digest(raw)}")
    print(f"hosted_source_sha256={SOURCE_SHA256}")
    print("LEAN_WITNESS_CAPTURE_PASS")


def consume_witness(
    witness_dir: Path, *, source: Path, expected_commit: str,
    expected_run_id: str, expected_run_attempt: str,
    expected_lean_binary_sha256: str,
    expected_lean_archive_sha256: str,
) -> dict[str, ProofEligibility]:
    """Consume provider-fetched run files, matching independent run id and SHA.

    The external caller must obtain expected_run_id, expected_run_attempt and
    expected_commit from GitHub Actions API, and source from that exact Git
    revision (not the receipt).
    """
    receipt = json.loads(_read(witness_dir / "receipt.json", 64 * 1024))
    _require(isinstance(receipt, dict), "INVALID_WITNESS_RECEIPT")
    _require(set(receipt) == {"schema", "source", "run", "execution"}, "INVALID_WITNESS_FIELDS")
    _require(receipt["schema"] == SCHEMA, "INVALID_WITNESS_SCHEMA")
    upstream = receipt["source"]
    run = receipt["run"]
    details = receipt["execution"]
    _require(isinstance(upstream, dict) and set(upstream) == {"repository", "commit", "path", "sha256"}, "INVALID_SOURCE_RECEIPT")
    _require(isinstance(run, dict) and set(run) == {"id", "attempt"}, "INVALID_RUN_RECEIPT")
    _require(isinstance(details, dict) and set(details) == {"lean_version", "lean_binary_sha256", "lean_archive_sha256", "exit_code", "stdout_sha256", "stderr_sha256"}, "INVALID_EXECUTION_RECEIPT")
    _require(upstream["repository"] == REPO, "WRONG_REPOSITORY")
    _require(upstream["path"] == SOURCE_PATH, "WRONG_SOURCE_PATH")
    _require(upstream["commit"] == expected_commit, "SOURCE_COMMIT_MISMATCH")
    _require(run["id"] == expected_run_id, "RUN_ID_MISMATCH")
    _require(isinstance(run["attempt"], str) and run["attempt"].isdecimal(), "INVALID_RUN_ATTEMPT")
    _require(run["attempt"] == expected_run_attempt, "RUN_ATTEMPT_MISMATCH")
    _require(upstream["sha256"] == SOURCE_SHA256, "SOURCE_DIGEST_MISMATCH")
    _require(_digest(_read(source, 64 * 1024)) == SOURCE_SHA256, "SOURCE_BYTES_MISMATCH")
    _require(isinstance(details["lean_version"], str) and details["lean_version"].startswith(f"Lean (version {VERSION}, "), "WRONG_LEAN_TOOLCHAIN")
    _require(details["exit_code"] == 0, "LEAN_PROCESS_FAILED")
    _require(
        details["lean_binary_sha256"] == expected_lean_binary_sha256,
        "LEAN_BINARY_DIGEST_MISMATCH",
    )
    _require(
        details["lean_archive_sha256"] == expected_lean_archive_sha256,
        "LEAN_ARCHIVE_DIGEST_MISMATCH",
    )
    for key in ("lean_binary_sha256", "lean_archive_sha256", "stdout_sha256", "stderr_sha256"):
        _require(isinstance(details[key], str) and re.fullmatch(r"[0-9a-f]{64}", details[key]) is not None, "INVALID_DIGEST")
    raw = _read(witness_dir / "stdout.txt")
    stderr = _read(witness_dir / "stderr.txt", 64 * 1024)
    _require(not stderr and _digest(stderr) == details["stderr_sha256"], "INVALID_STDERR")
    _require(_digest(raw) == details["stdout_sha256"], "STDOUT_DIGEST_MISMATCH")
    reports = extract_exact_axiom_reports(
        raw, expected_declarations=DECLARATIONS, exit_code=0,
        expected_stdout_sha256=details["stdout_sha256"],
    )
    clean = _axioms_from_exact_report(reports[DECLARATIONS[0]], DECLARATIONS[0])
    sorry = _axioms_from_exact_report(reports[DECLARATIONS[1]], DECLARATIONS[1])
    _require(clean == (), "INVALID_POSITIVE_CONTROL")
    _require("sorryAx" in sorry, "INVALID_SORRY_NEGATIVE_CONTROL")
    outcomes = {
        DECLARATIONS[0]: evaluate_proof_eligibility(
            corpus_class=CorpusEvidenceClass.PROOF_CORPUS, observed_axioms=clean,
        ),
        DECLARATIONS[1]: evaluate_proof_eligibility(
            corpus_class=CorpusEvidenceClass.PROOF_CORPUS, observed_axioms=sorry,
        ),
    }
    print(f"consumer_run_id={expected_run_id}")
    print(f"consumer_head_sha={expected_commit}")
    print("LEAN_WITNESS_CONSUMER_PASS (toy fixture; no accepted artifact linkage)")
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--source", type=Path, required=True)
    capture.add_argument("--out", type=Path, required=True)
    capture.add_argument("--lean", type=Path, required=True)
    capture.add_argument("--repository", required=True)
    capture.add_argument("--source-commit", required=True)
    capture.add_argument("--run-id", required=True)
    capture.add_argument("--run-attempt", required=True)
    capture.add_argument("--lean-archive-sha256", required=True)
    consume = sub.add_parser("consume")
    consume.add_argument("--witness-dir", type=Path, required=True)
    consume.add_argument("--source", type=Path, required=True)
    consume.add_argument("--expected-commit", required=True)
    consume.add_argument("--expected-run-id", required=True)
    consume.add_argument("--expected-run-attempt", required=True)
    consume.add_argument("--expected-lean-binary-sha256", required=True)
    consume.add_argument("--expected-lean-archive-sha256", required=True)
    args = parser.parse_args()
    if args.mode == "capture":
        capture_witness(
            source=args.source, output_dir=args.out, lean_executable=args.lean,
            repository=args.repository, source_commit=args.source_commit,
            run_id=args.run_id, run_attempt=args.run_attempt,
            lean_archive_sha256=args.lean_archive_sha256,
        )
    else:
        consume_witness(
            args.witness_dir, source=args.source,
            expected_commit=args.expected_commit, expected_run_id=args.expected_run_id,
            expected_run_attempt=args.expected_run_attempt,
            expected_lean_binary_sha256=args.expected_lean_binary_sha256,
            expected_lean_archive_sha256=args.expected_lean_archive_sha256,
        )


if __name__ == "__main__":
    main()
