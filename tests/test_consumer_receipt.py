import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.write_consumer_receipt import build_receipt
from tests.test_replay_ten_proofs_multicolor import write_fixture
from tests.test_v2_guardrails import (
    AUTHORITY,
    write_authoritative_v2,
)
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
            manifest, *_ = load_artifact(artifact)
            identity = artifact_identity(manifest)
            replay.write_text(
                json.dumps({"status": "PASS", "artifact_identity": identity, "exact": {"target": "x"}}),
                encoding="utf-8",
            )

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

            self.assertEqual(receipt["schema"], "theseus.repo-search-consumer-receipt.v1")
            self.assertEqual(receipt["result"], "PASS")
            self.assertEqual(receipt["artifact"]["identity"], artifact_identity(manifest))
            self.assertEqual(receipt["artifact"]["name"], "ten-proofs-artifact")
            self.assertEqual(receipt["projection"]["quick_check"], "ok")
            self.assertEqual(receipt["projection"]["artifact_identity"], artifact_identity(manifest))
            self.assertEqual(receipt["replay"]["status"], "PASS")
            self.assertEqual(receipt["replay"]["artifact_identity"], identity)
            self.assertEqual(receipt["workflow"]["repository_head"], "a" * 40)
            self.assertEqual(receipt["workflow"]["run_id"], "12345")
            self.assertEqual(receipt["workflow"]["run_attempt"], "2")
            self.assertEqual(receipt["workflow"]["workflow_sha"], "b" * 40)

    def test_archive_receipt_uses_structured_authority_and_rejects_member_evidence_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_authoritative_v2(root / "artifact")
            db = root / "index.sqlite"
            build_projection(artifact, db)
            manifest, *_ = load_artifact(artifact)
            identity = artifact_identity(manifest)
            replay = root / "replay.json"
            provenance = {
                "source": {
                    "kind": "archive",
                    "url": AUTHORITY.url,
                    "sha256": AUTHORITY.sha256,
                    "format": AUTHORITY.format,
                    "subdir": AUTHORITY.subdir,
                },
                "materialization": {
                    "tree_sha256": "d" * 64,
                    "member_manifest_sha256": "e" * 64,
                },
            }
            replay.write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "artifact_identity": identity,
                        "provenance": provenance,
                    }
                ),
                encoding="utf-8",
            )

            receipt = build_receipt(
                artifact=artifact, db=db, replay=replay, artifact_name="archive-v2",
                repository_head="a" * 40, workflow_run_id="1", workflow_run_attempt="1",
                workflow_ref="wf", workflow_sha="b" * 40,
            )
            artifact_payload = receipt["artifact"]
            self.assertEqual(artifact_payload["source"], provenance["source"])
            self.assertNotIn("source_commit", artifact_payload)
            self.assertNotIn("source_repo", artifact_payload)

            provenance["materialization"]["member_manifest_sha256"] = "f" * 64
            replay.write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "artifact_identity": identity,
                        "provenance": provenance,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "replay provenance mismatch"):
                build_receipt(
                    artifact=artifact, db=db, replay=replay, artifact_name="archive-v2",
                    repository_head="a" * 40, workflow_run_id="1", workflow_run_attempt="1",
                    workflow_ref="wf", workflow_sha="b" * 40,
                )

    def test_archive_receipt_rejects_projection_authority_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_authoritative_v2(root / "artifact")
            db = root / "index.sqlite"
            build_projection(artifact, db)
            manifest, *_ = load_artifact(artifact)
            identity = artifact_identity(manifest)
            replay = root / "replay.json"
            from theseus_repo_search.replay_contract import registered_replay_provenance

            replay.write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "artifact_identity": identity,
                        "provenance": registered_replay_provenance(artifact, manifest),
                    }
                ),
                encoding="utf-8",
            )
            with sqlite3.connect(db) as conn:
                authority = json.loads(
                    dict(conn.execute("SELECT key, value FROM meta"))[
                        "source_authority_json"
                    ]
                )
                authority["url"] = "https://example.invalid/tampered.zip"
                conn.execute(
                    "UPDATE meta SET value=? WHERE key='source_authority_json'",
                    (json.dumps(authority),),
                )
                conn.commit()
            with self.assertRaisesRegex(ValueError, "projection provenance mismatch"):
                build_receipt(
                    artifact=artifact, db=db, replay=replay, artifact_name="archive-v2",
                    repository_head="a" * 40, workflow_run_id="1", workflow_run_attempt="1",
                    workflow_ref="wf", workflow_sha="b" * 40,
                )

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


    def test_receipt_rejects_missing_replay_artifact_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            db = root / "index.sqlite"
            build_projection(artifact, db)
            replay = root / "replay.json"
            replay.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "replay artifact identity"):
                build_receipt(
                    artifact=artifact, db=db, replay=replay, artifact_name="a",
                    repository_head="a" * 40, workflow_run_id="1", workflow_run_attempt="1",
                    workflow_ref="wf", workflow_sha="b" * 40,
                )

    def test_receipt_rejects_replay_artifact_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_fixture(root)
            db = root / "index.sqlite"
            build_projection(artifact, db)
            replay = root / "replay.json"
            replay.write_text(
                json.dumps({"status": "PASS", "artifact_identity": "0" * 64}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "replay artifact identity mismatch"):
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
