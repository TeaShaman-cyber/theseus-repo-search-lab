import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.restore_riemann_ci_cache_subset import (
    select_rows,
    subset_mapping_bytes,
)


class RiemannTargetedCacheTests(unittest.TestCase):
    def test_select_rows_keeps_only_requested_modules_in_manifest_order(self):
        rows = [
            ["A", "aaaaaaaaaaaaaaaa", "1111111111111111.ltar", "1" * 64],
            ["B", "bbbbbbbbbbbbbbbb", "2222222222222222.ltar", "2" * 64],
            ["C", "cccccccccccccccc", "3333333333333333.ltar", "3" * 64],
        ]

        selected = select_rows(rows, {"C", "A"})

        self.assertEqual(selected, [rows[0], rows[2]])

    def test_select_rows_fails_closed_when_project_closure_module_has_no_cache_row(self):
        rows = [
            ["A", "aaaaaaaaaaaaaaaa", "1111111111111111.ltar", "1" * 64],
        ]

        with self.assertRaisesRegex(ValueError, "missing validated cache rows: B"):
            select_rows(rows, {"A", "B"})

    def test_subset_mapping_bytes_keeps_header_and_only_selected_input_hashes(self):
        full = b"".join(
            [
                json.dumps("2026-03-17").encode() + b"\n",
                json.dumps(["aaaaaaaaaaaaaaaa", "1111111111111111.ltar"]).encode() + b"\n",
                json.dumps(["bbbbbbbbbbbbbbbb", "2222222222222222.ltar"]).encode() + b"\n",
                json.dumps(["cccccccccccccccc", "3333333333333333.ltar"]).encode() + b"\n",
            ]
        )
        selected_rows = [
            ["C", "cccccccccccccccc", "3333333333333333.ltar", "3" * 64],
            ["A", "aaaaaaaaaaaaaaaa", "1111111111111111.ltar", "1" * 64],
        ]

        subset = subset_mapping_bytes(full, selected_rows)

        self.assertEqual(
            subset.splitlines(),
            [
                json.dumps("2026-03-17").encode(),
                json.dumps(["aaaaaaaaaaaaaaaa", "1111111111111111.ltar"]).encode(),
                json.dumps(["cccccccccccccccc", "3333333333333333.ltar"]).encode(),
            ],
        )

    def test_subset_mapping_bytes_rejects_selected_hash_missing_from_validated_map(self):
        full = (
            json.dumps("2026-03-17").encode()
            + b"\n"
            + json.dumps(["aaaaaaaaaaaaaaaa", "1111111111111111.ltar"]).encode()
            + b"\n"
        )
        selected_rows = [
            ["B", "bbbbbbbbbbbbbbbb", "2222222222222222.ltar", "2" * 64],
        ]

        with self.assertRaisesRegex(ValueError, "missing selected input hashes"):
            subset_mapping_bytes(full, selected_rows)


class IsolatedTransportTests(unittest.TestCase):
    def test_download_uses_disposable_workspace_and_preserves_original(self):
        from scripts.restore_riemann_ci_cache_subset import download_subset
        source = ('name="tree"\nplatformIndependent=true\n[[require]]\nname="mathlib"\n'
                  '[cache]\ndefaultService="rh"\n[[cache.service]]\nname="rh"\nkind="s3"\n'
                  'artifactEndpoint="https://example.org/artifacts"\nrevisionEndpoint="https://example.org/revisions"\n')
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            config = project / 'lakefile.toml'
            config.write_text(source)
            cache = project / 'cache'
            subset = b'"2026-03-17"\n'
            observed = []
            def run(args, *, cwd, env, check):
                observed.append(cwd)
                self.assertNotEqual(cwd, project)
                self.assertEqual(Path(args[4]).read_bytes(), subset)
                self.assertEqual(env['LAKE_CACHE_DIR'], str(cache.resolve()))
                self.assertEqual(env['LAKE_CONFIG'], str(cwd / 'lakefile.toml'))
                self.assertNotIn('[[require]]', (cwd / 'lakefile.toml').read_text())
                self.assertTrue(check)
                raise subprocess.CalledProcessError(1, args)
            with patch('scripts.restore_riemann_ci_cache_subset.subprocess.run', side_effect=run), self.assertRaises(subprocess.CalledProcessError):
                download_subset(project, cache, subset, 'owner/repo', {'ELAN_TOOLCHAIN': 'leanprover/lean4:v4.36.0'})
            self.assertEqual(config.read_text(), source)
            self.assertEqual(len(observed), 1)
            self.assertFalse(observed[0].exists())

    def test_transport_preserves_cache_configuration_without_build_dependencies(self):
        import tomllib

        from scripts.restore_riemann_ci_cache_subset import transport_config
        source = '''name = "tree"
platformIndependent = true
[[require]]
name = "mathlib"
[cache]
defaultService = "rh"
[[cache.service]]
name = "rh"
kind = "s3"
artifactEndpoint = "https://example.org/artifacts"
revisionEndpoint = "https://example.org/revisions"
'''
        original = tomllib.loads(source)
        result = tomllib.loads(transport_config(source))
        self.assertEqual(result, {key: original[key] for key in ('name', 'platformIndependent', 'cache')})

    def test_unsupported_cache_layout_rejected(self):
        from scripts.restore_riemann_ci_cache_subset import transport_config
        for source in ('name="tree"', 'name="tree"\n[cache]\ndefaultService="missing"', 'name="tree"\n[cache]\ndefaultService="rh"\n[[cache.service]]\nname="rh"\nkind="reservoir"'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                transport_config(source)


if __name__ == "__main__":
    unittest.main()
