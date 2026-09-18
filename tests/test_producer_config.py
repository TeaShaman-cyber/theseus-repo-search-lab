import json
import tempfile
import unittest
from pathlib import Path

from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.producer_config import (
    LeanGitSource,
    RunnerPins,
    load_lean_git_source,
    load_runner_pins,
)


COMMIT = "a" * 40


class LeanGitSourceTests(unittest.TestCase):
    def test_loads_valid_descriptor(self):
        source = LeanGitSource.from_dict({
            "schema": "theseus.lean-git-source.v1",
            "source_id": "openai-long-gaps",
            "source_repo": "openai/LongGapsBetweenPrimes",
            "source_commit": COMMIT,
            "source_subdir": "",
            "root_modules": ["LongGapsBetweenPrimes"],
            "build_target": "LongGapsBetweenPrimes",
        })
        self.assertEqual(source.root_modules, ("LongGapsBetweenPrimes",))
        self.assertEqual(source.source_commit, COMMIT)

    def test_loads_v2_descriptor_with_source_exclusions(self):
        source = LeanGitSource.from_dict({"schema":"theseus.lean-git-source.v2","source_id":"con-nf","source_repo":"leanprover-community/con-nf","source_commit":COMMIT,"source_subdir":"","root_modules":["ConNF"],"build_target":"ConNF","exclude_source_prefixes":["Old/"]})
        self.assertEqual(source.exclude_source_prefixes, ("Old/",))

    def test_load_file_uses_same_contract(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source.json"
            path.write_text(json.dumps({
                "schema": "theseus.lean-git-source.v1",
                "source_id": "zeta23",
                "source_repo": "anthropics/formal-math",
                "source_commit": COMMIT,
                "source_subdir": "zeta23",
                "root_modules": ["Zeta23"],
                "build_target": "Zeta23",
            }), encoding="utf-8")
            self.assertEqual(load_lean_git_source(path).source_id, "zeta23")


class RunnerPinsTests(unittest.TestCase):
    def test_loads_valid_runner_pins(self):
        runner = RunnerPins.from_dict({
            "schema": "theseus.lean-producer-runner.v1",
            "extractor_repo": "cameronfreer/LeanDepViz",
            "extractor_commit": "b" * 40,
            "extractor_main_sha256": "c" * 64,
            "elan_version": "v4.2.3",
            "elan_sha256": "d" * 64,
        })
        self.assertEqual(runner.extractor_commit, "b" * 40)


class StrictConfigTests(unittest.TestCase):
    def source_payload(self):
        return {"schema":"theseus.lean-git-source.v1","source_id":"fixture","source_repo":"example/repo","source_commit":COMMIT,"source_subdir":"","root_modules":["Fixture"],"build_target":"Fixture"}
    def runner_payload(self):
        return {"schema":"theseus.lean-producer-runner.v1","extractor_repo":"cameronfreer/LeanDepViz","extractor_commit":"b"*40,"extractor_main_sha256":"c"*64,"elan_version":"v4.2.3","elan_sha256":"d"*64}
    def test_invalid_descriptors_fail_closed(self):
        for patch in [{'schema': 'wrong.schema'}, {'source_commit': 'main'}, {'source_repo': 'missing-owner'}, {'source_repo': 'example/repo\nINJECTED=value'}, {'source_subdir': '/absolute'}, {'source_subdir': 'formal\nINJECTED=value'}, {'source_subdir': '../escape'}, {'root_modules': []}, {'root_modules': ['Zeta23', 'Zeta23']}, {'root_modules': ['Fixture,Injected']}, {'root_modules': ['Fixture\nINJECTED=value']}, {'root_modules': ['Fixture\rInjected']}, {'root_modules': ['Fixture\x00Injected']}, {'build_target': ''}]:
            p=self.source_payload(); p.update(patch)
            with self.assertRaises(RepoSearchError) as cm: LeanGitSource.from_dict(p)
            self.assertEqual(cm.exception.code,"BLOCKED_SOURCE_BINDING")
    def test_unknown_descriptor_keys_fail_closed(self):
        p=self.source_payload(); p["publisher"]="openai"
        with self.assertRaises(RepoSearchError) as cm: LeanGitSource.from_dict(p)
        self.assertEqual(cm.exception.code,"BLOCKED_SOURCE_BINDING")
    def test_invalid_runner_pins_fail_closed(self):
        for patch in [{'schema': 'wrong.runner.schema'}, {'extractor_commit': 'main'}, {'extractor_repo': 'cameronfreer/LeanDepViz\nINJECTED=value'}, {'elan_version': 'v4.2.3\nINJECTED=value'}, {'extractor_main_sha256': 'gggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggg'}, {'elan_sha256': 'gggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggg'}]:
            p=self.runner_payload(); p.update(patch)
            with self.assertRaises(RepoSearchError) as cm: RunnerPins.from_dict(p)
            self.assertEqual(cm.exception.code,"BLOCKED_SOURCE_BINDING")
    def test_unknown_runner_keys_fail_closed(self):
        p=self.runner_payload(); p["publisher"]="openai"
        with self.assertRaises(RepoSearchError) as cm: RunnerPins.from_dict(p)
        self.assertEqual(cm.exception.code,"BLOCKED_SOURCE_BINDING")


class ConfigFileLoadTests(unittest.TestCase):
    def test_invalid_utf8_configs_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, loader in (("source.json", load_lean_git_source), ("runner.json", load_runner_pins)):
                with self.subTest(name=name):
                    path = root / name
                    path.write_bytes(b"{\xff}")
                    with self.assertRaises(RepoSearchError) as cm:
                        loader(path)
                    self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")

def source_for(subdir: str) -> LeanGitSource:
    return LeanGitSource.from_dict({
        "schema": "theseus.lean-git-source.v1",
        "source_id": "fixture",
        "source_repo": "example/repo",
        "source_commit": COMMIT,
        "source_subdir": subdir,
        "root_modules": ["Fixture"],
        "build_target": "Fixture",
    })


class LeanGitSourceRootTests(unittest.TestCase):
    def test_resolve_source_root_accepts_nested_subdir(self):
        with tempfile.TemporaryDirectory() as d:
            checkout = Path(d) / "repo"
            nested = checkout / "formal"
            nested.mkdir(parents=True)
            self.assertEqual(source_for("formal").resolve_source_root(checkout), nested.resolve())

    def test_resolve_source_root_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as d:
            checkout = Path(d) / "repo"
            checkout.mkdir()
            with self.assertRaises(RepoSearchError) as cm:
                source_for("missing").resolve_source_root(checkout)
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")

    def test_resolve_source_root_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            checkout = root / "repo"
            outside = root / "outside"
            checkout.mkdir(); outside.mkdir()
            (checkout / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(RepoSearchError) as cm:
                source_for("escape").resolve_source_root(checkout)
            self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
