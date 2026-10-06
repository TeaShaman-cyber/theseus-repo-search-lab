import io
import json
import tempfile
import unittest
import zipfile
from hashlib import sha256
from pathlib import Path
from unittest import mock

from scripts.materialize_archive_source import materialize_archive_source
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

    def test_receipt_publication_failure_rolls_back_destination(self):
        data = zip_bytes({"pkg/Main.lean": b"x"})
        source = LeanArchiveSource.from_dict(payload(data))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dest = root / "source"
            receipt_path = root / "receipt.json"
            real_replace = __import__("os").replace

            def fail_receipt_replace(src, dst):
                if Path(dst) == receipt_path:
                    raise OSError("simulated receipt publication failure")
                return real_replace(src, dst)

            with mock.patch(
                "scripts.materialize_archive_source.os.replace", side_effect=fail_receipt_replace
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
