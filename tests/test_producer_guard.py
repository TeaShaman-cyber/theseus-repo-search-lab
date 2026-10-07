import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from scripts import producer_guard
from scripts.producer_guard import (
    checkout_exact,
    main,
    run_exact_command,
    verify_checked_out_commit,
)
from theseus_repo_search.errors import RepoSearchError


class ProducerGuardTests(unittest.TestCase):
    @patch("scripts.producer_guard.subprocess.run")
    def test_checkout_failure_is_blocked_source_binding(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["git"])
        with self.assertRaises(RepoSearchError) as caught:
            checkout_exact("https://github.com/example/repo.git", "a" * 40, Path("target"))
        self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_BINDING")

    @patch("scripts.producer_guard.subprocess.run")
    def test_readback_mismatch_is_blocked_source_mismatch(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["git"], 0, stdout=("b" * 40) + "\n", stderr=""
        )
        with self.assertRaises(RepoSearchError) as caught:
            verify_checked_out_commit(Path("target"), "a" * 40)
        self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")

    @patch("scripts.producer_guard.subprocess.run")
    def test_exact_command_failure_is_degraded_extraction_unavailable(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["lake", "build"])
        with self.assertRaises(RepoSearchError) as caught:
            run_exact_command(["lake", "build", "Zeta23"], Path("target"))
        self.assertEqual(caught.exception.code, "DEGRADED_EXACT_EXTRACTION_UNAVAILABLE")

    def test_generic_extraction_dispatches_by_descriptor_kind(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            git_descriptor = root / "git.json"
            git_descriptor.write_text(
                json.dumps(
                    {
                        "schema": "theseus.lean-git-source.v1",
                        "source_id": "git-fixture",
                        "source_repo": "example/repo",
                        "source_commit": "a" * 40,
                        "source_subdir": "pkg",
                        "root_modules": ["Main"],
                        "build_target": "Main",
                    }
                ),
                encoding="utf-8",
            )
            archive_descriptor = root / "archive.json"
            archive_descriptor.write_text(
                json.dumps(
                    {
                        "schema": "theseus.lean-archive-source.v1",
                        "source_id": "archive-fixture",
                        "archive_url": "https://example.invalid/source.zip",
                        "archive_sha256": "e" * 64,
                        "archive_format": "zip",
                        "source_subdir": "pkg",
                        "root_modules": ["Main"],
                        "build_target": "Main",
                        "exclude_source_prefixes": [],
                    }
                ),
                encoding="utf-8",
            )
            common = {
                "argv": ["lake", "env", "lean"],
                "cwd": root / "source" / "pkg",
                "source_container": root / "source",
                "raw_depgraph": root / "raw.json",
                "receipt_path": root / "raw-receipt.json",
                "producer_kind": "lean-dep-viz",
                "producer_tool_repo": "cameronfreer/LeanDepViz",
                "producer_tool_commit": "b" * 40,
                "producer_tool_hash": "c" * 64,
            }
            with patch.object(producer_guard, "run_bound_extraction") as git_run, patch.object(
                producer_guard, "run_bound_archive_extraction"
            ) as archive_run:
                producer_guard.run_bound_source_extraction(
                    descriptor_path=git_descriptor,
                    materialization_receipt=None,
                    **common,
                )
                git_run.assert_called_once()
                archive_run.assert_not_called()

                git_run.reset_mock()
                producer_guard.run_bound_source_extraction(
                    descriptor_path=archive_descriptor,
                    materialization_receipt=root / "materialization.json",
                    **common,
                )
                archive_run.assert_called_once()
                git_run.assert_not_called()

    def test_archive_bound_extraction_writes_v3_without_fake_git_fields(self):
        import zipfile

        from scripts.materialize_archive_source import materialize_archive_source
        from theseus_repo_search.producer_config import LeanArchiveSource

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive_buffer = io.BytesIO()
            with zipfile.ZipFile(archive_buffer, "w") as archive:
                archive.writestr("pkg/lean-toolchain", "leanprover/lean4:v4.30.0\n")
                archive.writestr("pkg/Main.lean", "theorem ok : True := by trivial\n")
            archive_bytes = archive_buffer.getvalue()
            source = LeanArchiveSource.from_dict({
                "schema": "theseus.lean-archive-source.v1",
                "source_id": "fixture-archive",
                "archive_url": "https://example.invalid/source.zip",
                "archive_sha256": sha256(archive_bytes).hexdigest(),
                "archive_format": "zip",
                "source_subdir": "pkg",
                "root_modules": ["Main"],
                "build_target": "Main",
                "exclude_source_prefixes": [],
            })
            materialized = root / "source"
            materialization_receipt = root / "materialization.json"

            class Response(io.BytesIO):
                def __enter__(self):
                    return self
                def __exit__(self, exc_type, exc, tb):
                    self.close()

            materialize_archive_source(
                source,
                dest=materialized,
                receipt_path=materialization_receipt,
                opener=lambda *_args, **_kwargs: Response(archive_bytes),
            )
            raw = root / "raw.json"
            receipt = root / "raw-receipt.json"
            fresh = b'{"nodes":[],"edges":[]}\n'

            def fake_run(argv, cwd):
                raw.write_bytes(fresh)

            with patch.object(producer_guard, "run_exact_command", side_effect=fake_run):
                producer_guard.run_bound_archive_extraction(
                    ["lake", "env", "lean"],
                    cwd=materialized / "pkg",
                    materialization_root=materialized,
                    materialization_receipt=materialization_receipt,
                    source=source,
                    raw_depgraph=raw,
                    receipt_path=receipt,
                    producer_kind="lean-dep-viz",
                    producer_tool_repo="cameronfreer/LeanDepViz",
                    producer_tool_commit="b" * 40,
                    producer_tool_hash="c" * 64,
                )

            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "theseus.raw-depgraph-receipt.v3")
            self.assertEqual(payload["source"]["kind"], "archive")
            self.assertEqual(payload["source"]["sha256"], source.archive_sha256)
            self.assertNotIn("repo", payload["source"])
            self.assertNotIn("commit", payload["source"])
            self.assertIn("tree_sha256", payload["materialization"])
            self.assertIn("member_manifest_sha256", payload["materialization"])
            self.assertEqual(payload["raw_depgraph"]["sha256"], sha256(fresh).hexdigest())
            from theseus_repo_search.cli import _verify_archive_raw_depgraph_receipt

            _verify_archive_raw_depgraph_receipt(
                receipt,
                raw,
                source=source,
                tree_sha256=payload["materialization"]["tree_sha256"],
                member_manifest_sha256=payload["materialization"]["member_manifest_sha256"],
                producer_kind="lean-dep-viz",
                producer_tool_repo="cameronfreer/LeanDepViz",
                producer_tool_commit="b" * 40,
                producer_tool_hash="c" * 64,
            )

    def test_archive_bound_extraction_rejects_existing_receipt_before_command(self):
        from theseus_repo_search.producer_config import LeanArchiveSource

        source = LeanArchiveSource.from_dict({
            "schema": "theseus.lean-archive-source.v1",
            "source_id": "fixture-archive",
            "archive_url": "https://example.invalid/source.zip",
            "archive_sha256": "a" * 64,
            "archive_format": "zip",
            "source_subdir": ".",
            "root_modules": ["Main"],
            "build_target": "Main",
            "exclude_source_prefixes": [],
        })
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "lean-toolchain").write_text("leanprover/lean4:v4.30.0\n", encoding="utf-8")
            receipt = root.parent / f"{root.name}-existing-raw-receipt.json"
            receipt.write_text("existing\n", encoding="utf-8")
            try:
                with patch.object(
                    producer_guard, "run_exact_command"
                ) as run, self.assertRaises(RepoSearchError) as caught:
                    producer_guard.run_bound_archive_extraction(
                        ["lake", "env", "lean"],
                        cwd=root,
                        materialization_root=root,
                        materialization_receipt=root / "unused.json",
                        source=source,
                        raw_depgraph=root.parent / f"{root.name}-raw.json",
                        receipt_path=receipt,
                        producer_kind="lean-dep-viz",
                        producer_tool_repo="cameronfreer/LeanDepViz",
                        producer_tool_commit="b" * 40,
                        producer_tool_hash="c" * 64,
                    )
                self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_BINDING")
                self.assertEqual(receipt.read_text(encoding="utf-8"), "existing\n")
                run.assert_not_called()
            finally:
                receipt.unlink(missing_ok=True)

    def test_archive_bound_extraction_blocks_member_drift_before_command(self):
        import zipfile

        from scripts.materialize_archive_source import materialize_archive_source
        from theseus_repo_search.producer_config import LeanArchiveSource

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("pkg/lean-toolchain", "leanprover/lean4:v4.30.0\n")
            archive.writestr("pkg/Main.lean", "theorem ok : True := by trivial\n")
        archive_bytes = archive_buffer.getvalue()
        source = LeanArchiveSource.from_dict({
            "schema": "theseus.lean-archive-source.v1",
            "source_id": "fixture-archive",
            "archive_url": "https://example.invalid/source.zip",
            "archive_sha256": sha256(archive_bytes).hexdigest(),
            "archive_format": "zip",
            "source_subdir": "pkg",
            "root_modules": ["Main"],
            "build_target": "Main",
            "exclude_source_prefixes": [],
        })

        for label in ("mutate", "remove", "type-change"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                materialized = root / "source"
                materialization_receipt = root / "materialization.json"

                class Response(io.BytesIO):
                    def __enter__(self):
                        return self
                    def __exit__(self, exc_type, exc, tb):
                        self.close()

                materialize_archive_source(
                    source,
                    dest=materialized,
                    receipt_path=materialization_receipt,
                    opener=lambda *_args, **_kwargs: Response(archive_bytes),
                )
                target = materialized / "pkg" / "Main.lean"
                if label == "mutate":
                    target.write_text("tampered\n", encoding="utf-8")
                elif label == "remove":
                    target.unlink()
                else:
                    target.unlink()
                    target.mkdir()

                raw = root / "raw.json"
                receipt = root / "raw-receipt.json"
                with patch.object(
                    producer_guard, "run_exact_command"
                ) as run, self.assertRaises(RepoSearchError) as caught:
                    producer_guard.run_bound_archive_extraction(
                    ["lake", "env", "lean"],
                    cwd=materialized / "pkg",
                    materialization_root=materialized,
                    materialization_receipt=materialization_receipt,
                    source=source,
                    raw_depgraph=raw,
                    receipt_path=receipt,
                    producer_kind="lean-dep-viz",
                    producer_tool_repo="cameronfreer/LeanDepViz",
                    producer_tool_commit="b" * 40,
                    producer_tool_hash="c" * 64,
                )
                self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")
                run.assert_not_called()
                self.assertFalse(receipt.exists())

    def test_archive_bound_extraction_blocks_member_drift_after_command(self):
        import zipfile

        from scripts.materialize_archive_source import materialize_archive_source
        from theseus_repo_search.producer_config import LeanArchiveSource

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive_buffer = io.BytesIO()
            with zipfile.ZipFile(archive_buffer, "w") as archive:
                archive.writestr("pkg/lean-toolchain", "leanprover/lean4:v4.30.0\n")
                archive.writestr("pkg/Main.lean", "theorem ok : True := by trivial\n")
            archive_bytes = archive_buffer.getvalue()
            source = LeanArchiveSource.from_dict({
                "schema": "theseus.lean-archive-source.v1",
                "source_id": "fixture-archive",
                "archive_url": "https://example.invalid/source.zip",
                "archive_sha256": sha256(archive_bytes).hexdigest(),
                "archive_format": "zip",
                "source_subdir": "pkg",
                "root_modules": ["Main"],
                "build_target": "Main",
                "exclude_source_prefixes": [],
            })
            materialized = root / "source"
            materialization_receipt = root / "materialization.json"

            class Response(io.BytesIO):
                def __enter__(self):
                    return self
                def __exit__(self, exc_type, exc, tb):
                    self.close()

            materialize_archive_source(
                source,
                dest=materialized,
                receipt_path=materialization_receipt,
                opener=lambda *_args, **_kwargs: Response(archive_bytes),
            )
            raw = root / "raw.json"
            receipt = root / "raw-receipt.json"

            def dirty_run(argv, cwd):
                raw.write_text('{"nodes":[],"edges":[]}\n', encoding="utf-8")
                (materialized / "pkg" / "Main.lean").write_text(
                    "tampered during extraction\n", encoding="utf-8"
                )

            with patch.object(
                producer_guard, "run_exact_command", side_effect=dirty_run
            ), self.assertRaises(RepoSearchError) as caught:
                producer_guard.run_bound_archive_extraction(
                        ["lake", "env", "lean"],
                        cwd=materialized / "pkg",
                        materialization_root=materialized,
                        materialization_receipt=materialization_receipt,
                        source=source,
                        raw_depgraph=raw,
                        receipt_path=receipt,
                        producer_kind="lean-dep-viz",
                        producer_tool_repo="cameronfreer/LeanDepViz",
                        producer_tool_commit="b" * 40,
                        producer_tool_hash="c" * 64,
                    )
            self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_MISMATCH")
            self.assertFalse(receipt.exists())

    def test_bound_extraction_requires_cwd_to_equal_verified_source_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            source = repo / "zeta23"
            other = root / "other"
            source.mkdir(parents=True)
            other.mkdir()
            (source / "lean-toolchain").write_text("leanprover/lean4:v4.33.0\n", encoding="utf-8")
            raw = root / "raw.json"
            receipt = root / "receipt.json"
            with patch.object(producer_guard, "verify_checked_out_commit", return_value="a" * 40), \
                 patch.object(producer_guard, "verify_tracked_source_clean"), \
                 patch.object(producer_guard, "run_exact_command") as run, \
                 self.assertRaisesRegex(RepoSearchError, "extraction cwd"):
                producer_guard.run_bound_extraction(
                        ["lake", "env", "lean"],
                        cwd=other,
                        repo_dir=repo,
                        expected_commit="a" * 40,
                        raw_depgraph=raw,
                        receipt_path=receipt,
                        source_repo="example/repo",
                        source_subdir="zeta23",
                        root_modules=("Zeta23",),
                        producer_kind="lean-dep-viz",
                        producer_tool_repo="cameronfreer/LeanDepViz",
                        producer_tool_commit="b" * 40,
                        producer_tool_hash="c" * 64,
                    )
            run.assert_not_called()
            self.assertFalse(receipt.exists())

    def test_bound_extraction_replaces_stale_graph_and_writes_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "zeta23"
            source.mkdir()
            raw = root / "raw.json"
            receipt = root / "receipt.json"
            (source / "lean-toolchain").write_text("leanprover/lean4:v4.33.0\n", encoding="utf-8")
            raw.write_text("stale", encoding="utf-8")
            fresh = b'{"nodes":[],"edges":[]}\n'

            def fake_run(argv, cwd):
                self.assertFalse(raw.exists())
                raw.write_bytes(fresh)

            with patch.object(producer_guard, "verify_checked_out_commit", return_value="a" * 40) as verify, \
                 patch.object(producer_guard, "verify_tracked_source_clean") as clean, \
                 patch.object(producer_guard, "run_exact_command", side_effect=fake_run):
                producer_guard.run_bound_extraction(
                    ["lake", "env", "lean"],
                    cwd=source,
                    repo_dir=root,
                    expected_commit="a" * 40,
                    raw_depgraph=raw,
                    receipt_path=receipt,
                    source_repo="example/repo",
                    source_subdir="zeta23",
                    root_modules=("Zeta23",),
                    producer_kind="lean-dep-viz",
                    producer_tool_repo="cameronfreer/LeanDepViz",
                    producer_tool_commit="b" * 40,
                    producer_tool_hash="c" * 64,
                )
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(clean.call_count, 2)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "theseus.raw-depgraph-receipt.v2")
            self.assertEqual(payload["source"]["commit"], "a" * 40)
            self.assertEqual(payload["observed"], {"lean_toolchain": "leanprover/lean4:v4.33.0"})
            self.assertEqual(payload["raw_depgraph"]["sha256"], sha256(fresh).hexdigest())

    def test_bound_extraction_blocks_dirty_tracked_source_before_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            source = repo / "zeta23"
            source.mkdir(parents=True)
            tracked = source / "Tiny.lean"
            tracked.write_text("theorem a : True := by trivial\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Repo Search Test"], check=True)
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True)
            commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
            tracked.write_text("theorem a : False := by contradiction\n", encoding="utf-8")
            raw = root / "raw.json"
            receipt = root / "receipt.json"

            with self.assertRaises(RepoSearchError) as caught:
                producer_guard.run_bound_extraction(
                    ["python3", "-c", f"from pathlib import Path; Path({str(raw)!r}).write_text('{{\"nodes\":[],\"edges\":[]}}\\n')"],
                    cwd=source,
                    repo_dir=repo,
                    expected_commit=commit,
                    raw_depgraph=raw,
                    receipt_path=receipt,
                    source_repo="example/repo",
                    source_subdir="zeta23",
                    root_modules=("Zeta23",),
                    producer_kind="lean-dep-viz",
                    producer_tool_repo="cameronfreer/LeanDepViz",
                    producer_tool_commit="b" * 40,
                    producer_tool_hash="c" * 64,
                )
            self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertIn("tracked source worktree is dirty", str(caught.exception))
            self.assertFalse(receipt.exists())

    def test_bound_extraction_blocks_dirty_tracked_source_after_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            source = repo / "zeta23"
            source.mkdir(parents=True)
            tracked = source / "Tiny.lean"
            tracked.write_text("theorem a : True := by trivial\n", encoding="utf-8")
            (source / "lean-toolchain").write_text("leanprover/lean4:v4.33.0\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Repo Search Test"], check=True)
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True)
            commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
            raw = root / "raw.json"
            receipt = root / "receipt.json"

            def dirty_run(argv, cwd):
                tracked.write_text("theorem a : False := by contradiction\n", encoding="utf-8")
                raw.write_text('{"nodes":[],"edges":[]}\n', encoding="utf-8")

            with patch.object(producer_guard, "run_exact_command", side_effect=dirty_run), \
                 self.assertRaises(RepoSearchError) as caught:
                producer_guard.run_bound_extraction(
                        ["lake", "env", "lean"],
                        cwd=source,
                        repo_dir=repo,
                        expected_commit=commit,
                        raw_depgraph=raw,
                        receipt_path=receipt,
                        source_repo="example/repo",
                        source_subdir="zeta23",
                        root_modules=("Zeta23",),
                        producer_kind="lean-dep-viz",
                        producer_tool_repo="cameronfreer/LeanDepViz",
                        producer_tool_commit="b" * 40,
                        producer_tool_hash="c" * 64,
                    )
            self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertIn("tracked source worktree is dirty", str(caught.exception))
            self.assertFalse(receipt.exists())

    def test_bound_extraction_rejects_symlinked_lean_toolchain(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            outside = root.parent / f"{root.name}-outside-toolchain"
            outside.write_text("leanprover/lean4:v4.33.0\n", encoding="utf-8")
            (root / "lean-toolchain").symlink_to(outside)
            raw = root / "raw.json"
            receipt = root / "receipt.json"

            def fake_run(argv, cwd):
                raw.write_text('{"nodes":[],"edges":[]}\n', encoding="utf-8")

            try:
                with patch.object(producer_guard, "verify_checked_out_commit", return_value="a" * 40), \
                     patch.object(producer_guard, "verify_tracked_source_clean"), \
                     patch.object(producer_guard, "run_exact_command", side_effect=fake_run), \
                     self.assertRaises(RepoSearchError) as caught:
                    producer_guard.run_bound_extraction(
                            ["lake", "env", "lean"],
                            cwd=root,
                            repo_dir=root,
                            expected_commit="a" * 40,
                            raw_depgraph=raw,
                            receipt_path=receipt,
                            source_repo="example/repo",
                            source_subdir="",
                            root_modules=("Root",),
                            producer_kind="lean-dep-viz",
                            producer_tool_repo="cameronfreer/LeanDepViz",
                            producer_tool_commit="b" * 40,
                            producer_tool_hash="c" * 64,
                        )
                self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_BINDING")
                self.assertIn("lean-toolchain", str(caught.exception))
                self.assertFalse(raw.exists())
                self.assertFalse(receipt.exists())
            finally:
                outside.unlink(missing_ok=True)

    def test_bound_extraction_requires_nonempty_lean_toolchain(self):
        for label, content in (("missing", None), ("empty", "\n")):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                source = root / "zeta23"
                source.mkdir()
                raw = root / "raw.json"
                receipt = root / "receipt.json"
                if content is not None:
                    (source / "lean-toolchain").write_text(content, encoding="utf-8")

                def fake_run(argv, cwd, raw=raw):
                    raw.write_text('{"nodes":[],"edges":[]}\n', encoding="utf-8")

                with patch.object(producer_guard, "verify_checked_out_commit", return_value="a" * 40), \
                     patch.object(producer_guard, "verify_tracked_source_clean"), \
                     patch.object(producer_guard, "run_exact_command", side_effect=fake_run), \
                     self.assertRaises(RepoSearchError) as caught:
                    producer_guard.run_bound_extraction(
                            ["lake", "env", "lean"],
                            cwd=source,
                            repo_dir=root,
                            expected_commit="a" * 40,
                            raw_depgraph=raw,
                            receipt_path=receipt,
                            source_repo="example/repo",
                            source_subdir="zeta23",
                            root_modules=("Zeta23",),
                            producer_kind="lean-dep-viz",
                            producer_tool_repo="cameronfreer/LeanDepViz",
                            producer_tool_commit="b" * 40,
                            producer_tool_hash="c" * 64,
                        )
                self.assertEqual(caught.exception.code, "BLOCKED_SOURCE_BINDING")
                self.assertIn("lean-toolchain", str(caught.exception))
                self.assertFalse(receipt.exists())

    @patch("scripts.producer_guard.verify_checked_out_commit")
    def test_cli_emits_one_machine_error_to_stderr(self, verify):
        verify.side_effect = RepoSearchError("BLOCKED_SOURCE_MISMATCH", "sha mismatch")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["verify", "--repo-dir", "target", "--expected", "a" * 40])
        self.assertNotEqual(code, 0)
        self.assertEqual(
            stderr.getvalue(),
            '{"code":"BLOCKED_SOURCE_MISMATCH","message":"sha mismatch","status":"BLOCKED"}\n',
        )
