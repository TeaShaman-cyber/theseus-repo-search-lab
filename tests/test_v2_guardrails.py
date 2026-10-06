import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from tests.test_replay_ten_proofs_multicolor import write_fixture as write_git_fixture
from theseus_repo_search.artifact import (
    write_archive_artifact_v2,
)
from theseus_repo_search.model import ArchiveAuthority, ArtifactScope, ProducerPin
from theseus_repo_search.projection import build_projection
from theseus_repo_search.replay_contract import (
    prepare_registered_replay,
    registered_replay_provenance,
    validate_registered_replay_provenance,
)

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

RUNNER_PATH = Path("producer/runner.json")
RUNNER_PAYLOAD = json.loads(RUNNER_PATH.read_text(encoding="utf-8"))
REPLAY_PRODUCER = ProducerPin(
    kind="lean-dep-viz",
    tool_repo=RUNNER_PAYLOAD["extractor_repo"],
    tool_commit=RUNNER_PAYLOAD["extractor_commit"],
    tool_hash=RUNNER_PAYLOAD["extractor_main_sha256"],
)


def write_archive_descriptor(path: Path, *, url: str = AUTHORITY.url, sha: str = AUTHORITY.sha256, subdir: str = AUTHORITY.subdir) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "theseus.lean-archive-source.v1",
                "source_id": "archive-replay-fixture",
                "archive_url": url,
                "archive_sha256": sha,
                "archive_format": "zip",
                "source_subdir": subdir,
                "root_modules": list(SCOPE.root_modules),
                "build_target": "Regular",
                "exclude_source_prefixes": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def write_authoritative_v2(path: Path, *, member_manifest_sha256: str = "e" * 64) -> Path:
    raw = b'{"nodes":[],"edges":[]}\n'
    receipt = (
        json.dumps(
            {
                "schema": "theseus.raw-depgraph-receipt.v3",
                "source": {
                    "kind": "archive",
                    "url": AUTHORITY.url,
                    "sha256": AUTHORITY.sha256,
                    "format": AUTHORITY.format,
                    "subdir": AUTHORITY.subdir,
                },
                "materialization": {
                    "tree_sha256": "d" * 64,
                    "member_manifest_sha256": member_manifest_sha256,
                },
                "scope": {"root_modules": list(SCOPE.root_modules)},
                "producer": {
                    "kind": REPLAY_PRODUCER.kind,
                    "tool_repo": REPLAY_PRODUCER.tool_repo,
                    "tool_commit": REPLAY_PRODUCER.tool_commit,
                    "tool_hash": REPLAY_PRODUCER.tool_hash,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.30.0"},
                "raw_depgraph": {"sha256": sha256(raw).hexdigest()},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    write_archive_artifact_v2(
        path,
        nodes=[],
        edges=[],
        sources=[],
        source_authority=AUTHORITY,
        producer=REPLAY_PRODUCER,
        scope=SCOPE,
        created_from_authoritative_source=True,
        authority_receipt=receipt,
        raw_depgraph=raw,
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

    def test_registered_replay_accepts_matching_archive_descriptor_from_task7(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_authoritative_v2(root / "artifact")
            descriptor = write_archive_descriptor(root / "archive-source.json")
            manifest, source = prepare_registered_replay(
                artifact, root / "index.sqlite", descriptor
            )
            self.assertEqual(manifest.schema, "theseus.repo-index.v2")
            self.assertEqual(source.archive_sha256, AUTHORITY.sha256)

    def test_registered_replay_rejects_archive_artifact_against_git_descriptor(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_authoritative_v2(root / "artifact")
            with self.assertRaisesRegex(TypeError, "source kind"):
                prepare_registered_replay(
                    artifact,
                    root / "index.sqlite",
                    Path("producer/sources/openai-prime-gaps-186.json"),
                )

    def test_registered_replay_rejects_git_artifact_against_archive_descriptor(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_git_fixture(root)
            descriptor = write_archive_descriptor(root / "archive-source.json")
            with self.assertRaisesRegex(TypeError, "source kind"):
                prepare_registered_replay(artifact, root / "index.sqlite", descriptor)

    def test_registered_replay_rejects_member_evidence_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_authoritative_v2(root / "artifact")
            descriptor = write_archive_descriptor(root / "archive-source.json")
            manifest, _ = prepare_registered_replay(
                artifact, root / "index.sqlite", descriptor
            )
            observed = registered_replay_provenance(artifact, manifest)
            observed["materialization"]["member_manifest_sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "replay provenance mismatch"):
                validate_registered_replay_provenance(artifact, manifest, observed)

    def test_registered_replay_rejects_archive_descriptor_authority_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = write_authoritative_v2(root / "artifact")
            for label, kwargs in (
                ("url", {"url": "https://example.invalid/other.zip"}),
                ("sha", {"sha": "5" * 64}),
                ("subdir", {"subdir": "other-subdir"}),
            ):
                with self.subTest(label=label):
                    descriptor = write_archive_descriptor(
                        root / f"{label}.json", **kwargs
                    )
                    with self.assertRaisesRegex(AssertionError, "provenance/scope"):
                        prepare_registered_replay(
                            artifact, root / f"{label}.sqlite", descriptor
                        )
