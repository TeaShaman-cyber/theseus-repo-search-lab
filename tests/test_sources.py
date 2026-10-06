import os
import tempfile
import subprocess
import unittest
from hashlib import sha256
from pathlib import Path

from theseus_repo_search.model import Node, SourceChunk
from theseus_repo_search.sources import (
    bind_node_sources,
    scan_lean_sources,
    scan_manifest_backed_lean_sources,
)


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

    def test_comment_delimiters_inside_strings_do_not_hide_following_declarations(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "Strings.lean"
            path.write_text(
                "def marker : String := \"/-\"\n"
                "theorem visible : True := by trivial\n",
                encoding="utf-8",
            )
            chunks = scan_lean_sources(root, source_commit="abc123")
            self.assertEqual(
                [chunk.declaration_hint for chunk in chunks],
                ["marker", "visible"],
            )

    def test_comment_delimiters_inside_line_comments_do_not_open_block_comments(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "LineComment.lean"
            path.write_text(
                "def marker : Nat := 0 -- /- not a block comment\n"
                "theorem visible : True := by trivial\n",
                encoding="utf-8",
            )
            chunks = scan_lean_sources(root, source_commit="abc123")
            self.assertEqual(
                [chunk.declaration_hint for chunk in chunks],
                ["marker", "visible"],
            )

    def test_nested_block_comments_still_hide_declaration_shaped_text(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "Nested.lean"
            path.write_text(
                "/- outer\n"
                "  /- theorem fake_nested : True -/\n"
                "  lemma fake_outer : True\n"
                "-/\n"
                "theorem visible : True := by trivial\n",
                encoding="utf-8",
            )
            chunks = scan_lean_sources(root, source_commit="abc123")
            self.assertEqual([chunk.declaration_hint for chunk in chunks], ["visible"])

    def test_declaration_after_closed_block_comment_on_same_line_is_scanned(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "CommentSuffix.lean"
            path.write_text(
                "/- doc -/ theorem visible : True := by trivial\n",
                encoding="utf-8",
            )
            chunks = scan_lean_sources(root, source_commit="abc123")
            self.assertEqual([chunk.declaration_hint for chunk in chunks], ["visible"])

    def test_declaration_after_multiline_block_comment_close_is_scanned(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "MultilineCommentSuffix.lean"
            path.write_text(
                "/- doc\n"
                "continued -/ theorem visible : True := by trivial\n",
                encoding="utf-8",
            )
            chunks = scan_lean_sources(root, source_commit="abc123")
            self.assertEqual([chunk.declaration_hint for chunk in chunks], ["visible"])

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

    def test_manifest_backed_scan_ignores_generated_unmanifested_lean(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            authoritative = root / "Main.lean"
            authoritative_bytes = b"theorem authoritative : True := by trivial\n"
            authoritative.write_bytes(authoritative_bytes)
            generated = root / "Generated.lean"
            generated.write_text(
                "theorem generated : True := by trivial\n", encoding="utf-8"
            )
            member_manifest = root / "members.json"
            member_manifest.write_text(
                json.dumps(
                    {
                        "schema": "theseus.archive-member-manifest.v1",
                        "source": {
                            "kind": "archive",
                            "url": "https://example.invalid/source.zip",
                            "sha256": "a" * 64,
                            "format": "zip",
                            "subdir": ".",
                        },
                        "source_root_relative": ".",
                        "members": [
                            {
                                "path": "Main.lean",
                                "source_path": "Main.lean",
                                "sha256": sha256(authoritative_bytes).hexdigest(),
                            }
                        ],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                {chunk.declaration_hint for chunk in scan_lean_sources(root, source_commit="a" * 64)},
                {"authoritative", "generated"},
            )
            chunks = scan_manifest_backed_lean_sources(
                root,
                source_revision="a" * 64,
                member_manifest_path=member_manifest,
            )
            self.assertEqual([chunk.declaration_hint for chunk in chunks], ["authoritative"])
            self.assertEqual([chunk.source_path for chunk in chunks], ["Main.lean"])
            self.assertTrue(all(chunk.source_revision == "a" * 64 for chunk in chunks))

    def test_manifest_backed_scan_rejects_member_changed_before_consumption(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            authoritative = root / "Main.lean"
            original = b"theorem authoritative : True := by trivial\n"
            authoritative.write_bytes(original)
            member_manifest = root / "members.json"
            member_manifest.write_text(
                json.dumps(
                    {
                        "schema": "theseus.archive-member-manifest.v1",
                        "source": {
                            "kind": "archive",
                            "url": "https://example.invalid/source.zip",
                            "sha256": "a" * 64,
                            "format": "zip",
                            "subdir": ".",
                        },
                        "source_root_relative": ".",
                        "members": [
                            {
                                "path": "Main.lean",
                                "source_path": "Main.lean",
                                "sha256": sha256(original).hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            authoritative.write_text(
                "theorem authoritative : False := by contradiction\n",
                encoding="utf-8",
            )

            from theseus_repo_search.errors import RepoSearchError

            with self.assertRaises(RepoSearchError) as caught:
                scan_manifest_backed_lean_sources(
                    root,
                    source_revision="a" * 64,
                    member_manifest_path=member_manifest,
                )
            self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")
            self.assertIn("Main.lean", str(caught.exception))

    def test_manifest_backed_scan_rejects_type_changed_source_at_consumption(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "Main.lean"
            data = b"theorem authoritative : True := by trivial\n"
            target.write_bytes(data)
            manifest_path = root / "members.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema": "theseus.archive-member-manifest.v1",
                        "source": {
                            "kind": "archive",
                            "url": "https://example.invalid/source.zip",
                            "sha256": "a" * 64,
                            "format": "zip",
                            "subdir": ".",
                        },
                        "source_root_relative": ".",
                        "members": [
                            {
                                "path": "Main.lean",
                                "source_path": "Main.lean",
                                "sha256": sha256(data).hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            target.unlink()
            target.mkdir()
            from theseus_repo_search.errors import RepoSearchError

            with self.assertRaises(RepoSearchError) as caught:
                scan_manifest_backed_lean_sources(
                    root,
                    source_revision="a" * 64,
                    member_manifest_path=manifest_path,
                )
            self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")

    def test_manifest_backed_scan_rejects_changed_member_manifest_bytes(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data = b"theorem authoritative : True := by trivial\n"
            (root / "Main.lean").write_bytes(data)
            manifest_path = root / "members.json"
            payload = {
                "schema": "theseus.archive-member-manifest.v1",
                "source": {
                    "kind": "archive",
                    "url": "https://example.invalid/source.zip",
                    "sha256": "a" * 64,
                    "format": "zip",
                    "subdir": ".",
                },
                "source_root_relative": ".",
                "members": [
                    {
                        "path": "Main.lean",
                        "source_path": "Main.lean",
                        "sha256": sha256(data).hexdigest(),
                    }
                ],
            }
            original = (
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            manifest_path.write_bytes(original)
            expected = sha256(original).hexdigest()
            payload["members"].append(
                {
                    "path": "Generated.lean",
                    "source_path": "Generated.lean",
                    "sha256": "0" * 64,
                }
            )
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            from theseus_repo_search.errors import RepoSearchError

            with self.assertRaises(RepoSearchError) as caught:
                scan_manifest_backed_lean_sources(
                    root,
                    source_revision="a" * 64,
                    member_manifest_path=manifest_path,
                    expected_manifest_sha256=expected,
                )
            self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")

    def test_manifest_backed_binding_accepts_qualified_declaration_hint_suffix(self):
        from theseus_repo_search.sources import bind_manifest_backed_node_sources

        text = "lemma AlmostDetSeq.det_prepend : True := by trivial\n"
        source = SourceChunk(
            id="src:Regular.lean:1:1",
            source_commit="a" * 64,
            source_path="Regular.lean",
            source_start_line=1,
            source_end_line=1,
            declaration_hint="AlmostDetSeq.det_prepend",
            text=text,
            content_sha256=sha256(text.encode()).hexdigest(),
        )
        node = Node.from_lean(
            full_name="DCR.AlmostDetSeq.det_prepend",
            name="det_prepend",
            kind="lemma",
            module="Regular",
            source_commit="a" * 64,
        )

        bound = bind_manifest_backed_node_sources([node], [source])
        self.assertEqual(bound[0].source_path, "Regular.lean")
        self.assertEqual(bound[0].source_start_line, 1)

    def test_manifest_backed_binding_rejects_ambiguous_suffix_matches(self):
        from theseus_repo_search.errors import RepoSearchError
        from theseus_repo_search.sources import bind_manifest_backed_node_sources

        chunks = [
            SourceChunk(
                id=f"src:Regular.lean:{line}:{line}",
                source_commit="a" * 64,
                source_path="Regular.lean",
                source_start_line=line,
                source_end_line=line,
                declaration_hint="det_prepend",
                text="lemma det_prepend : True := by trivial\n",
                content_sha256=sha256(b"lemma det_prepend : True := by trivial\n").hexdigest(),
            )
            for line in (1, 10)
        ]
        node = Node.from_lean(
            full_name="DCR.AlmostDetSeq.det_prepend",
            name="det_prepend",
            kind="lemma",
            module="Regular",
            source_commit="a" * 64,
        )

        with self.assertRaises(RepoSearchError) as caught:
            bind_manifest_backed_node_sources([node], chunks)
        self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")

    def test_manifest_backed_binding_rejects_unbacked_node_module(self):
        from theseus_repo_search.errors import RepoSearchError
        from theseus_repo_search.sources import bind_manifest_backed_node_sources

        text = "theorem authoritative : True := by trivial\n"
        source = SourceChunk(
            id="src:Main.lean:1:1",
            source_commit="a" * 64,
            source_path="Main.lean",
            source_start_line=1,
            source_end_line=1,
            declaration_hint="authoritative",
            text=text,
            content_sha256=sha256(text.encode()).hexdigest(),
        )
        generated = Node.from_lean(
            full_name="Generated.injected",
            name="injected",
            kind="thm",
            module="Generated",
            source_commit="a" * 64,
        )
        with self.assertRaises(RepoSearchError) as caught:
            bind_manifest_backed_node_sources([generated], [source])
        self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")
        self.assertIn("Generated.injected", str(caught.exception))

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
