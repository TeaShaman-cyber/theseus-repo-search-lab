from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools" / "dev" / "check"


class DevCheckContractTest(unittest.TestCase):
    def test_canonical_dev_check_exists_and_composes_repo_checks(self):
        self.assertTrue(CHECK.is_file(), "missing canonical tools/dev/check")
        self.assertTrue(os.access(CHECK, os.X_OK), "tools/dev/check is not executable")
        text = CHECK.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        for marker in (
            "PYTHONPYCACHEPREFIX",
            "python3 -m unittest discover",
            "python3 -m compileall",
            "python3 -m json.tool",
            "git diff --check",
            "DEV_CHECK_PASS",
        ):
            self.assertIn(marker, text)

    def _run_copied_check_with_plan(
        self,
        plan_text: str,
        repo_files: dict[str, str] | None = None,
        clean_python: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            for rel in ("src", "scripts", "tests", "docs/superpowers/plans", "tools/dev"):
                (root / rel).mkdir(parents=True, exist_ok=True)
            copied = root / "tools/dev/check"
            shutil.copy2(CHECK, copied)
            (root / "docs/superpowers/plans/example.md").write_text(plan_text, encoding="utf-8")
            (root / "tests/test_smoke.py").write_text(
                "import unittest\n\nclass SmokeTest(unittest.TestCase):\n    def test_smoke(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            for rel, content in (repo_files or {}).items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            env = os.environ.copy()
            if clean_python:
                env.pop("PYTHONPATH", None)
                env["PYTHONNOUSERSITE"] = "1"
            return subprocess.run(
                [str(copied)],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

    def test_dev_check_sets_repo_src_for_clean_checkout(self):
        result = self._run_copied_check_with_plan(
            "# Clean checkout\n",
            repo_files={
                "src/theseus_repo_search/__init__.py": "VALUE = 1\n",
                "tests/test_package_import.py": (
                    "import unittest\n"
                    "import theseus_repo_search\n\n"
                    "class PackageImportTest(unittest.TestCase):\n"
                    "    def test_import(self):\n"
                    "        self.assertEqual(theseus_repo_search.VALUE, 1)\n"
                ),
            },
            clean_python=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_dev_check_rejects_module_level_unittest_method_snippet(self):
        result = self._run_copied_check_with_plan(
            """# Bad plan\n\n```python\ndef test_missing_case(self):\n    self.assertEqual(1, 1)\n```\n"""
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("undiscoverable", result.stderr)

    def test_dev_check_accepts_class_scoped_unittest_method_snippet(self):
        result = self._run_copied_check_with_plan(
            """# Good plan\n\n```python\nclass ExampleTests(unittest.TestCase):\n    def test_missing_case(self):\n        self.assertEqual(1, 1)\n```\n"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dev_check_rejects_script_invoked_before_plan_creation(self):
        result = self._run_copied_check_with_plan(
            """# Task 1\n\n```yaml\n- run: python3 scripts/replay_future.py --artifact out\n```\n\n# Task 2\n- Create: `scripts/replay_future.py`\n"""
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("invoked before creation", result.stderr)

    def test_dev_check_accepts_script_created_before_plan_invocation(self):
        result = self._run_copied_check_with_plan(
            """# Task 1\n- Create: `scripts/replay_future.py`\n\n# Task 2\n```yaml\n- run: python3 scripts/replay_future.py --artifact out\n```\n"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dev_check_rejects_delete_before_live_consumer_replacement(self):
        config = "producer/" + "zeta23.json"
        workflow = ".github/workflows/legacy.yml"
        result = self._run_copied_check_with_plan(
            f"# Task 1\n- Delete: `{config}`\n\n# Task 2\n- Modify: `{workflow}`\n",
            repo_files={
                config: "{}\n",
                workflow: f"config: {config}\n",
            },
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("deleted before live consumer replacement", result.stderr)

    def test_dev_check_accepts_consumer_replacement_before_delete(self):
        config = "producer/" + "zeta23.json"
        workflow = ".github/workflows/legacy.yml"
        result = self._run_copied_check_with_plan(
            f"# Task 1\n- Modify: `{workflow}`\n\n# Task 2\n- Delete: `{config}`\n",
            repo_files={
                config: "{}\n",
                workflow: f"config: {config}\n",
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_dev_check_rejects_same_step_github_env_read_after_write(self):
        result = self._run_copied_check_with_plan(
            """# Bad phase\n\n```yaml\n- name: Export and consume\n  shell: bash\n  run: |\n    printf 'SOURCE_ROOT=%s\\n' \"$PWD/source\" >> \"$GITHUB_ENV\"\n    test -f \"$SOURCE_ROOT/lean-toolchain\"\n```\n"""
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GITHUB_ENV value read in same step", result.stderr)

    def test_dev_check_rejects_loader_source_root_read_in_same_step(self):
        loader = "scripts/load_producer_env.py"
        result = self._run_copied_check_with_plan(
            f"""# Historical phase bug\n\n```yaml\n- name: Resolve and consume\n  shell: bash\n  run: |\n    python3 {loader} --github-env \"$GITHUB_ENV\"\n    test -n \"$SOURCE_ROOT\"\n```\n""",
            repo_files={loader: "print('fixture')\n"},
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SOURCE_ROOT read in same GITHUB_ENV step", result.stderr)

    def test_dev_check_accepts_github_env_value_in_next_step(self):
        result = self._run_copied_check_with_plan(
            """# Good phase boundary\n\n```yaml\n- name: Export\n  shell: bash\n  run: |\n    printf 'SOURCE_ROOT=%s\\n' \"$PWD/source\" >> \"$GITHUB_ENV\"\n- name: Consume\n  shell: bash\n  run: |\n    test -f \"$SOURCE_ROOT/lean-toolchain\"\n```\n"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
