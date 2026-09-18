import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.load_producer_env import environment_mapping, main
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.producer_config import LeanGitSource, RunnerPins


class ProducerEnvTests(unittest.TestCase):
    def test_mapping_joins_multiple_roots_for_leandepviz(self):
        source = LeanGitSource.from_dict({
            "schema": "theseus.lean-git-source.v1",
            "source_id": "fixture",
            "source_repo": "example/repo",
            "source_commit": "a" * 40,
            "source_subdir": "formal",
            "root_modules": ["Fixture.A", "Fixture.B"],
            "build_target": "Fixture",
        })
        runner = RunnerPins.from_dict({
            "schema": "theseus.lean-producer-runner.v1",
            "extractor_repo": "cameronfreer/LeanDepViz",
            "extractor_commit": "b" * 40,
            "extractor_main_sha256": "c" * 64,
            "elan_version": "v4.2.3",
            "elan_sha256": "d" * 64,
        })
        env = environment_mapping(source, runner)
        self.assertEqual(env["ROOT_MODULES_CSV"], "Fixture.A,Fixture.B")
        self.assertEqual(env["SOURCE_SUBDIR"], "formal")
        self.assertEqual(env["BUILD_TARGET"], "Fixture")


class ProducerEnvCliTests(unittest.TestCase):
    def test_cli_writes_sorted_environment_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source_path = root / "source.json"
            runner_path = root / "runner.json"
            env_path = root / "github-env"
            source_path.write_text(json.dumps({
                "schema": "theseus.lean-git-source.v1",
                "source_id": "fixture",
                "source_repo": "example/repo",
                "source_commit": "a" * 40,
                "source_subdir": "formal",
                "root_modules": ["Fixture.A", "Fixture.B"],
                "build_target": "Fixture",
            }), encoding="utf-8")
            runner_path.write_text(json.dumps({
                "schema": "theseus.lean-producer-runner.v1",
                "extractor_repo": "cameronfreer/LeanDepViz",
                "extractor_commit": "b" * 40,
                "extractor_main_sha256": "c" * 64,
                "elan_version": "v4.2.3",
                "elan_sha256": "d" * 64,
            }), encoding="utf-8")
            rc = main([
                "--source", str(source_path),
                "--runner", str(runner_path),
                "--github-env", str(env_path),
            ])
            self.assertEqual(rc, 0)
            lines = env_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, sorted(lines))
            keys = [line.split("=", 1)[0] for line in lines]
            self.assertEqual(len(keys), len(set(keys)))

    def test_cli_checkout_root_writes_only_canonical_source_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            checkout = root / "checkout"
            source_root = checkout / "formal"
            source_root.mkdir(parents=True)
            source_path = root / "source.json"
            runner_path = root / "runner.json"
            env_path = root / "github-env"
            source_path.write_text(json.dumps({
                "schema": "theseus.lean-git-source.v1",
                "source_id": "fixture",
                "source_repo": "example/repo",
                "source_commit": "a" * 40,
                "source_subdir": "formal",
                "root_modules": ["Fixture"],
                "build_target": "Fixture",
            }), encoding="utf-8")
            runner_path.write_text(json.dumps({
                "schema": "theseus.lean-producer-runner.v1",
                "extractor_repo": "cameronfreer/LeanDepViz",
                "extractor_commit": "b" * 40,
                "extractor_main_sha256": "c" * 64,
                "elan_version": "v4.2.3",
                "elan_sha256": "d" * 64,
            }), encoding="utf-8")
            rc = main([
                "--source", str(source_path),
                "--runner", str(runner_path),
                "--checkout-root", str(checkout),
                "--github-env", str(env_path),
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(
                env_path.read_text(encoding="utf-8").splitlines(),
                [f"SOURCE_ROOT={source_root.resolve()}"],
            )

    def test_cli_checkout_root_rejects_tracked_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            checkout = root / "checkout"
            outside = root / "outside"
            checkout.mkdir()
            outside.mkdir()
            (checkout / "escape").symlink_to(outside, target_is_directory=True)
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "add", "escape"], check=True)

            source_path = root / "source.json"
            runner_path = root / "runner.json"
            env_path = root / "github-env"
            source_path.write_text(json.dumps({
                "schema": "theseus.lean-git-source.v1",
                "source_id": "fixture",
                "source_repo": "example/repo",
                "source_commit": "a" * 40,
                "source_subdir": "escape",
                "root_modules": ["Fixture"],
                "build_target": "Fixture",
            }), encoding="utf-8")
            runner_path.write_text(json.dumps({
                "schema": "theseus.lean-producer-runner.v1",
                "extractor_repo": "cameronfreer/LeanDepViz",
                "extractor_commit": "b" * 40,
                "extractor_main_sha256": "c" * 64,
                "elan_version": "v4.2.3",
                "elan_sha256": "d" * 64,
            }), encoding="utf-8")

            with self.assertRaises(RepoSearchError) as cm:
                main([
                    "--source", str(source_path),
                    "--runner", str(runner_path),
                    "--checkout-root", str(checkout),
                    "--github-env", str(env_path),
                ])
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
