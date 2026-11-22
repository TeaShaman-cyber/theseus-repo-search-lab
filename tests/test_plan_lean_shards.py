import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PlanLeanShardsTests(unittest.TestCase):
    def test_cli_partitions_exact_git_tree_deterministically(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            src = repo / "src"
            src.mkdir()
            for name, size in (("A", 90), ("B", 80), ("C", 70), ("D", 60)):
                (src / f"{name}.lean").write_text("x" * size, encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

            out = Path(d) / "plan.json"
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "plan_lean_shards.py"),
                    "--repo-root", str(repo),
                    "--source-prefix", "src",
                    "--shard-count", "2",
                    "--out", str(out),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(out.read_text())
            self.assertEqual(plan["schema"], "theseus.lean-module-shards.v1")
            self.assertEqual(plan["source_commit"], head)
            self.assertEqual(plan["module_count"], 4)
            self.assertEqual(plan["total_bytes"], 300)
            self.assertEqual(
                [shard["modules"] for shard in plan["shards"]],
                [["A", "D"], ["B", "C"]],
            )
            self.assertEqual(
                [shard["total_bytes"] for shard in plan["shards"]],
                [150, 150],
            )


if __name__ == "__main__":
    unittest.main()
