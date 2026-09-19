import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.write_consumer_receipt import build_receipt
from tests.test_replay_ten_proofs_multicolor import write_fixture
from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.projection import build_projection


class ConsumerReceiptTests(unittest.TestCase):
    def test_receipt_binds_artifact_projection_replay_and_workflow_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            db = root / "index.sqlite"
            build_projection(artifact, db)
            replay = root / "replay.json"
            replay.write_text(json.dumps({"status": "PASS", "exact": {"target": "x"}}), encoding="utf-8")

            receipt = build_receipt(
                artifact=artifact,
                db=db,
                replay=replay,
                artifact_name="ten-proofs-artifact",
                repository_head="a" * 40,
                workflow_run_id="12345",
                workflow_run_attempt="2",
                workflow_ref="owner/repo/.github/workflows/heavy.yml@refs/heads/main",
                workflow_sha="b" * 40,
            )

            manifest, *_ = load_artifact(artifact)
            self.assertEqual(receipt["schema"], "theseus.repo-search-consumer-receipt.v1")
            self.assertEqual(receipt["result"], "PASS")
            self.assertEqual(receipt["artifact"]["identity"], artifact_identity(manifest))
            self.assertEqual(receipt["artifact"]["name"], "ten-proofs-artifact")
            self.assertEqual(receipt["projection"]["quick_check"], "ok")
            self.assertEqual(receipt["projection"]["artifact_identity"], artifact_identity(manifest))
            self.assertEqual(receipt["replay"]["status"], "PASS")
            self.assertEqual(receipt["workflow"]["repository_head"], "a" * 40)
            self.assertEqual(receipt["workflow"]["run_id"], "12345")
            self.assertEqual(receipt["workflow"]["run_attempt"], "2")
            self.assertEqual(receipt["workflow"]["workflow_sha"], "b" * 40)

    def test_receipt_rejects_non_pass_replay(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            db = root / "index.sqlite"
            build_projection(artifact, db)
            replay = root / "replay.json"
            replay.write_text(json.dumps({"status": "NO_SIGNAL"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "replay status"):
                build_receipt(
                    artifact=artifact, db=db, replay=replay, artifact_name="a",
                    repository_head="a" * 40, workflow_run_id="1", workflow_run_attempt="1",
                    workflow_ref="wf", workflow_sha="b" * 40,
                )

    def test_receipt_rejects_projection_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            db = root / "index.sqlite"
            build_projection(artifact, db)
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE meta SET value='bad' WHERE key='artifact_identity'")
                conn.commit()
            replay = root / "replay.json"
            replay.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact identity"):
                build_receipt(
                    artifact=artifact, db=db, replay=replay, artifact_name="a",
                    repository_head="a" * 40, workflow_run_id="1", workflow_run_attempt="1",
                    workflow_ref="wf", workflow_sha="b" * 40,
                )
