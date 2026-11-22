import hashlib
import unittest

from scripts.adapt_leandepviz_lean436 import adapt_source


class LeanDepVizLean436AdapterTests(unittest.TestCase):
    def test_rewrites_only_the_three_pinned_string_trim_sites(self):
        source = b"""private def nameFromString (s : String) : Name :=
  (s.splitOn ".").foldl (init := Name.anonymous) fun acc part =>
    let part := part.trim
    if part.isEmpty then acc else Name.str acc part

let names := roots.splitOn "," |>.map String.trim |>.filter (\xc2\xb7 \xe2\x89\xa0 "")
let prefixes := pref.splitOn "," |>.map String.trim |>.filter (\xc2\xb7 \xe2\x89\xa0 "")
"""
        digest = hashlib.sha256(source).hexdigest()

        adapted = adapt_source(source, expected_sha256=digest).decode()

        self.assertIn("let part := part.trimAscii.copy", adapted)
        self.assertEqual(
            adapted.count("|>.map (fun s => s.trimAscii.copy) |>.filter"),
            2,
        )
        self.assertNotIn("String.trim", adapted)
        self.assertNotIn("part.trim\n", adapted)

    def test_rejects_unexpected_input_hash_before_rewrite(self):
        source = b"let part := part.trim\n"

        with self.assertRaisesRegex(ValueError, "input SHA-256 mismatch"):
            adapt_source(source, expected_sha256="0" * 64)

    def test_rejects_changed_upstream_rewrite_shape(self):
        source = b"""let part := part.trim
let names := roots.splitOn "," |>.map String.trim |>.filter (\xc2\xb7 \xe2\x89\xa0 "")
"""
        digest = hashlib.sha256(source).hexdigest()

        with self.assertRaisesRegex(ValueError, "expected exactly 2 occurrences"):
            adapt_source(source, expected_sha256=digest)


if __name__ == "__main__":
    unittest.main()
