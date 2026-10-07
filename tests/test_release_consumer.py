import json
import tempfile
import unittest
from pathlib import Path

from scripts.consume_accepted_release import (
    RebuildRequired,
    ReleaseEvidenceInvalid,
    verify_and_extract_release,
)
from scripts.package_accepted_artifact import build_release_package
from tests.test_release_package import (
    DESCRIPTOR,
    _accepted_context,
    _write_replay,
    write_fixture,
)


class ReleaseConsumerTests(unittest.TestCase):
    def _build(self, root: Path):
        artifact = write_fixture(root)
        replay = root / "replay.json"
        _write_replay(replay, artifact)
        repo, descriptor, runner, consumer, _ = _accepted_context(
            root, artifact, replay, descriptor_source=DESCRIPTOR
        )
        archive = root / "accepted.tar.gz"
        receipt = root / "release-receipt.json"
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
        return archive, receipt, descriptor, runner

    def test_verified_release_extracts_canonical_artifact_and_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, receipt, descriptor, runner = self._build(root)
            dest = root / "out"
            result = verify_and_extract_release(
                archive=archive,
                receipt=receipt,
                source_descriptor=descriptor,
                runner_config=runner,
                dest=dest,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertTrue((dest / "artifact" / "manifest.json").is_file())
            self.assertTrue((dest / "consumer-receipt.json").is_file())
            self.assertTrue((dest / "replay.json").is_file())
            self.assertEqual(
                result["artifact_identity"],
                json.loads(receipt.read_text(encoding="utf-8"))["artifact"]["identity"],
            )

    def test_producer_config_drift_requires_rebuild(self):
        for case in ("descriptor", "runner"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                archive, receipt, descriptor, runner = self._build(root)
                target = descriptor if case == "descriptor" else runner
                target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

                with self.assertRaises(RebuildRequired) as caught:
                    verify_and_extract_release(
                        archive=archive,
                        receipt=receipt,
                        source_descriptor=descriptor,
                        runner_config=runner,
                        dest=root / "out",
                    )
                self.assertIn("REBUILD_REQUIRED", str(caught.exception))
                self.assertFalse((root / "out").exists())

    def test_invalid_or_missing_release_evidence_fails_closed(self):
        cases = ("missing_package", "package_bytes", "member_hash")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                archive, receipt, descriptor, runner = self._build(root)
                if case == "missing_package":
                    archive.unlink()
                elif case == "package_bytes":
                    data = bytearray(archive.read_bytes())
                    data[-10] ^= 1
                    archive.write_bytes(bytes(data))
                elif case == "member_hash":
                    payload = json.loads(receipt.read_text(encoding="utf-8"))
                    payload["package"]["members"][0]["sha256"] = "0" * 64
                    receipt.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaises(ReleaseEvidenceInvalid) as caught:
                    verify_and_extract_release(
                        archive=archive,
                        receipt=receipt,
                        source_descriptor=descriptor,
                        runner_config=runner,
                        dest=root / "out",
                    )
                self.assertIn("RELEASE_EVIDENCE_INVALID", str(caught.exception))
                self.assertFalse((root / "out").exists())

    def test_refuses_nonempty_destination(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, receipt, descriptor, runner = self._build(root)
            dest = root / "out"
            dest.mkdir()
            (dest / "old.txt").write_text("stale", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_and_extract_release(
                    archive=archive,
                    receipt=receipt,
                    source_descriptor=descriptor,
                    runner_config=runner,
                    dest=dest,
                )


if __name__ == "__main__":
    unittest.main()
