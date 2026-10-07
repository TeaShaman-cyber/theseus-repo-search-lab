import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.package_accepted_artifact import build_release_package
from tests.test_replay_con_nf import DESCRIPTOR as CON_NF_DESCRIPTOR
from tests.test_replay_con_nf import RUNNER as CON_NF_RUNNER
from tests.test_replay_con_nf import write_fixture as write_con_nf_fixture
from tests.test_replay_ten_proofs_multicolor import DESCRIPTOR, write_fixture
from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.errors import RepoSearchError
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


def _config_repo(root: Path, descriptor_bytes: bytes, runner_bytes: bytes) -> tuple[Path, Path, Path, str]:
    repo = root / "config-repo"
    descriptor = repo / "producer" / "sources" / "source.json"
    runner = repo / "producer" / "runner.json"
    descriptor.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True, exist_ok=True)
    descriptor.write_bytes(descriptor_bytes)
    runner.write_bytes(runner_bytes)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "qa@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "QA Fixture"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, descriptor, runner, head


def _write_consumer_receipt(path: Path, artifact: Path, replay: Path, repository_head: str) -> dict[str, object]:
    manifest = load_artifact(artifact)[0]
    identity = artifact_identity(manifest)
    replay_payload = json.loads(replay.read_text(encoding="utf-8"))
    payload: dict[str, object] = {
        "schema": "theseus.repo-search-consumer-receipt.v1",
        "result": "PASS",
        "workflow": {
            "repository_head": repository_head,
            "run_id": "12345",
            "run_attempt": "1",
            "workflow_ref": "owner/repo/.github/workflows/lean-source-producer-smoke.yml@refs/heads/main",
            "workflow_sha": repository_head,
        },
        "artifact": {
            "name": "fixture-repo-index-v1",
            "identity": identity,
            "source_repo": manifest.source_repo,
            "source_commit": manifest.source_commit,
            "source_subdir": manifest.source_subdir,
        },
        "projection": {"quick_check": "ok", "artifact_identity": identity, "db_sha256": "a" * 64},
        "replay": {
            "status": replay_payload.get("status"),
            "artifact_identity": replay_payload.get("artifact_identity"),
            "sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _accepted_context(
    root: Path,
    artifact: Path,
    replay: Path,
    *,
    descriptor_source: Path,
) -> tuple[Path, Path, Path, Path, str]:
    repo, descriptor, runner, head = _config_repo(
        root,
        descriptor_source.read_bytes(),
        RUNNER_PATH.read_bytes(),
    )
    consumer = root / "consumer-receipt.json"
    _write_consumer_receipt(consumer, artifact, replay, head)
    return repo, descriptor, runner, consumer, head


class ReleasePackageTests(unittest.TestCase):
    def test_package_bytes_and_receipt_are_reproducible_across_source_mtime_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            replay = root / "replay.json"
            _write_replay(replay, artifact)
            repo, descriptor, runner, consumer, _ = _accepted_context(
                root, artifact, replay, descriptor_source=DESCRIPTOR
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
                source_descriptor=descriptor,
                runner_config=runner,
                consumer_receipt=consumer,
                repository_root=repo,
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
                source_descriptor=descriptor,
                runner_config=runner,
                consumer_receipt=consumer,
                repository_root=repo,
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
            "build_target",
            "runner_repo",
            "runner_commit",
            "runner_hash",
            "runner_elan_version",
            "runner_elan_sha256",
            "non_authoritative",
            "producer_kind",
            "replay_status",
            "replay_identity",
            "replay_provenance",
            "consumer_result",
            "consumer_artifact_identity",
            "consumer_projection_identity",
            "consumer_replay_sha",
            "consumer_repository_head",
            "consumer_workflow_ref",
        )

        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                producer = None
                authoritative = case != "non_authoritative"
                if case == "producer_kind":
                    producer = ProducerPin(
                        kind="lexical_only",
                        tool_repo=CON_NF_RUNNER.extractor_repo,
                        tool_commit=CON_NF_RUNNER.extractor_commit,
                        tool_hash=CON_NF_RUNNER.extractor_main_sha256,
                    )
                artifact = write_con_nf_fixture(
                    root, authoritative=authoritative, producer=producer
                )
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
                repo, descriptor, runner, consumer, _ = _accepted_context(
                    root, artifact, replay, descriptor_source=CON_NF_DESCRIPTOR
                )

                descriptor_payload = json.loads(descriptor.read_text(encoding="utf-8"))
                runner_payload = json.loads(runner.read_text(encoding="utf-8"))
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
                elif case == "build_target":
                    descriptor_payload["build_target"] = "Other"
                elif case == "runner_repo":
                    runner_payload["extractor_repo"] = "example/other"
                elif case == "runner_commit":
                    runner_payload["extractor_commit"] = "c" * 40
                elif case == "runner_hash":
                    runner_payload["extractor_main_sha256"] = "d" * 64
                elif case == "runner_elan_version":
                    runner_payload["elan_version"] = "v9.9.9"
                elif case == "runner_elan_sha256":
                    runner_payload["elan_sha256"] = "e" * 64
                descriptor.write_text(json.dumps(descriptor_payload), encoding="utf-8")
                runner.write_text(json.dumps(runner_payload), encoding="utf-8")

                consumer_payload = json.loads(consumer.read_text(encoding="utf-8"))
                if case == "consumer_result":
                    consumer_payload["result"] = "FAIL"
                elif case == "consumer_artifact_identity":
                    consumer_payload["artifact"]["identity"] = "0" * 64
                elif case == "consumer_projection_identity":
                    consumer_payload["projection"]["artifact_identity"] = "0" * 64
                elif case == "consumer_replay_sha":
                    consumer_payload["replay"]["sha256"] = "0" * 64
                elif case == "consumer_repository_head":
                    consumer_payload["workflow"]["repository_head"] = "0" * 40
                elif case == "consumer_workflow_ref":
                    consumer_payload["workflow"]["workflow_ref"] = "owner/repo/.github/workflows/other.yml@refs/heads/main"
                consumer.write_text(json.dumps(consumer_payload), encoding="utf-8")

                with self.assertRaises((AssertionError, TypeError, ValueError)):
                    build_release_package(
                        artifact=artifact,
                        replay=replay,
                        source_descriptor=descriptor,
                        runner_config=runner,
                        consumer_receipt=consumer,
                        repository_root=repo,
                        archive=root / "accepted-artifact.tar.gz",
                        receipt=root / "accepted-artifact-receipt.json",
                    )

    def test_rejects_post_acceptance_artifact_member_mutation_matrix(self):
        cases = (
            "extra_file",
            "unexpected_nested_file",
            "extra_symlink",
            "missing_canonical_member",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                artifact = write_fixture(root)
                replay = root / "replay.json"
                _write_replay(replay, artifact)
                repo, descriptor, runner, consumer, _ = _accepted_context(
                    root, artifact, replay, descriptor_source=DESCRIPTOR
                )

                if case == "extra_file":
                    (artifact / "unexpected.txt").write_text("not accepted\n", encoding="utf-8")
                elif case == "unexpected_nested_file":
                    nested = artifact / "nested"
                    nested.mkdir()
                    (nested / "unexpected.json").write_text("{}\n", encoding="utf-8")
                elif case == "extra_symlink":
                    (artifact / "unexpected-link").symlink_to("manifest.json")
                elif case == "missing_canonical_member":
                    (artifact / "edges.jsonl").unlink()

                with self.assertRaises((AssertionError, RepoSearchError, TypeError, ValueError)):
                    build_release_package(
                        artifact=artifact,
                        replay=replay,
                        source_descriptor=descriptor,
                        runner_config=runner,
                        consumer_receipt=consumer,
                        repository_root=repo,
                        archive=root / "accepted-artifact.tar.gz",
                        receipt=root / "accepted-artifact-receipt.json",
                    )

    def test_rejects_output_path_collision_matrix(self):
        cases = ("same_outputs", "archive_overwrites_replay", "receipt_inside_artifact")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                artifact = write_fixture(root)
                replay = root / "replay.json"
                _write_replay(replay, artifact)
                repo, descriptor, runner, consumer, _ = _accepted_context(
                    root, artifact, replay, descriptor_source=DESCRIPTOR
                )
                archive = root / "archive.tar.gz"
                receipt = root / "receipt.json"
                if case == "same_outputs":
                    receipt = archive
                elif case == "archive_overwrites_replay":
                    archive = replay
                elif case == "receipt_inside_artifact":
                    receipt = artifact / "receipt.json"

                with self.assertRaises(ValueError):
                    build_release_package(
                        artifact=artifact,
                        replay=replay,
                        source_descriptor=descriptor,
                        runner_config=runner,
                        consumer_receipt=consumer,
                        repository_root=repo,
                        archive=archive,
                        receipt=receipt,
                    )

    def test_receipt_binds_exact_inputs_artifact_identity_acceptance_and_package_digest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            identity = artifact_identity(load_artifact(artifact)[0])
            replay = root / "replay.json"
            _write_replay(replay, artifact)
            repo, descriptor, runner, consumer, head = _accepted_context(
                root, artifact, replay, descriptor_source=DESCRIPTOR
            )
            archive = root / "accepted-artifact.tar.gz"
            receipt_path = root / "accepted-artifact-receipt.json"

            receipt = build_release_package(
                artifact=artifact,
                replay=replay,
                source_descriptor=descriptor,
                runner_config=runner,
                consumer_receipt=consumer,
                repository_root=repo,
                archive=archive,
                receipt=receipt_path,
            )

            self.assertEqual(
                receipt["schema"],
                "theseus.repo-search-accepted-artifact-release-receipt.v1",
            )
            self.assertEqual(receipt["artifact"]["identity"], identity)
            self.assertEqual(receipt["acceptance"]["repository_head"], head)
            self.assertEqual(
                receipt["acceptance"]["consumer_receipt_sha256"],
                hashlib.sha256(consumer.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                receipt["source_descriptor"]["sha256"],
                hashlib.sha256(descriptor.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                receipt["runner_config"]["sha256"],
                hashlib.sha256(runner.read_bytes()).hexdigest(),
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
            self.assertIn(
                "consumer-receipt.json",
                [member["path"] for member in receipt["package"]["members"]],
            )
            self.assertRegex(receipt["fingerprint"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8")), receipt
            )


if __name__ == "__main__":
    unittest.main()
