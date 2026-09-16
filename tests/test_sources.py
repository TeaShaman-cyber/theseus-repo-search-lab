import unittest
from hashlib import sha256
from pathlib import Path

from theseus_repo_search.sources import scan_lean_sources


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "lean_src"


class SourceChunkTests(unittest.TestCase):
    def test_extracts_declarations_with_exact_provenance_and_hash(self):
        chunks = scan_lean_sources(FIXTURE_ROOT, source_commit="abc123")
        self.assertEqual(len(chunks), 2)
        first = chunks[0]
        self.assertEqual(first.declaration_hint, "lemmaR_tight_two")
        self.assertEqual(first.source_path, "Zeta23/Tiny.lean")
        self.assertEqual((first.source_start_line, first.source_end_line), (2, 5))
        self.assertIn("rank trace tightness", first.text)
        self.assertEqual(first.content_sha256, sha256(first.text.encode()).hexdigest())
        self.assertEqual(first.source_commit, "abc123")

    def test_chunks_do_not_overlap_and_second_runs_to_eof(self):
        chunks = scan_lean_sources(FIXTURE_ROOT, source_commit="abc123")
        self.assertEqual(chunks[0].source_end_line + 1, chunks[1].source_start_line)
        self.assertEqual(chunks[1].declaration_hint, "N0star_lower_moment")
        self.assertEqual((chunks[1].source_start_line, chunks[1].source_end_line), (6, 11))
        self.assertIn("end Zeta23.Tiny", chunks[1].text)

    def test_chunk_ids_are_deterministic_and_sorted(self):
        chunks = scan_lean_sources(FIXTURE_ROOT, source_commit="abc123")
        self.assertEqual(
            [chunk.id for chunk in chunks],
            [
                "src:Zeta23/Tiny.lean:2:5",
                "src:Zeta23/Tiny.lean:6:11",
            ],
        )
        self.assertEqual(
            chunks,
            sorted(chunks, key=lambda chunk: (chunk.source_path, chunk.source_start_line, chunk.id)),
        )
