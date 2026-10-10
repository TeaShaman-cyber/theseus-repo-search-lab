"""Strictly extract declaration-specific `#print axioms` records from Lean stdout.

This is a parser of *reported* data only. An external runner must independently
pin the complete stdout bytes and attest the toolchain, source, execution and
exit status. A hash passed by the author of the same stdout is no attestation.
"""

from __future__ import annotations

import hashlib
import re

from .proof_evidence_sidecar import _axioms_from_exact_report

MAX_STDOUT_BYTES = 1024 * 1024
_SORRY_WARNING = re.compile(r"^.+:[0-9]+:[0-9]+: warning: declaration uses `sorry`$")


def extract_exact_axiom_reports(
    raw_stdout: bytes,
    *,
    expected_declarations: tuple[str, ...],
    exit_code: int,
    expected_stdout_sha256: str | None,
) -> dict[str, bytes]:
    """Verify exact stdout pin and isolate one valid report per expected declaration.

    Only the previously observed `sorry` diagnostic is recognized; unknown
    stdout lines, additional declarations and duplicated claims fail closed.
    This does NOT certify Lean execution or mathematical correctness.
    """
    if exit_code != 0:
        raise ValueError("LEAN_EXECUTION_FAILED: nonzero exit")
    if (
        not isinstance(expected_stdout_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_stdout_sha256) is None
    ):
        raise ValueError("UNPINNED_STDOUT: missing exact expected digest")
    if not raw_stdout or len(raw_stdout) > MAX_STDOUT_BYTES:
        raise ValueError("invalid Lean stdout length")
    if hashlib.sha256(raw_stdout).hexdigest() != expected_stdout_sha256:
        raise ValueError("LEAN_STDOUT_DIGEST_MISMATCH")
    if not expected_declarations or len(set(expected_declarations)) != len(expected_declarations):
        raise ValueError("invalid expected declarations")
    if any(
        not isinstance(decl, str)
        or not decl
        or any(control in decl for control in ("\n", "\r", "\x00"))
        for decl in expected_declarations
    ):
        raise ValueError("invalid declaration name")
    try:
        stdout = raw_stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("non-UTF-8 Lean stdout") from exc
    if not stdout.endswith("\n") or "\r" in stdout or "\x00" in stdout:
        raise ValueError("invalid Lean stdout framing")

    reports: dict[str, bytes] = {}
    sorry_warnings = 0
    for line in stdout.splitlines():
        if _SORRY_WARNING.fullmatch(line):
            sorry_warnings += 1
            continue
        # Lean quotes the *whole* declaration, but a valid Lean name may
        # itself end in apostrophes: 'Example.foo'' is a valid report.
        # Anchor parsing to an expected name and its exact closing delimiter.
        matched = [
            declaration for declaration in expected_declarations
            if line.startswith(f"'{declaration}' ")
        ]
        if len(matched) != 1:
            raise ValueError("unrequested or ambiguous Lean declaration")
        declaration = matched[0]
        if declaration in reports:
            raise ValueError("duplicate Lean axiom report")
        report_bytes = (line + "\n").encode("utf-8")
        detail = line[len(f"'{declaration}' ") :]
        if detail == "depends on axioms: []":
            raise ValueError("invalid empty Lean axiom list")
        _axioms_from_exact_report(report_bytes, declaration)
        reports[declaration] = report_bytes
    if set(reports) != set(expected_declarations):
        raise ValueError("missing Lean axiom report")
    if sorry_warnings and not any(b"sorryAx" in item for item in reports.values()):
        raise ValueError("unexplained sorry warning")
    return reports
