import json
import os
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ShardToolTests(unittest.TestCase):
    def test_merge_cli_verifies_shards_and_writes_v4_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            input_root = root / "shards"
            toolchain = "leanprover/lean4:v4.36.0"
            for index, raw in enumerate(
                (
                    {
                        "nodes": [
                            {"module": "A", "fullName": "A.x", "name": "x", "kind": "theorem"},
                            {"module": "B", "fullName": "B.y", "name": "y", "kind": "theorem"},
                        ],
                        "edges": [{"kind": "value", "source": "A.x", "target": "B.y"}],
                    },
                    {
                        "nodes": [
                            {"module": "B", "fullName": "B.y", "name": "y", "kind": "theorem"},
                            {"module": "C", "fullName": "C.z", "name": "z", "kind": "theorem"},
                        ],
                        "edges": [{"kind": "type", "source": "B.y", "target": "C.z"}],
                    },
                )
            ):
                shard = input_root / f"shard-{index:03d}"
                shard.mkdir(parents=True)
                raw_path = shard / "raw-depgraph.json"
                raw_path.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")
                receipt = {
                    "schema": "theseus.raw-depgraph-receipt.v2",
                    "source": {"repo": "example/repo", "commit": "a" * 40, "subdir": ""},
                    "scope": {"root_modules": [f"TheseusShard{index:03d}"]},
                    "producer": {
                        "kind": "lean-dep-viz",
                        "tool_repo": "example/extractor",
                        "tool_commit": "b" * 40,
                        "tool_hash": "c" * 64,
                    },
                    "observed": {"lean_toolchain": toolchain},
                    "raw_depgraph": {"sha256": sha256(raw_path.read_bytes()).hexdigest()},
                }
                (shard / "raw-depgraph-receipt.json").write_text(
                    json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
                )

            out_raw = root / "merged.json"
            out_receipt = root / "merged-receipt.json"
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "merge_raw_depgraph_shards.py"),
                    "--input-root", str(input_root),
                    "--source-repo", "example/repo",
                    "--source-commit", "a" * 40,
                    "--source-subdir", "",
                    "--scope-root-module", "A",
                    "--scope-root-module", "B",
                    "--scope-root-module", "C",
                    "--producer-kind", "lean-dep-viz-shard-merge",
                    "--producer-tool-repo", "example/merge",
                    "--producer-tool-commit", "d" * 40,
                    "--producer-tool-hash", "e" * 64,
                    "--out-raw", str(out_raw),
                    "--out-receipt", str(out_receipt),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            merged = json.loads(out_raw.read_text())
            self.assertEqual([row["fullName"] for row in merged["nodes"]], ["A.x", "B.y", "C.z"])
            receipt = json.loads(out_receipt.read_text())
            self.assertEqual(receipt["schema"], "theseus.raw-depgraph-receipt.v4")
            self.assertEqual(receipt["observed"]["lean_toolchain"], toolchain)
            self.assertEqual(len(receipt["shards"]), 2)
            self.assertEqual(
                receipt["raw_depgraph"]["sha256"],
                sha256(out_raw.read_bytes()).hexdigest(),
            )

    def test_merge_cli_rejects_mismatched_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            shard = root / "shards" / "shard-000"
            shard.mkdir(parents=True)
            raw_path = shard / "raw-depgraph.json"
            raw_path.write_text('{"nodes":[],"edges":[]}\n', encoding="utf-8")
            receipt = {
                "schema": "theseus.raw-depgraph-receipt.v2",
                "source": {"repo": "wrong/repo", "commit": "a" * 40, "subdir": ""},
                "scope": {"root_modules": ["TheseusShard000"]},
                "producer": {
                    "kind": "lean-dep-viz",
                    "tool_repo": "example/extractor",
                    "tool_commit": "b" * 40,
                    "tool_hash": "c" * 64,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.36.0"},
                "raw_depgraph": {"sha256": sha256(raw_path.read_bytes()).hexdigest()},
            }
            (shard / "raw-depgraph-receipt.json").write_text(json.dumps(receipt) + "\n")

            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "merge_raw_depgraph_shards.py"),
                    "--input-root", str(root / "shards"),
                    "--source-repo", "example/repo",
                    "--source-commit", "a" * 40,
                    "--source-subdir", "",
                    "--scope-root-module", "A",
                    "--producer-kind", "lean-dep-viz-shard-merge",
                    "--producer-tool-repo", "example/merge",
                    "--producer-tool-commit", "d" * 40,
                    "--producer-tool-hash", "e" * 64,
                    "--out-raw", str(root / "merged.json"),
                    "--out-receipt", str(root / "receipt.json"),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
