"""Compositional lexical invariants, not a second Lean grammar."""
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from theseus_repo_search.sources import _chunks_from_text

PROPERTY_SETTINGS = settings(max_examples=200, deadline=None, derandomize=True, database=None)

NAMES = st.sampled_from(["Alpha", "Beta", "Gamma", "Delta", "Ω", "Zeta"])
WRAPPERS = st.sampled_from(["block", "string", "raw", "interpolation", "quotation", "identifier"])


def fragment(kind, i, payload):
    name = f"marker{i}"
    if kind == "block":
        return f"/- outer /- nested -/\n{payload}\n-/\n", []
    if kind == "string":
        return f'def {name} := "start\n{payload}\nfinish"\n', [name]
    if kind == "raw":
        return f'def {name} := r##"start "\n{payload}\nfinish"##\n', [name]
    if kind == "interpolation":
        return f'def {name} := s!"{{"start\n{payload}\nfinish"}}"\n', [name]
    if kind == "quotation":
        return f"def {name} := {chr(96)}(command|\n{payload})\n", [name]
    return f'def «hidden{i}"» := 1\n', []


class SourceProperties(unittest.TestCase):
    @PROPERTY_SETTINGS
    @given(st.lists(WRAPPERS, min_size=1, max_size=6), NAMES)
    def test_non_command_payload_cannot_invent_or_hide_real_declarations(self, wrappers, scope):
        parts = ["namespace Real\n", "theorem D.first : True := by trivial\n"]
        expected = ["Real.D.first"]
        for i, kind in enumerate(wrappers):
            payload = f"namespace Fake{i}\ntheorem Ghost.x : True := by trivial\nend Fake{i}"
            text, names = fragment(kind, i, payload)
            parts.append(text)
            expected.extend(names)
        parts.extend([f"namespace {scope}\n", "theorem D.last : True := by trivial\n",
                      f"end {scope}\nend Real\n"])
        expected.append(f"Real.{scope}.D.last")
        text = "".join(parts)
        chunks = _chunks_from_text("Fixture.lean", text, source_revision="fixture")
        self.assertEqual([c.declaration_hint for c in chunks], expected)
        lines = text.splitlines(keepends=True)
        for c in chunks:
            self.assertEqual(c.text, "".join(lines[c.source_start_line-1:c.source_end_line]))
        last = chunks[-1]
        self.assertIn("theorem D.last", last.text)

    @PROPERTY_SETTINGS
    @given(NAMES, NAMES, st.booleans())
    def test_namespace_section_composition_preserves_exact_names(self, outer, inner, combined_end):
        close = f"end {inner}.Local\n" if combined_end else f"end Local\nend {inner}\n"
        text = (f"namespace {outer}\nnamespace {inner}\nsection Local\n"
                "theorem D.first : True := by trivial\n" + close +
                "theorem D.last : True := by trivial\n" + f"end {outer}\n")
        chunks = _chunks_from_text("Fixture.lean", text, source_revision="fixture")
        self.assertEqual([c.declaration_hint for c in chunks],
                         [f"{outer}.{inner}.D.first", f"{outer}.D.last"])
