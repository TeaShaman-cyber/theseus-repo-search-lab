"""Bounded parsing of actual Lean output, not an independent proof attestation."""

import hashlib
import unittest
from pathlib import Path

from theseus_repo_search.lean_axioms_output import extract_exact_axiom_reports

FIXTURES = Path(__file__).parent / "fixtures" / "lean_axioms"
DECLS = ("EvidenceClassCanary.fully_proved", "EvidenceClassCanary.admitted")
SOURCE_SHA256 = "d92d2482c12a116d254feba6e09c275dab183e4a3bf9d1ebf6aa3d48260342b7"
STDOUT_SHA256 = "8457dbb96c6f46cd229f789ec0eda55f3d11387e2a7cfcdf4d839f95d254366e"


def parse_with_test_pin(raw: bytes, *, expected_declarations: tuple[str, ...], exit_code: int, pin: str | None = None):
    """Test-only pin; a production caller must obtain it independently."""
    return extract_exact_axiom_reports(
        raw,
        expected_declarations=expected_declarations,
        exit_code=exit_code,
        expected_stdout_sha256=pin or hashlib.sha256(raw).hexdigest(),
    )


class LeanAxiomStdoutTests(unittest.TestCase):
    def setUp(self):
        self.actual = (FIXTURES / "actual-lean-4.33.0-rc2.stdout").read_bytes()

    def test_genuine_observation_is_frozen_and_split_without_warning_loss(self):
        self.assertEqual(hashlib.sha256((FIXTURES / "AxiomProbe.lean").read_bytes()).hexdigest(), SOURCE_SHA256)
        self.assertEqual(hashlib.sha256(self.actual).hexdigest(), STDOUT_SHA256)
        result = parse_with_test_pin(self.actual, expected_declarations=DECLS, exit_code=0, pin=STDOUT_SHA256)
        self.assertEqual(result[DECLS[0]], f"'{DECLS[0]}' does not depend on any axioms\n".encode())
        self.assertEqual(result[DECLS[1]], f"'{DECLS[1]}' depends on axioms: [sorryAx]\n".encode())

    def test_wrong_or_missing_output_fails_closed(self):
        for stdout in (b"", b"random\n", f"'{DECLS[0]}' does not depend on any axioms\n".encode()):
            with self.subTest(stdout=stdout), self.assertRaises(ValueError):
                parse_with_test_pin(stdout, expected_declarations=DECLS, exit_code=0)

    def test_doubled_report_fails_even_when_both_lines_are_identical(self):
        repeated = self.actual + f"'{DECLS[1]}' depends on axioms: [sorryAx]\n".encode()
        with self.assertRaises(ValueError):
            parse_with_test_pin(repeated, expected_declarations=DECLS, exit_code=0)

    def test_unrequested_declaration_is_not_silently_ignored(self):
        rogue = self.actual + b"'NotRequested.fake' does not depend on any axioms\n"
        with self.assertRaises(ValueError):
            parse_with_test_pin(rogue, expected_declarations=DECLS, exit_code=0)

    def test_nonzero_lean_exit_fails_even_when_reports_look_good(self):
        with self.assertRaises(ValueError):
            parse_with_test_pin(self.actual, expected_declarations=DECLS, exit_code=1)

    def test_warning_and_error_mutations_fail_closed(self):
        edits = (
            self.actual.replace(b"warning: declaration uses `sorry`", b"error: type mismatch"),
            self.actual.replace(b"warning: declaration uses `sorry`", b"warning: unknown elaboration anomaly"),
            self.actual + b"hello: unexpected line\n",
        )
        for edited in edits:
            with self.subTest(edited=edited[:60]), self.assertRaises(ValueError):
                parse_with_test_pin(edited, expected_declarations=DECLS, exit_code=0)

    def test_duplicate_expected_declaration_is_error(self):
        with self.assertRaises(ValueError):
            parse_with_test_pin(self.actual, expected_declarations=(DECLS[0], DECLS[0]), exit_code=0)

    def test_missing_external_stdout_pin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "UNPINNED_STDOUT"):
            extract_exact_axiom_reports(
                self.actual, expected_declarations=DECLS, exit_code=0,
                expected_stdout_sha256=None,
            )

    def test_missing_or_surplus_sorry_axiom_is_not_masked(self):
        wrong = self.actual.replace(b"[sorryAx]", b"[]")
        with self.assertRaises(ValueError):
            parse_with_test_pin(wrong, expected_declarations=DECLS, exit_code=0, pin=STDOUT_SHA256)


if __name__ == "__main__":
    unittest.main()
