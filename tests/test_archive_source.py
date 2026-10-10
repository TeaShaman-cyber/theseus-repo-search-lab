import io
import json
import stat
import tempfile
import unittest
import zipfile
from hashlib import sha256
from pathlib import Path
from unittest import mock

from scripts.materialize_archive_source import (
    materialize_archive_source,
    member_manifest_path_for_receipt,
    verify_materialized_archive_members,
)
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.producer_config import LeanArchiveSource, load_lean_source


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def payload(data: bytes, *, subdir: str = "pkg") -> dict[str, object]:
    return {
        "schema": "theseus.lean-archive-source.v1",
        "source_id": "fixture-archive",
        "archive_url": "https://example.invalid/source.zip",
        "archive_sha256": sha256(data).hexdigest(),
        "archive_format": "zip",
        "source_subdir": subdir,
        "root_modules": ["Main"],
        "build_target": "Main",
        "exclude_source_prefixes": [],
    }


class ArchiveDescriptorTests(unittest.TestCase):
    def test_loads_archive_descriptor(self):
        data = zip_bytes({"pkg/Main.lean": b"theorem ok : True := by trivial\n"})
        source = LeanArchiveSource.from_dict(payload(data))
        self.assertEqual(source.archive_format, "zip")
        self.assertEqual(source.root_modules, ("Main",))

    def test_generic_loader_dispatches_archive_schema(self):
        data = zip_bytes({"pkg/Main.lean": b"theorem ok : True := by trivial\n"})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source.json"
            path.write_text(json.dumps(payload(data)), encoding="utf-8")
            self.assertIsInstance(load_lean_source(path), LeanArchiveSource)

    def test_archive_descriptor_rejects_unsafe_or_ambiguous_authority(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        bad = [
            {"archive_url": "http://example.invalid/source.zip"},
            {"archive_url": "https://user:pass@example.invalid/source.zip"},
            {"archive_url": "https://example.invalid/source.zip#fragment"},
            {"archive_sha256": "a" * 40},
            {"archive_format": "tar"},
            {"source_subdir": "/absolute"},
        ]
        for patch in bad:
            with self.subTest(patch=patch):
                value = payload(data)
                value.update(patch)
                with self.assertRaises(RepoSearchError) as cm:
                    LeanArchiveSource.from_dict(value)
                self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")


class ArchiveMaterializerTests(unittest.TestCase):
    def test_materializes_verified_archive_and_writes_authority_receipt(self):
        data = zip_bytes({
            "pkg/lean-toolchain": b"leanprover/lean4:v4.30.0\n",
            "pkg/Main.lean": b"theorem ok : True := by trivial\n",
        })
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            receipt_path = root / "receipt.json"
            receipt = materialize_archive_source(
                source,
                dest=dest,
                receipt_path=receipt_path,
                opener=lambda *_args, **_kwargs: Response(data),
            )
            self.assertEqual((dest / "pkg" / "Main.lean").read_bytes(), b"theorem ok : True := by trivial\n")
            self.assertEqual(receipt["source"]["sha256"], source.archive_sha256)
            self.assertEqual(receipt["materialized"]["file_count"], 2)
            persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted, receipt)

    def test_materialization_persists_member_manifest_and_digest(self):
        data = zip_bytes({
            "pkg/lean-toolchain": b"leanprover/lean4:v4.30.0\n",
            "pkg/Main.lean": b"theorem ok : True := by trivial\n",
        })
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            receipt_path = root / "receipt.json"
            receipt = materialize_archive_source(
                source,
                dest=root / "source",
                receipt_path=receipt_path,
                opener=lambda *_args, **_kwargs: Response(data),
            )
            member_manifest_path = root / "receipt.members.json"
            manifest_bytes = member_manifest_path.read_bytes()
            member_manifest = json.loads(manifest_bytes)

            self.assertEqual(
                receipt["materialized"]["member_manifest_sha256"],
                sha256(manifest_bytes).hexdigest(),
            )
            self.assertEqual(member_manifest["schema"], "theseus.archive-member-manifest.v1")
            self.assertEqual(member_manifest["source"], receipt["source"])
            self.assertEqual(member_manifest["source_root_relative"], "pkg")
            self.assertEqual(
                member_manifest["members"],
                [
                    {
                        "path": "pkg/Main.lean",
                        "source_path": "Main.lean",
                        "sha256": sha256(b"theorem ok : True := by trivial\n").hexdigest(),
                    },
                    {
                        "path": "pkg/lean-toolchain",
                        "source_path": "lean-toolchain",
                        "sha256": sha256(b"leanprover/lean4:v4.30.0\n").hexdigest(),
                    },
                ],
            )
            self.assertEqual(
                verify_materialized_archive_members(
                    source, dest=root / "source", receipt_path=receipt_path
                ),
                member_manifest,
            )

    def test_member_manifest_and_receipt_are_output_path_independent(self):
        data = zip_bytes({
            "pkg/Main.lean": b"theorem ok : True := by trivial\n",
            "pkg/lean-toolchain": b"leanprover/lean4:v4.30.0\n",
        })
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snapshots = []
            for name in ("one", "two"):
                run_root = root / name
                receipt_path = run_root / "authority.json"
                materialize_archive_source(
                    source,
                    dest=run_root / "source",
                    receipt_path=receipt_path,
                    opener=lambda *_args, **_kwargs: Response(data),
                )
                snapshots.append(
                    (
                        receipt_path.read_bytes(),
                        member_manifest_path_for_receipt(receipt_path).read_bytes(),
                    )
                )
            self.assertEqual(snapshots[0], snapshots[1])

    def test_existing_member_manifest_target_fails_before_publication(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            receipt_path = root / "receipt.json"
            member_manifest_path = member_manifest_path_for_receipt(receipt_path)
            member_manifest_path.write_text("existing manifest\n", encoding="utf-8")
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=root / "source",
                    receipt_path=receipt_path,
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertFalse((root / "source").exists())
            self.assertFalse(receipt_path.exists())
            self.assertEqual(
                member_manifest_path.read_text(encoding="utf-8"), "existing manifest\n"
            )

    def test_persisted_member_manifest_rejects_tampered_member(self):
        data = zip_bytes({
            "pkg/Main.lean": b"theorem ok : True := by trivial\n",
            "pkg/lean-toolchain": b"leanprover/lean4:v4.30.0\n",
        })
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            receipt_path = root / "receipt.json"
            materialize_archive_source(
                source,
                dest=dest,
                receipt_path=receipt_path,
                opener=lambda *_args, **_kwargs: Response(data),
            )
            (dest / "pkg" / "Main.lean").write_bytes(b"tampered\n")

            with self.assertRaises(RepoSearchError) as cm:
                verify_materialized_archive_members(
                    source, dest=dest, receipt_path=receipt_path
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_MISMATCH")
            self.assertIn("pkg/Main.lean", str(cm.exception))

    def test_relative_destination_paths_are_supported(self):
        data = zip_bytes({"pkg/Main.lean": b"theorem ok : True := by trivial\n"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old = Path.cwd()
            try:
                import os
                os.chdir(root)
                receipt = materialize_archive_source(
                    source,
                    dest=Path("source"),
                    receipt_path=Path("receipt.json"),
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            finally:
                os.chdir(old)
            self.assertEqual(receipt["materialized"]["source_root_relative"], "pkg")
            self.assertTrue((root / "source" / "pkg" / "Main.lean").is_file())

    def test_checksum_mismatch_fails_before_publication(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source_data = payload(data)
        source_data["archive_sha256"] = "0" * 64
        source = LeanArchiveSource.from_dict(source_data)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=dest,
                    receipt_path=root / "receipt.json",
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_MISMATCH")
            self.assertFalse(dest.exists())

    def test_parent_traversal_member_fails_closed(self):
        data = zip_bytes({"escape/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data, subdir="escape"))
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../escape/Main.lean", b"x")
        unsafe_data = unsafe.getvalue()
        source_data = payload(unsafe_data, subdir="escape")
        source = LeanArchiveSource.from_dict(source_data)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=root / "source",
                    receipt_path=root / "receipt.json",
                    opener=lambda *_args, **_kwargs: Response(unsafe_data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")

    def test_declared_zip_member_type_must_match_directory_marker(self):
        cases = [
            ("pkg/", stat.S_IFREG | 0o644),
            ("pkg", stat.S_IFDIR | 0o755),
        ]
        for name, mode in cases:
            with self.subTest(name=name, mode=mode):
                unsafe = io.BytesIO()
                with zipfile.ZipFile(unsafe, "w") as archive:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = mode << 16
                    archive.writestr(info, b"x")
                unsafe_data = unsafe.getvalue()
                source = LeanArchiveSource.from_dict(payload(unsafe_data, subdir="."))
                with tempfile.TemporaryDirectory() as d:
                    root = Path(d)
                    with self.assertRaises(RepoSearchError) as cm:
                        materialize_archive_source(
                            source,
                            dest=root / "source",
                            receipt_path=root / "receipt.json",
                            opener=lambda *_args, data=unsafe_data, **_kwargs: Response(data),
                        )
                    self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")

    def test_symlink_member_fails_closed(self):
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            info = zipfile.ZipInfo("pkg/link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, b"Main.lean")
        unsafe_data = unsafe.getvalue()
        source = LeanArchiveSource.from_dict(payload(unsafe_data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=root / "source",
                    receipt_path=root / "receipt.json",
                    opener=lambda *_args, **_kwargs: Response(unsafe_data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")

    def test_receipt_inside_destination_fails_closed(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=dest,
                    receipt_path=dest / "receipt.json",
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertFalse(dest.exists())

    def test_receipt_ancestor_of_destination_fails_before_parent_creation(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            receipt_path = root / "out"
            dest = receipt_path / "source"
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=dest,
                    receipt_path=receipt_path,
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertFalse(receipt_path.exists())
            self.assertFalse(dest.exists())

    def test_existing_directory_receipt_target_fails_before_publication(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            receipt_path = root / "receipt-dir"
            receipt_path.mkdir()
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=dest,
                    receipt_path=receipt_path,
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertFalse(dest.exists())

    def test_receipt_staging_path_is_unique_per_invocation(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            receipt_path = root / "receipt.json"
            staged: list[Path] = []

            def capture_and_fail(src, _dst):
                staged.append(Path(src))
                raise OSError("stop after staging")

            with mock.patch(
                "scripts.materialize_archive_source.os.link", side_effect=capture_and_fail
            ):
                for index in range(2):
                    with self.assertRaises(RepoSearchError):
                        materialize_archive_source(
                            source,
                            dest=root / f"source-{index}",
                            receipt_path=receipt_path,
                            opener=lambda *_args, **_kwargs: Response(data),
                        )

            self.assertEqual(len(staged), 2)
            self.assertNotEqual(staged[0], staged[1])
            self.assertFalse(receipt_path.exists())

    def test_concurrent_receipt_target_is_not_overwritten(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            receipt_path = root / "receipt.json"
            real_link = __import__("os").link

            def race_receipt_link(src, dst):
                if Path(dst) == receipt_path:
                    receipt_path.write_text("other receipt\n", encoding="utf-8")
                return real_link(src, dst)

            with mock.patch(
                "scripts.materialize_archive_source.os.link", side_effect=race_receipt_link
            ), self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=dest,
                    receipt_path=receipt_path,
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertFalse(dest.exists())
            self.assertEqual(receipt_path.read_text(encoding="utf-8"), "other receipt\n")
            self.assertFalse(member_manifest_path_for_receipt(receipt_path).exists())

    def test_concurrent_member_manifest_target_is_not_overwritten(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            receipt_path = root / "receipt.json"
            member_manifest_path = member_manifest_path_for_receipt(receipt_path)
            real_link = __import__("os").link

            def race_member_manifest_link(src, dst):
                if Path(dst) == member_manifest_path:
                    member_manifest_path.write_text("other manifest\n", encoding="utf-8")
                return real_link(src, dst)

            with mock.patch(
                "scripts.materialize_archive_source.os.link",
                side_effect=race_member_manifest_link,
            ), self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=dest,
                    receipt_path=receipt_path,
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertFalse(dest.exists())
            self.assertFalse(receipt_path.exists())
            self.assertEqual(
                member_manifest_path.read_text(encoding="utf-8"), "other manifest\n"
            )

    def test_receipt_publication_failure_rolls_back_destination(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            receipt_path = root / "receipt.json"
            real_link = __import__("os").link

            def fail_receipt_link(src, dst):
                if Path(dst) == receipt_path:
                    raise OSError("simulated receipt publication failure")
                return real_link(src, dst)

            with mock.patch(
                "scripts.materialize_archive_source.os.link", side_effect=fail_receipt_link
            ), self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=dest,
                    receipt_path=receipt_path,
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
            self.assertFalse(dest.exists())
            self.assertFalse(receipt_path.exists())
            self.assertFalse(member_manifest_path_for_receipt(receipt_path).exists())

    def test_existing_file_destination_fails_closed(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            dest.write_text("occupied", encoding="utf-8")
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=dest,
                    receipt_path=root / "receipt.json",
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")

    def test_missing_selected_source_root_fails_closed(self):
        data = zip_bytes({"other/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data, subdir="pkg"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(RepoSearchError) as cm:
                materialize_archive_source(
                    source,
                    dest=root / "source",
                    receipt_path=root / "receipt.json",
                    opener=lambda *_args, **_kwargs: Response(data),
                )
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
