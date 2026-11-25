import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.riemann_compressed_cache import (
    SpaceGuard,
    batch_rows,
    canonical_graph_hash,
    ensure_space,
    part_hashes,
    restore_batch,
)


class CompressedCacheTests(unittest.TestCase):
    def test_batches_cover_exact_rows_without_overlap(self):
        rows = [[str(i), "input", "archive", "digest"] for i in range(9)]
        self.assertEqual(list(batch_rows(rows, 4)), [rows[:4], rows[4:8], rows[8:]])
        with self.assertRaises(ValueError):
            list(batch_rows(rows, 0))

    def test_disk_and_inode_reserve(self):
        with patch("scripts.riemann_compressed_cache.os.statvfs") as stat:
            stat.return_value.f_bavail = 100
            stat.return_value.f_frsize = 4096
            stat.return_value.f_favail = 20
            ensure_space(Path("."), 409600, 20)
            with self.assertRaisesRegex(ValueError, "disk"):
                ensure_space(Path("."), 409601, 20)
            with self.assertRaisesRegex(ValueError, "inode"):
                ensure_space(Path("."), 409600, 21)

    def test_olean_parts_exact_coverage_no_ir_or_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "M.olean").write_bytes(b"main")
            (root / "M.olean.private").write_bytes(b"private")
            (root / "M.olean.server").write_bytes(b"server")
            (root / "M.ir").write_bytes(b"ignored")
            self.assertEqual(
                set(part_hashes(root, ["M"])),
                {"M.olean", "M.olean.private", "M.olean.server"},
            )
            with self.assertRaisesRegex(ValueError, "missing"):
                part_hashes(root, ["N"])
            (root / "N.olean").symlink_to(root / "M.olean")
            with self.assertRaisesRegex(ValueError, "symlink"):
                part_hashes(root, ["N"])
            (root / "N.olean").unlink()
            (root / "N.olean").write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "unexpected"):
                part_hashes(root, ["M"])

    def test_legacy_and_split_sidecars(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "M.olean").write_bytes(b"legacy")
            self.assertEqual(set(part_hashes(root, ["M"])), {"M.olean"})
            (root / "M.olean.server").write_bytes(b"server")
            with self.assertRaisesRegex(ValueError, "paired"):
                part_hashes(root, ["M"])

    def test_restore_retargets_only_verified_extraction_bases(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = root / "source"
            project.mkdir()
            sentinel = project / ".lake/build/lib/lean/Unrelated.olean"
            sentinel.parent.mkdir(parents=True)
            sentinel.write_bytes(b"preserve")
            cache = root / "cache"
            build = root / "batch-build"
            ci = Mock()
            ci.REPOSITORY = "owner/repo"
            ci.restore.restore_jobs.return_value = [
                {
                    "file": str(cache / "archive.ltar"),
                    "hash": "input",
                    "base": [
                        str(project / ".lake/build/lib/lean/A"),
                        str(project / ".lake/build/ir/A"),
                    ],
                }
            ]
            guard = Mock()
            rows = [["A.M", "input", "archive.ltar", "digest"]]
            contents = b'"2026-03-17"\n["input","archive.ltar"]\n'
            with patch(
                "scripts.riemann_compressed_cache.download_subset",
                return_value="config",
            ):
                restore_batch(
                    ci,
                    {"toolchain": "leanprover/lean4:v4.36.0"},
                    project,
                    cache,
                    build,
                    rows,
                    contents,
                    guard,
                    2,
                    Path("/tool/bin/lean"),
                )
            ci.restore.restore_jobs.assert_called_once_with(project, cache, rows)
            job = json.loads(guard.run.call_args.kwargs["input"])[0]
            self.assertEqual(
                job["base"], [str(build / "lib/lean/A"), str(build / "ir/A")]
            )
            self.assertEqual(job["file"], str(cache / "archive.ltar"))
            self.assertEqual(job["hash"], "input")
            self.assertEqual(sentinel.read_bytes(), b"preserve")
            self.assertTrue((build / "lib/lean/A").is_dir())

    def test_graph_parity_ignores_order_but_not_duplicate_rows_or_edges(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"
            rows = [
                {"module": "M", "name": "M.a", "uses": ["c", "b"]},
                {"module": "M", "name": "M.b", "uses": []},
            ]
            a.write_text("".join(json.dumps(r) + "\n" for r in rows))
            rows[0]["uses"] = ["b", "c"]
            b.write_text("".join(json.dumps(r) + "\n" for r in reversed(rows)))
            self.assertEqual(canonical_graph_hash(a), canonical_graph_hash(b))
            rows[0]["uses"] = ["b", "c", "c"]
            b.write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertNotEqual(canonical_graph_hash(a), canonical_graph_hash(b))
            with a.open("a") as f:
                f.write(json.dumps(rows[1]) + "\n")
            self.assertEqual(canonical_graph_hash(a)[0], 3)

    @unittest.skipUnless(
        Path("/proc").is_dir() and hasattr(os, "killpg"),
        "Linux process groups required",
    )
    def test_disk_guard_stops_signal_ignoring_descendant(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pidfile = root / "child.pid"
            guard = SpaceGuard(root, 1, 1)
            child = (
                "import os,signal,time,pathlib,sys;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)"
            )
            parent = (
                "import subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);time.sleep(60)"
            )

            def alive(pid):
                try:
                    state = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0]
                    return state not in ("Z", "X")
                except FileNotFoundError:
                    return False

            try:
                with (
                    patch.object(
                        guard,
                        "check",
                        side_effect=[None, ValueError("injected disk reserve breach")],
                    ),
                    self.assertRaisesRegex(ValueError, "reserve"),
                ):
                    guard.run([sys.executable, "-c", parent, child, str(pidfile)])
                pid = int(pidfile.read_text())
                deadline = time.monotonic() + 1
                while alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(alive(pid))
            finally:
                if pidfile.exists():
                    pid = int(pidfile.read_text())
                    if alive(pid):
                        os.kill(pid, signal.SIGKILL)
