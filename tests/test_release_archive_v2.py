"""Archive-backed corpus v2 gets the same immutable release contract as git v1."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.consume_accepted_release import RebuildRequired, verify_and_extract_release
from scripts.package_accepted_artifact import build_release_package
from tests.test_release_package import _config_repo, _write_replay
from tests.test_v2_guardrails import (
    AUTHORITY,
    write_archive_descriptor,
    write_authoritative_v2,
)
from theseus_repo_search.artifact import artifact_identity, load_artifact

RUNNER = Path(__file__).resolve().parents[1] / "producer/runner.json"


def _fixture(root: Path):
    artifact = write_authoritative_v2(root / "artifact")
    replay = root / "replay.json"
    _write_replay(replay, artifact)
    descriptor_json = root / "archive.json"
    write_archive_descriptor(descriptor_json)
    repo, descriptor, runner, head = _config_repo(root, descriptor_json.read_bytes(), RUNNER.read_bytes())
    manifest = load_artifact(artifact)[0]
    identity = artifact_identity(manifest)
    replay_bytes = replay.read_bytes()
    payload = {
        "schema": "theseus.repo-search-consumer-receipt.v2",
        "result": "PASS",
        "workflow": {"repository_head":head,"workflow_sha":head,"run_id":"23456",
                     "run_attempt":"1","workflow_ref":"owner/repo/.github/workflows/lean-source-producer-smoke.yml@refs/heads/main"},
        "artifact": {"name":"fixture-archive-v2","identity":identity,"source":{
            "kind":"archive","url":AUTHORITY.url,"sha256":AUTHORITY.sha256,
            "format":AUTHORITY.format,"subdir":AUTHORITY.subdir}},
        "producer_config": {"source_descriptor":{"path":"producer/sources/source.json",
                               "sha256":hashlib.sha256(descriptor.read_bytes()).hexdigest()},
                            "runner_config":{"path":"producer/runner.json",
                               "sha256":hashlib.sha256(runner.read_bytes()).hexdigest()}},
        "projection":{"quick_check":"ok","artifact_identity":identity,"db_sha256":"a"*64},
        "replay":{"status":"PASS","artifact_identity":identity,
                  "sha256":hashlib.sha256(replay_bytes).hexdigest()},
    }
    consumer = root / "consumer.json"
    consumer.write_text(json.dumps(payload))
    return artifact,replay,repo,descriptor,runner,consumer


class ReleaseArchiveV2Tests(unittest.TestCase):
    def test_archive_release_roundtrip_and_exact_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            artifact,replay,repo,descriptor,runner,consumer=_fixture(root)
            archive=root/'accepted.tar.gz';receipt=root/'receipt.json'
            packaged=build_release_package(
                artifact=artifact,replay=replay,source_descriptor=descriptor,
                runner_config=runner,consumer_receipt=consumer,repository_root=repo,
                archive=archive,receipt=receipt)
            self.assertEqual(packaged['artifact']['schema'],'theseus.repo-index.v2')
            self.assertEqual(packaged['source_descriptor']['archive']['sha256'],AUTHORITY.sha256)
            result=verify_and_extract_release(archive=archive,receipt=receipt,
                source_descriptor=descriptor,runner_config=runner,dest=root/'consumed')
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(result['artifact_identity'],packaged['artifact']['identity'])
            self.assertTrue((root/'consumed/artifact/raw-depgraph.json').is_file())
            self.assertTrue((root/'consumed/artifact/authority-receipt.json').is_file())

    def test_archive_source_drift_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            artifact,replay,repo,descriptor,runner,consumer=_fixture(root)
            archive=root/'accepted.tar.gz';receipt=root/'receipt.json'
            build_release_package(artifact=artifact,replay=replay,source_descriptor=descriptor,
                runner_config=runner,consumer_receipt=consumer,repository_root=repo,
                archive=archive,receipt=receipt)
            observed=json.loads(descriptor.read_text())
            observed['archive_sha256']='9'*64
            descriptor.write_text(json.dumps(observed))
            with self.assertRaises(RebuildRequired):
                verify_and_extract_release(archive=archive,receipt=receipt,
                    source_descriptor=descriptor,runner_config=runner,dest=root/'denied')
            self.assertFalse((root/'denied').exists())

    def test_fake_git_provenance_cannot_replace_archive_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            artifact,replay,repo,descriptor,runner,consumer=_fixture(root)
            payload=json.loads(consumer.read_text())
            payload['artifact'].pop('source')
            payload['artifact'].update(source_repo='bad/repo',source_commit='a'*40,source_subdir='')
            consumer.write_text(json.dumps(payload))
            with self.assertRaises((ValueError,TypeError)):
                build_release_package(artifact=artifact,replay=replay,source_descriptor=descriptor,
                    runner_config=runner,consumer_receipt=consumer,repository_root=repo,
                    archive=root/'bad.tar.gz',receipt=root/'bad.json')


if __name__=='__main__':unittest.main()
