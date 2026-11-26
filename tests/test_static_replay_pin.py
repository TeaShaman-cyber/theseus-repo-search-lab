import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_replay_ten_proofs_multicolor import write_fixture
from theseus_repo_search.artifact import load_artifact
from theseus_repo_search.producer_config import load_lean_git_source
from theseus_repo_search.replay_contract import (
    static_producer_pin,
    validate_registered_replay_manifest,
)

DESCRIPTOR = Path("producer/sources/anthropic-fermats-last-theorem.json")


class StaticPinTests(unittest.TestCase):
    def test_static_pin_requires_exact_git_revision_and_extractor(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            subprocess.run(["git","init","-q",d],check=True)
            p = repo/"scripts/extract_anthropic_flt_static_graph.py"
            p.parent.mkdir()
            p.write_text("pinned extractor\n")
            subprocess.run(["git","-C",d,"add","."],check=True)
            subprocess.run(["git","-C",d,"-c","user.name=QA","-c","user.email=qa@example.invalid","commit","-qm","fixture"],check=True)
            head = subprocess.check_output(["git","-C",d,"rev-parse","HEAD"],text=True).strip()
            pin = static_producer_pin(repo,head)
            p.write_text("dirty extractor\n")
            self.assertEqual(static_producer_pin(repo,head),pin)
            with self.assertRaises(ValueError):
                static_producer_pin(repo,"HEAD")

    def test_static_pin_does_not_authorize_other_corpus(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = load_artifact(write_fixture(Path(d)))[0]
            head = subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
            pin = static_producer_pin(Path.cwd(),head)
            with self.assertRaises(AssertionError):
                validate_registered_replay_manifest(replace(manifest,producer=pin),
                    Path("producer/sources/openai-ten-proofs-multicolor.json"), expected_static_pin=pin)

    def test_static_manifest_requires_explicit_matching_pin(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = load_artifact(write_fixture(Path(d)))[0]
            source = load_lean_git_source(DESCRIPTOR)
            head = subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
            pin = static_producer_pin(Path.cwd(),head)
            manifest = replace(manifest,source_repo=source.source_repo,source_commit=source.source_commit,
                source_subdir=source.source_subdir,scope=replace(manifest.scope,
                root_modules=source.root_modules,exclude_source_prefixes=source.exclude_source_prefixes),producer=pin)
            with self.assertRaises(AssertionError):
                validate_registered_replay_manifest(manifest,DESCRIPTOR)
            self.assertEqual(validate_registered_replay_manifest(manifest,DESCRIPTOR,expected_static_pin=pin),source)
            with self.assertRaises(AssertionError):
                validate_registered_replay_manifest(manifest,DESCRIPTOR,expected_static_pin=replace(pin,tool_hash="0"*64))
