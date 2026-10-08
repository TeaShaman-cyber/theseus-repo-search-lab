"""One pinned Marton Lean axiom report; no proof-authority promotion.

Hosting and run metadata remain external. Caller must independently verify GitHub
run SHA/attempt and artifact provider digest before consuming downloaded files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.evidence_class import ProofEligibility
from theseus_repo_search.lean_axioms_output import extract_exact_axiom_reports
from theseus_repo_search.proof_evidence_sidecar import (
    SCHEMA as SIDECAR_SCHEMA,
)
from theseus_repo_search.proof_evidence_sidecar import (
    _axioms_from_exact_report,
    _read_bounded_evidence,
    verify_bound_proof_evidence,
)

SCHEMA = "theseus.repo-search-marton-hosted-axiom-witness.v1"
REPOSITORY = "TeaShaman-cyber/theseus-repo-search-lab"
SOURCE_REPO = "ImperialCollegeLondon/AnnalsChallenge"
SOURCE_COMMIT = "e32eb1411db0d700ca874dd695aea92f78699db8"
SOURCE_PATH = "AnnalsChallenge/AnnalsOfMathematics/2025-201-2-ConjectureOfMarton.lean"
SOURCE_SHA256 = "a07d6d29b17aca763e05952382612dac0451405895deefa13bee94d9ea518fab"
LEAN_TOOLCHAIN = "leanprover/lean4:v4.33.0-rc1"
DECLARATION = "ConjectureOfMarton.theorem_1_2"
ARTIFACT_IDENTITY = "1d0d8ce2fbeb279fc9024856a94f788a2ed015dca5aeaee94786040b89d8f17c"
PROBE_SHA256 = "6da7136727f5dcc0a52d0d92901b76ab6280a1b3e7073fd76844743cf8d4d72f"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require(ok: bool, error: str) -> None:
    if not ok:
        raise ValueError(error)


def _sha(value: object, name: str) -> None:
    _require(
        isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
        name,
    )


def capture_marton_witness(
    *,
    source_root: Path,
    probe: Path,
    out: Path,
    repository: str,
    github_head: str,
    run_id: str,
    run_attempt: str,
    lean_version: str,
    lean_executable_sha256: str,
) -> dict[str, object]:
    """Capture real `lake env lean`; caller verifies the outer hosted run."""
    _require(repository == REPOSITORY, "REPOSITORY_MISMATCH")
    _require(re.fullmatch(r"[a-f0-9]{40}", github_head) is not None, "INVALID_RUN_HEAD")
    _require(run_id.isdecimal() and run_attempt.isdecimal(), "INVALID_RUN_ID")
    _sha(lean_executable_sha256, "INVALID_BINARY_HASH")
    _require(lean_version.startswith("Lean (version 4.33.0-rc1, "), "WRONG_TOOLCHAIN")
    _require(
        (source_root / "lean-toolchain").read_text().strip() == LEAN_TOOLCHAIN,
        "TOOLCHAIN_PIN_MISMATCH",
    )
    _require(
        _digest((source_root / SOURCE_PATH).read_bytes()) == SOURCE_SHA256,
        "SOURCE_BYTES_MISMATCH",
    )
    _require(_digest(probe.read_bytes()) == PROBE_SHA256, "PROBE_BYTES_MISMATCH")
    run = subprocess.run(
        ["lake", "env", "lean", str(probe.resolve())],
        cwd=source_root,
        capture_output=True,
        timeout=120,
        check=False,
    )
    _require(run.returncode == 0, "LEAN_EXIT_NONZERO")
    _require(not run.stderr, "LEAN_STDERR_NONEMPTY")
    _require(0 < len(run.stdout) <= 64 * 1024, "LEAN_STDOUT_SIZE_INVALID")
    parsed = extract_exact_axiom_reports(
        run.stdout,
        expected_declarations=(DECLARATION,),
        exit_code=run.returncode,
        expected_stdout_sha256=_digest(run.stdout),
    )
    axioms = _axioms_from_exact_report(parsed[DECLARATION], DECLARATION)
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "run": {
            "repository": repository,
            "head": github_head,
            "id": run_id,
            "attempt": run_attempt,
        },
        "source": {
            "repo": SOURCE_REPO,
            "commit": SOURCE_COMMIT,
            "path": SOURCE_PATH,
            "sha256": SOURCE_SHA256,
            "toolchain": LEAN_TOOLCHAIN,
        },
        "declaration": DECLARATION,
        "probe_sha256": PROBE_SHA256,
        "artifact_identity": ARTIFACT_IDENTITY,
        "execution": {
            "lean_version": lean_version,
            "binary_sha256": lean_executable_sha256,
            "exit_code": run.returncode,
            "stdout_sha256": _digest(run.stdout),
            "stderr_sha256": _digest(run.stderr),
            "observed_axioms": list(axioms),
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "stdout.txt").write_bytes(run.stdout)
    (out / "stderr.txt").write_bytes(run.stderr)
    (out / "receipt.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n"
    )
    print("MARTON_AXIOM_CAPTURE_PASS")
    print("stdout_sha256=" + _digest(run.stdout))
    return payload


def consume_marton_witness(
    witness_dir: Path,
    *,
    artifact: Path,
    expected_head: str,
    expected_run_id: str,
    expected_run_attempt: str,
    out: Path,
) -> dict[str, object]:
    """Independently bind witness/target/artifact after provider run readback.

    This validates the supplied bytes, *not* their origin. Caller must validate
    expected_* via GitHub Actions API and the provider artifact digest.
    """
    payload = json.loads(
        _read_bounded_evidence(
            witness_dir / "receipt.json", limit=64 * 1024, label="witness receipt"
        )
    )
    _require(
        isinstance(payload, dict)
        and set(payload)
        == {
            "schema",
            "run",
            "source",
            "declaration",
            "probe_sha256",
            "artifact_identity",
            "execution",
        },
        "RECEIPT_SHAPE_INVALID",
    )
    _require(payload["schema"] == SCHEMA, "SCHEMA_MISMATCH")
    run = payload["run"]
    source = payload["source"]
    execution = payload["execution"]
    _require(
        run
        == {
            "repository": REPOSITORY,
            "head": expected_head,
            "id": expected_run_id,
            "attempt": expected_run_attempt,
        },
        "RUN_ID_MISMATCH"
        if run.get("id") != expected_run_id
        else "RUN_ATTEMPT_MISMATCH"
        if run.get("attempt") != expected_run_attempt
        else "RUN_HEAD_MISMATCH",
    )
    _require(
        source
        == {
            "repo": SOURCE_REPO,
            "commit": SOURCE_COMMIT,
            "path": SOURCE_PATH,
            "sha256": SOURCE_SHA256,
            "toolchain": LEAN_TOOLCHAIN,
        },
        "SOURCE_DIGEST_MISMATCH",
    )
    _require(payload["declaration"] == DECLARATION, "DECLARATION_MISMATCH")
    _require(payload["probe_sha256"] == PROBE_SHA256, "PROBE_DIGEST_MISMATCH")
    _require(payload["artifact_identity"] == ARTIFACT_IDENTITY, "ARTIFACT_PIN_MISMATCH")
    _require(
        isinstance(execution, dict)
        and set(execution)
        == {
            "lean_version",
            "binary_sha256",
            "exit_code",
            "stdout_sha256",
            "stderr_sha256",
            "observed_axioms",
        },
        "EXECUTION_FIELDS_INVALID",
    )
    _require(
        execution["lean_version"].startswith("Lean (version 4.33.0-rc1, "),
        "TOOLCHAIN_MISMATCH",
    )
    _sha(execution["binary_sha256"], "INVALID_BINARY_DIGEST")
    _require(execution["exit_code"] == 0, "LEAN_NONZERO")
    raw = _read_bounded_evidence(
        witness_dir / "stdout.txt", limit=64 * 1024, label="Lean stdout"
    )
    stderr = _read_bounded_evidence(
        witness_dir / "stderr.txt", limit=64 * 1024, label="Lean stderr"
    )
    _require(_digest(raw) == execution["stdout_sha256"], "STDOUT_DIGEST_MISMATCH")
    _require(
        not stderr and _digest(stderr) == execution["stderr_sha256"], "STDERR_MISMATCH"
    )
    reports = extract_exact_axiom_reports(
        raw,
        expected_declarations=(DECLARATION,),
        exit_code=0,
        expected_stdout_sha256=_digest(raw),
    )
    transcript = reports[DECLARATION]
    axioms = _axioms_from_exact_report(transcript, DECLARATION)
    _require(list(axioms) == execution["observed_axioms"], "AXIOM_REPORT_MISMATCH")
    _require(artifact.is_dir(), "ARTIFACT_MISSING")
    manifest, nodes, _, _ = load_artifact(artifact)
    _require(
        artifact_identity(manifest) == ARTIFACT_IDENTITY
        and manifest.source_revision == SOURCE_COMMIT
        and DECLARATION in {n.full_name for n in nodes},
        "ARTIFACT_BINDING_MISMATCH",
    )
    sidecar = {
        "schema": SIDECAR_SCHEMA,
        "artifact": {"identity": ARTIFACT_IDENTITY, "source_revision": SOURCE_COMMIT},
        "corpus_class": "STATEMENT_CORPUS",
        "declaration": DECLARATION,
        "lean_audit": {
            "toolchain": LEAN_TOOLCHAIN,
            "command": f"#print axioms {DECLARATION}",
            "transcript_sha256": _digest(transcript),
            "observed_axioms": list(axioms),
            "allowed_axioms": [],
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    sidecar_path = out / "sidecar.json"
    report_path = out / "axiom-report.txt"
    sidecar_path.write_text(json.dumps(sidecar, sort_keys=True, indent=2) + "\n")
    report_path.write_bytes(transcript)
    verdict = verify_bound_proof_evidence(
        artifact=artifact,
        sidecar=sidecar_path,
        transcript=report_path,
        expected_sidecar_sha256=_digest(sidecar_path.read_bytes()),
    )
    _require(verdict is ProofEligibility.NOT_PROOF_EVIDENCE, "INVALID_PROOF_PROMOTION")
    result: dict[str, object] = {
        "result": "PASS",
        "artifact_identity": ARTIFACT_IDENTITY,
        "proof_eligibility": verdict.value,
        "observed_axioms": list(axioms),
        "run_id": expected_run_id,
        "run_attempt": expected_run_attempt,
        "witness_head": expected_head,
        "sidecar_sha256": _digest(sidecar_path.read_bytes()),
    }
    (out / "verification.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    capture = sub.add_parser("capture")
    for flag in (
        "--source-root",
        "--probe",
        "--out",
        "--repository",
        "--github-head",
        "--run-id",
        "--run-attempt",
        "--lean-version",
        "--lean-executable-sha256",
    ):
        capture.add_argument(flag, required=True)
    consume = sub.add_parser("consume")
    for flag in (
        "--witness-dir",
        "--artifact",
        "--expected-head",
        "--expected-run-id",
        "--expected-run-attempt",
        "--out",
    ):
        consume.add_argument(flag, required=True)
    arg = parser.parse_args()
    if arg.mode == "capture":
        capture_marton_witness(
            source_root=Path(arg.source_root),
            probe=Path(arg.probe),
            out=Path(arg.out),
            repository=arg.repository,
            github_head=arg.github_head,
            run_id=arg.run_id,
            run_attempt=arg.run_attempt,
            lean_version=arg.lean_version,
            lean_executable_sha256=arg.lean_executable_sha256,
        )
    else:
        result = consume_marton_witness(
            Path(arg.witness_dir),
            artifact=Path(arg.artifact),
            expected_head=arg.expected_head,
            expected_run_id=arg.expected_run_id,
            expected_run_attempt=arg.expected_run_attempt,
            out=Path(arg.out),
        )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
