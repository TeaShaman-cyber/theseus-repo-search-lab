import tempfile
import unittest
from pathlib import Path

from scripts.write_consumer_receipt import build_receipt
from theseus_repo_search.artifact import write_archive_artifact_v2
from theseus_repo_search.model import ArchiveAuthority, ArtifactScope, ProducerPin
from theseus_repo_search.projection import build_projection
from theseus_repo_search.replay_contract import prepare_registered_replay

AUTHORITY = ArchiveAuthority(
    url="https://zenodo.org/records/23160921/files/decreasing-diagrams-lean.zip",
    sha256="4" * 64,
    format="zip",
    subdir="decreasing-diagrams-lean",
)
PRODUCER = ProducerPin(
    kind="lean-dep-viz",
    tool_repo="cameronfreer/LeanDepViz",
    tool_commit="deadbeef",
    tool_hash="f" * 64,
)
SCOPE = ArtifactScope(root_modules=("Regular",), dependency_boundary="internal_only")


def write_v2(path: Path) -> Path:
    write_archive_artifact_v2(
        path,
        nodes=[],
        edges=[],
        sources=None,
        source_authority=AUTHORITY,
        producer=PRODUCER,
        scope=SCOPE,
    )
    return path


class V2GuardrailTests(unittest.TestCase):
    def test_projection_accepts_v2_from_task6_onward(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_v2(root / "artifact")
            db = root / "index.sqlite"
            fingerprint = build_projection(artifact, db)
            self.assertTrue(fingerprint)
            self.assertTrue(db.is_file())

    def test_registered_replay_rejects_v2_until_task7(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_v2(root / "artifact")
            with self.assertRaisesRegex(TypeError, "repo-index.v2"):
                prepare_registered_replay(
                    artifact,
                    root / "index.sqlite",
                    root / "not-read-before-v2-guard.json",
                )

    def test_consumer_receipt_rejects_v2_until_task7(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_v2(root / "artifact")
            with self.assertRaisesRegex(TypeError, "repo-index.v2"):
                build_receipt(
                    artifact=artifact,
                    db=root / "not-read-before-v2-guard.sqlite",
                    replay=root / "not-read-before-v2-guard.json",
                    artifact_name="archive-v2",
                    repository_head="a" * 40,
                    workflow_run_id="1",
                    workflow_run_attempt="1",
                    workflow_ref="wf",
                    workflow_sha="b" * 40,
                )
