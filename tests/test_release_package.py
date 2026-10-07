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
from tests.test_replay_ten_proofs_multicolor import RUNNER as PRODUCER_RUNNER
from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.model import ProducerPin
from theseus_repo_search.replay_contract import registered_replay_provenance

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "producer" / "runner.json"


_DEFAULT = object()


def _write_replay(
    path: Path,
    artifact: Path,
    *,
    status: str = "PASS",
    identity: str | None = None,
    provenance: object = _DEFAULT,
) -> None:
    manifest = load_artifact(artifact)[0]
    if identity is None:
        identity = artifact_identity(manifest)
    if provenance is _DEFAULT:
        provenance = registered_replay_provenance(artifact, manifest)
    path.write_text(
        json.dumps(
            {
                "status": status,
                "artifact_identity": identity,
                "provenance": provenance,
            }
        ),
        encoding="utf-8",
    )


class ReleasePackageTests(unittest.TestCase):
    def test_package_bytes_and_receipt_are_reproducible_across_source_mtime_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            replay = root / "replay.json"
            _write_replay(replay, artifact)

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
                runner_config=RUNNER_PATH,
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
                runner_config=RUNNER_PATH,
                archive=second_archive,
                receipt=second_receipt,
            )

            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            self.assertEqual(first_receipt.read_bytes(), second_receipt.read_bytes())

    def test_rejects_trust_boundary_mutation_matrix(self):
        cases = (
            "source_repo",
            "source_commit",
            "source_subdir",
            "root_modules",
            "exclude_source_prefixes",
            "runner_repo",
            "runner_commit",
            "runner_hash",
            "non_authoritative",
            "producer_kind",
            "replay_status",
            "replay_identity",
            "replay_provenance",
        )

        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                descriptor = root / "source.json"
                runner = root / "runner.json"
                descriptor_payload = json.loads(
                    CON_NF_DESCRIPTOR.read_text(encoding="utf-8")
                )
                runner_payload = json.loads(RUNNER_PATH.read_text(encoding="utf-8"))
                artifact = write_con_nf_fixture(root)

                if case == "source_repo":
                    descriptor_payload["source_repo"] = "example/other"
                elif case == "source_commit":
                    descriptor_payload["source_commit"] = "0" * 40
                elif case == "source_subdir":
                    descriptor_payload["source_subdir"] = "Other"
                elif case == "root_modules":
                    descriptor_payload["root_modules"] = ["Other"]
                elif case == "exclude_source_prefixes":
                    descriptor_payload["exclude_source_prefixes"] = []
                elif case == "runner_repo":
                    runner_payload["extractor_repo"] = "example/other"
                elif case == "runner_commit":
                    runner_payload["extractor_commit"] = "c" * 40
                elif case == "runner_hash":
                    runner_payload["extractor_main_sha256"] = "d" * 64
                elif case == "non_authoritative":
                    manifest_path = artifact / "manifest.json"
                    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest_payload["created_from_authoritative_commit"] = False
                    manifest_path.write_text(
                        json.dumps(manifest_payload, sort_keys=True, separators=(",", ":"))
                        + "\n",
                        encoding="utf-8",
                    )
                elif case == "producer_kind":
                    artifact = write_fixture(
                        root / "wrong-producer",
                        producer=ProducerPin(
                            kind="lexical_only",
                            tool_repo=PRODUCER_RUNNER.extractor_repo,
                            tool_commit=PRODUCER_RUNNER.extractor_commit,
                            tool_hash=PRODUCER_RUNNER.extractor_main_sha256,
                        ),
                    )
                    descriptor_payload = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))

                descriptor.write_text(json.dumps(descriptor_payload), encoding="utf-8")
                runner.write_text(json.dumps(runner_payload), encoding="utf-8")
                replay = root / "replay.json"
                replay_status = "NO_SIGNAL" if case == "replay_status" else "PASS"
                replay_identity = "0" * 64 if case == "replay_identity" else None
                replay_provenance: object = _DEFAULT
                if case == "replay_provenance":
                    replay_provenance = {
                        "repo": "example/other",
                        "commit": "0" * 40,
                        "subdir": "",
                    }
                _write_replay(
                    replay,
                    artifact,
                    status=replay_status,
                    identity=replay_identity,
                    provenance=replay_provenance,
                )

                with self.assertRaises((AssertionError, TypeError, ValueError)):
                    build_release_package(
                        artifact=artifact,
                        replay=replay,
                        source_descriptor=descriptor,
                        runner_config=runner,
                        archive=root / "accepted-artifact.tar.gz",
                        receipt=root / "accepted-artifact-receipt.json",
                    )

    def test_receipt_binds_exact_inputs_artifact_identity_and_package_digest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            identity = artifact_identity(load_artifact(artifact)[0])
            replay = root / "replay.json"
            _write_replay(replay, artifact)
            archive = root / "accepted-artifact.tar.gz"
            receipt_path = root / "accepted-artifact-receipt.json"

            receipt = build_release_package(
                artifact=artifact,
                replay=replay,
                source_descriptor=DESCRIPTOR,
                runner_config=RUNNER_PATH,
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
                hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
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
