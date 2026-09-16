import os
import tempfile
import subprocess
import unittest
from hashlib import sha256
from pathlib import Path

from theseus_repo_search.model import Node
from theseus_repo_search.sources import bind_node_sources, scan_lean_sources


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

    def test_theorem_shaped_lines_inside_block_comments_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "Commented.lean"
            path.write_text(
                "namespace Zeta23.Commented\n"
                "/-! STATEMENTS:\n"
                "  theorem fake_one : True\n"
                "  lemma fake_two : True\n"
                "-/\n"
                "theorem real_one : True := by trivial\n"
                "end Zeta23.Commented\n",
                encoding="utf-8",
            )
            chunks = scan_lean_sources(root, source_commit="abc123")
            self.assertEqual([chunk.declaration_hint for chunk in chunks], ["real_one"])

    def test_module_path_disambiguates_same_short_declaration_name(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Zeta23").mkdir()
            (root / "Zeta23" / "A.lean").write_text(
                "theorem shared : True := by trivial\n", encoding="utf-8"
            )
            (root / "Zeta23" / "B.lean").write_text(
                "theorem shared : True := by trivial\n", encoding="utf-8"
            )
            chunks = scan_lean_sources(root, source_commit="abc123")
            node = Node.from_lean(
                full_name="Zeta23.A.shared",
                name="shared",
                kind="thm",
                module="Zeta23.A",
                source_commit="abc123",
            )
            bound = bind_node_sources([node], chunks)[0]
            self.assertEqual(bound.source_path, "Zeta23/A.lean")
            self.assertEqual((bound.source_start_line, bound.source_end_line), (1, 1))

    def test_tracked_only_scan_excludes_lake_and_untracked_lean_files(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            source = repo / "zeta23"
            tracked = source / "Zeta23" / "Main.lean"
            tracked.parent.mkdir(parents=True)
            tracked.write_text("theorem tracked_decl : True := by trivial\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Repo Search Test"], check=True)
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True)
            generated = source / ".lake" / "packages" / "Fake" / "Fake.lean"
            generated.parent.mkdir(parents=True)
            generated.write_text("theorem generated_decl : True := by trivial\n", encoding="utf-8")
            untracked = source / "Zeta23" / "Scratch.lean"
            untracked.write_text("theorem scratch_decl : True := by trivial\n", encoding="utf-8")

            chunks = scan_lean_sources(source, source_commit="abc123", tracked_only=True)
            self.assertEqual([chunk.declaration_hint for chunk in chunks], ["tracked_decl"])
            self.assertEqual([chunk.source_path for chunk in chunks], ["Zeta23/Main.lean"])

    def test_tracked_only_scan_accepts_relative_source_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            source = repo / "zeta23"
            tracked = source / "Zeta23" / "Main.lean"
            tracked.parent.mkdir(parents=True)
            tracked.write_text("theorem tracked_decl : True := by trivial\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Repo Search Test"], check=True)
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True)

            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                chunks = scan_lean_sources(
                    Path("repo/zeta23"), source_commit="abc123", tracked_only=True
                )
            finally:
                os.chdir(original_cwd)

            self.assertEqual([chunk.declaration_hint for chunk in chunks], ["tracked_decl"])
            self.assertEqual([chunk.source_path for chunk in chunks], ["Zeta23/Main.lean"])
