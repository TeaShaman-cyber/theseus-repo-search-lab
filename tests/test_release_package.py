import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.package_accepted_artifact import build_release_package
from tests.test_replay_con_nf import DESCRIPTOR as CON_NF_DESCRIPTOR
from tests.test_replay_con_nf import write_fixture as write_con_nf_fixture
from tests.test_replay_ten_proofs_multicolor import DESCRIPTOR, write_fixture
from theseus_repo_search.artifact import artifact_identity, load_artifact

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "producer" / "runner.json"


class ReleasePackageTests(unittest.TestCase):
    def test_package_bytes_and_receipt_are_reproducible_across_source_mtime_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            identity = artifact_identity(load_artifact(artifact)[0])
            replay = root / "replay.json"
            replay.write_text(
                json.dumps({"status": "PASS", "artifact_identity": identity}),
                encoding="utf-8",
            )

            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first_archive = first_dir / "accepted-artifact.tar.gz"
            first_receipt = first_dir / "accepted-artifact-receipt.json"
            second_archive = second_dir / "accepted-artifact.tar.gz"
            second_receipt = second_dir / "accepted-artifact-receipt.json"

            build_release_package(
                artifact=artifact,
                replay=replay,
                source_descriptor=DESCRIPTOR,
                runner_config=RUNNER,
                archive=first_archive,
                receipt=first_receipt,
            )

            for path in artifact.rglob("*"):
                if path.is_file():
                    os.utime(path, (1_800_000_000, 1_800_000_000))
            os.utime(replay, (1_800_000_001, 1_800_000_001))

            build_release_package(
                artifact=artifact,
                replay=replay,
                source_descriptor=DESCRIPTOR,
                runner_config=RUNNER,
                archive=second_archive,
                receipt=second_receipt,
            )

            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            self.assertEqual(first_receipt.read_bytes(), second_receipt.read_bytes())

    def test_rejects_source_descriptor_exclusion_scope_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_con_nf_fixture(root)
            identity = artifact_identity(load_artifact(artifact)[0])
            replay = root / "replay.json"
            replay.write_text(
                json.dumps({"status": "PASS", "artifact_identity": identity}),
                encoding="utf-8",
            )
            descriptor_payload = json.loads(CON_NF_DESCRIPTOR.read_text(encoding="utf-8"))
            descriptor_payload["exclude_source_prefixes"] = []
            mismatched_descriptor = root / "mismatched-source.json"
            mismatched_descriptor.write_text(
                json.dumps(descriptor_payload), encoding="utf-8"
            )

            with self.assertRaisesRegex(
                ValueError, "source descriptor does not match accepted artifact provenance/scope"
            ):
                build_release_package(
                    artifact=artifact,
                    replay=replay,
                    source_descriptor=mismatched_descriptor,
                    runner_config=RUNNER,
                    archive=root / "accepted-artifact.tar.gz",
                    receipt=root / "accepted-artifact-receipt.json",
                )

    def test_receipt_binds_exact_inputs_artifact_identity_and_package_digest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            identity = artifact_identity(load_artifact(artifact)[0])
            replay = root / "replay.json"
            replay.write_text(
                json.dumps({"status": "PASS", "artifact_identity": identity}),
                encoding="utf-8",
            )
            archive = root / "accepted-artifact.tar.gz"
            receipt_path = root / "accepted-artifact-receipt.json"

            receipt = build_release_package(
                artifact=artifact,
                replay=replay,
                source_descriptor=DESCRIPTOR,
                runner_config=RUNNER,
                archive=archive,
                receipt=receipt_path,
            )

            self.assertEqual(
                receipt["schema"],
                "theseus.repo-search-accepted-artifact-release-receipt.v1",
            )
            self.assertEqual(receipt["artifact"]["identity"], identity)
            self.assertEqual(
                receipt["source_descriptor"]["sha256"],
                hashlib.sha256(DESCRIPTOR.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                receipt["runner_config"]["sha256"],
                hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                receipt["replay"]["sha256"],
                hashlib.sha256(replay.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                receipt["package"]["sha256"],
                hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
            self.assertEqual(receipt["package"]["format"], "tar.gz")
            self.assertRegex(receipt["fingerprint"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8")), receipt
            )


if __name__ == "__main__":
    unittest.main()
