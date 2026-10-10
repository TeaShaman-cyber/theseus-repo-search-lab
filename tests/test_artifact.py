import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from tests.raw_fixture import raw_depgraph_bytes
from theseus_repo_search.artifact import (
    _write_artifact_contents,
    artifact_identity,
    load_artifact,
    write_archive_artifact_v2,
    write_artifact,
)
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.model import (
    ArtifactManifest,
    ArtifactScope,
    Edge,
    EvidenceGrade,
    Node,
    ProducerPin,
    SourceChunk,
)

SOURCE_COMMIT = "abc123"
PRODUCER = ProducerPin(
    kind="lean-dep-viz",
    tool_repo="cameronfreer/LeanDepViz",
    tool_commit="deadbeef",
    tool_hash="f" * 64,
)
SCOPE = ArtifactScope(root_modules=("Zeta23",), dependency_boundary="internal_only")


def sample_nodes():
    return [
        Node.from_lean(
            full_name="Zeta23.Tiny.b",
            name="b",
            kind="thm",
            module="Zeta23.Tiny",
            source_commit=SOURCE_COMMIT,
        ),
        Node.from_lean(
            full_name="Zeta23.Tiny.a",
            name="a",
            kind="thm",
            module="Zeta23.Tiny",
            source_commit=SOURCE_COMMIT,
        ),
    ]


def sample_edges():
    return [
        Edge(
            source_id="lean:Zeta23.Tiny.b",
            target_id="lean:Zeta23.Tiny.a",
            relation="value_dependency",
            evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
            producer="cameronfreer/LeanDepViz@deadbeef",
        )
    ]


def sample_sources():
    text = "lemma a : True := by trivial\n"
    return [
        SourceChunk(
            id="src:Zeta23/Tiny.lean:1:1",
            source_commit=SOURCE_COMMIT,
            source_path="Zeta23/Tiny.lean",
            source_start_line=1,
            source_end_line=1,
            declaration_hint="a",
            text=text,
            content_sha256=sha256(text.encode()).hexdigest(),
        )
    ]


_DEFAULT = object()


def write_sample_raw(path: Path, *, nodes=None, edges=None, sources=_DEFAULT, scope=SCOPE):
    return _write_artifact_contents(
        path,
        nodes=sample_nodes() if nodes is None else nodes,
        edges=sample_edges() if edges is None else edges,
        sources=sample_sources() if sources is _DEFAULT else sources,
        source_repo="anthropics/formal-math",
        source_commit=SOURCE_COMMIT,
        source_subdir="zeta23",
        producer=PRODUCER,
        scope=scope,
        created_from_authoritative_commit=False,
    )


def write_sample(path: Path, *, nodes=None, edges=None, sources=_DEFAULT, scope=SCOPE):
    return write_artifact(
        path,
        nodes=sample_nodes() if nodes is None else nodes,
        edges=sample_edges() if edges is None else edges,
        sources=sample_sources() if sources is _DEFAULT else sources,
        source_repo="anthropics/formal-math",
        source_commit=SOURCE_COMMIT,
        source_subdir="zeta23",
        producer=PRODUCER,
        scope=scope,
        created_from_authoritative_commit=False,
    )


ARCHIVE_SHA256 = "4" * 64
ARCHIVE_URL = "https://zenodo.org/records/23160921/files/decreasing-diagrams-lean.zip"


def write_v2_archive_fixture(path: Path, *, revision_key: str = "source_revision") -> None:
    path.mkdir(parents=True, exist_ok=True)
    node = {
        "id": "lean:Regular.Main.demo",
        "full_name": "Regular.Main.demo",
        "name": "demo",
        "kind": "thm",
        "module": "Regular.Main",
        "source_path": None,
        "source_start_line": None,
        "source_end_line": None,
        revision_key: ARCHIVE_SHA256,
    }
    nodes_bytes = (
        json.dumps(node, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    edges_bytes = b""
    (path / "nodes.jsonl").write_bytes(nodes_bytes)
    (path / "edges.jsonl").write_bytes(edges_bytes)
    manifest = {
        "schema": "theseus.repo-index.v2",
        "source": {
            "kind": "archive",
            "url": ARCHIVE_URL,
            "sha256": ARCHIVE_SHA256,
            "format": "zip",
            "subdir": "decreasing-diagrams-lean",
        },
        "producer": {
            "kind": PRODUCER.kind,
            "tool_repo": PRODUCER.tool_repo,
            "tool_commit": PRODUCER.tool_commit,
            "tool_hash": PRODUCER.tool_hash,
        },
        "scope": {
            "root_modules": ["Regular"],
            "dependency_boundary": "internal_only",
        },
        "members": {
            "nodes": {"sha256": hashlib.sha256(nodes_bytes).hexdigest()},
            "edges": {"sha256": hashlib.sha256(edges_bytes).hexdigest()},
            "sources": {"sha256": None},
            "authority_receipt": {"sha256": None},
        },
        "counts": {"nodes": 1, "edges": 0},
        "created_from_authoritative_source": False,
    }
    (path / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


class ArtifactTests(unittest.TestCase):
    def test_manifest_non_object_is_normalized_to_integrity_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_v2_archive_fixture(path)
            (path / "manifest.json").write_text("[]\n", encoding="utf-8")

            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)

            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("manifest is not an object", str(caught.exception))

    def test_jsonl_non_object_is_normalized_to_integrity_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_v2_archive_fixture(path)
            nodes_bytes = b"[]\n"
            (path / "nodes.jsonl").write_bytes(nodes_bytes)
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["members"]["nodes"]["sha256"] = hashlib.sha256(nodes_bytes).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)

            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("JSONL row is not an object", str(caught.exception))

    def test_loads_v2_archive_fixture_with_structured_authority(self):
        from theseus_repo_search.model import ArchiveAuthority

        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_v2_archive_fixture(path)
            manifest, nodes, edges, sources = load_artifact(path)

        self.assertEqual(manifest.schema, "theseus.repo-index.v2")
        self.assertEqual(
            manifest.source_authority,
            ArchiveAuthority(
                url=ARCHIVE_URL,
                sha256=ARCHIVE_SHA256,
                format="zip",
                subdir="decreasing-diagrams-lean",
            ),
        )
        self.assertEqual(manifest.source_revision, ARCHIVE_SHA256)
        self.assertFalse(manifest.created_from_authoritative_source)
        self.assertEqual(nodes[0].source_revision, ARCHIVE_SHA256)
        self.assertEqual(edges, [])
        self.assertEqual(sources, [])

    def test_v2_node_rejects_source_commit_field(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_v2_archive_fixture(path, revision_key="source_commit")
            with self.assertRaises(RepoSearchError):
                load_artifact(path)

    def test_v2_writer_serializes_only_generic_revision_fields(self):
        from theseus_repo_search.model import ArchiveAuthority

        node = Node.from_lean(
            full_name="Regular.Main.demo",
            name="demo",
            kind="thm",
            module="Regular.Main",
            source_commit=ARCHIVE_SHA256,
        )
        text = "theorem demo : True := by trivial\n"
        source = SourceChunk(
            id="src:Regular/Main.lean:1:1",
            source_commit=ARCHIVE_SHA256,
            source_path="Regular/Main.lean",
            source_start_line=1,
            source_end_line=1,
            declaration_hint="demo",
            text=text,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        authority = ArchiveAuthority(
            url=ARCHIVE_URL, sha256=ARCHIVE_SHA256, format="zip", subdir="pkg"
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "artifact"
            manifest = write_archive_artifact_v2(
                path,
                nodes=[node],
                edges=[],
                sources=[source],
                source_authority=authority,
                producer=PRODUCER,
                scope=ArtifactScope(root_modules=("Regular",), dependency_boundary="internal_only"),
            )
            node_row = json.loads((path / "nodes.jsonl").read_text().strip())
            source_row = json.loads((path / "sources.jsonl").read_text().strip())
            manifest_row = json.loads((path / "manifest.json").read_text())
            loaded, _, _, _ = load_artifact(path)

        self.assertEqual(manifest, loaded)
        self.assertEqual(node_row["source_revision"], ARCHIVE_SHA256)
        self.assertNotIn("source_commit", node_row)
        self.assertEqual(source_row["source_revision"], ARCHIVE_SHA256)
        self.assertNotIn("source_commit", source_row)
        self.assertIn("created_from_authoritative_source", manifest_row)
        self.assertNotIn("created_from_authoritative_commit", manifest_row)

    def test_v2_identity_binds_full_archive_authority(self):
        from theseus_repo_search.model import ArchiveAuthority, ArtifactManifestV2

        base = ArtifactManifestV2(
            schema="theseus.repo-index.v2",
            source_authority=ArchiveAuthority(
                url=ARCHIVE_URL, sha256=ARCHIVE_SHA256, format="zip", subdir="pkg"
            ),
            producer=PRODUCER,
            scope=ArtifactScope(root_modules=("Regular",), dependency_boundary="internal_only"),
            nodes_sha256="n" * 64,
            edges_sha256="e" * 64,
            sources_sha256=None,
            nodes_count=1,
            edges_count=0,
            created_from_authoritative_source=False,
        )
        variants = [
            replace(base, source_authority=replace(base.source_authority, url=ARCHIVE_URL + "?v=2")),
            replace(base, source_authority=replace(base.source_authority, sha256="5" * 64)),
            replace(base, source_authority=replace(base.source_authority, format="tar")),
            replace(base, source_authority=replace(base.source_authority, subdir="other")),
            replace(base, created_from_authoritative_source=True),
        ]
        base_identity = artifact_identity(base)
        self.assertTrue(all(artifact_identity(item) != base_identity for item in variants))

    def test_v2_schema_rejects_v1_source_shape(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_v2_archive_fixture(path)
            manifest_path = path / "manifest.json"
            payload = json.loads(manifest_path.read_text())
            payload["source"] = {"repo": "owner/repo", "commit": "a" * 40, "subdir": ""}
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RepoSearchError):
                load_artifact(path)

    def test_v2_rejects_mixed_archive_and_git_source_claims(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_v2_archive_fixture(path)
            manifest_path = path / "manifest.json"
            payload = json.loads(manifest_path.read_text())
            payload["source"]["repo"] = "fake/repo"
            payload["source"]["commit"] = "a" * 40
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RepoSearchError):
                load_artifact(path)

    def test_v2_rejects_git_named_authority_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_v2_archive_fixture(path)
            manifest_path = path / "manifest.json"
            payload = json.loads(manifest_path.read_text())
            payload["created_from_authoritative_commit"] = False
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RepoSearchError):
                load_artifact(path)

    def test_v1_schema_rejects_v2_source_shape(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_sample(path)
            manifest_path = path / "manifest.json"
            payload = json.loads(manifest_path.read_text())
            payload["source"] = {
                "kind": "archive",
                "url": ARCHIVE_URL,
                "sha256": ARCHIVE_SHA256,
                "format": "zip",
                "subdir": "pkg",
            }
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RepoSearchError):
                load_artifact(path)

    def test_v2_authoritative_writer_requires_and_validates_receipt_v3(self):
        from theseus_repo_search.model import ArchiveAuthority

        authority = ArchiveAuthority(
            url=ARCHIVE_URL, sha256=ARCHIVE_SHA256, format="zip", subdir="pkg"
        )
        scope = ArtifactScope(root_modules=("Regular",), dependency_boundary="internal_only")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaisesRegex(RepoSearchError, "receipt v3"):
                write_archive_artifact_v2(
                    root / "missing-receipt",
                    nodes=[],
                    edges=[],
                    sources=[],
                    source_authority=authority,
                    producer=PRODUCER,
                    scope=scope,
                    created_from_authoritative_source=True,
                )

            raw = b'{"nodes":[],"edges":[]}\n'
            receipt = (
                json.dumps(
                    {
                        "schema": "theseus.raw-depgraph-receipt.v3",
                        "source": {
                            "kind": "archive",
                            "url": ARCHIVE_URL,
                            "sha256": ARCHIVE_SHA256,
                            "format": "zip",
                            "subdir": "pkg",
                        },
                        "materialization": {
                            "tree_sha256": "d" * 64,
                            "member_manifest_sha256": "e" * 64,
                        },
                        "scope": {"root_modules": ["Regular"]},
                        "producer": {
                            "kind": PRODUCER.kind,
                            "tool_repo": PRODUCER.tool_repo,
                            "tool_commit": PRODUCER.tool_commit,
                            "tool_hash": PRODUCER.tool_hash,
                        },
                        "observed": {"lean_toolchain": "leanprover/lean4:v4.30.0"},
                        "raw_depgraph": {"sha256": sha256(raw).hexdigest()},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
            path = root / "artifact"
            manifest = write_archive_artifact_v2(
                path,
                nodes=[],
                edges=[],
                sources=[],
                source_authority=authority,
                producer=PRODUCER,
                scope=scope,
                created_from_authoritative_source=True,
                authority_receipt=receipt,
                raw_depgraph=raw,
            )
            loaded, nodes, edges, sources = load_artifact(path)
            self.assertEqual(loaded, manifest)
            self.assertTrue(manifest.created_from_authoritative_source)
            self.assertEqual(manifest.authority_receipt_sha256, sha256(receipt).hexdigest())
            self.assertEqual(nodes, [])
            self.assertEqual(edges, [])
            self.assertEqual(sources, [])


    def test_v1_identity_and_manifest_bytes_are_stable(self):
        expected_identity = "2ab27e03377162f22a5af336036793f04d3f09ee1291ffe48fdd1d615100f03a"
        expected_manifest = (
            b'{"counts":{"edges":1,"nodes":2},"created_from_authoritative_commit":false,'
            b'"members":{"authority_receipt":{"sha256":null},'
            b'"edges":{"sha256":"27e76e62e3f81aff319008e0276456b29cd11e9fd0d0ff8ae180bf468329a76f"},'
            b'"nodes":{"sha256":"e4f1a5faf58f8e6150e8d1f850b39de20086c8287d12488d35c1e661f804c0a5"},'
            b'"sources":{"sha256":"2cf88ff3fada95495ee2fa0f8fa49c6ef684d168d4507d6025268a96923a7da4"}},'
            b'"producer":{"kind":"lean-dep-viz","tool_commit":"deadbeef",'
            b'"tool_hash":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",'
            b'"tool_repo":"cameronfreer/LeanDepViz"},"schema":"theseus.repo-index.v1",'
            b'"scope":{"dependency_boundary":"internal_only","root_modules":["Zeta23"]},'
            b'"source":{"commit":"abc123","repo":"anthropics/formal-math","subdir":"zeta23"}}\n'
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            manifest = write_sample(path)
            self.assertEqual(artifact_identity(manifest), expected_identity)
            self.assertEqual((path / "manifest.json").read_bytes(), expected_manifest)

    def test_v1_manifest_exposes_generic_git_authority_without_reserialization(self):
        from theseus_repo_search.model import GitAuthority

        with tempfile.TemporaryDirectory() as d:
            manifest = write_sample(Path(d))
            self.assertEqual(
                manifest.source_authority,
                GitAuthority(
                    repo="anthropics/formal-math",
                    commit=SOURCE_COMMIT,
                    subdir="zeta23",
                ),
            )
            self.assertEqual(manifest.source_revision, SOURCE_COMMIT)
            self.assertFalse(manifest.created_from_authoritative_source)

    def test_source_authority_dispatch_requires_explicit_kind(self):
        from theseus_repo_search.model import GitAuthority, source_authority_from_dict

        git = source_authority_from_dict({
            "kind": "git",
            "repo": "anthropics/formal-math",
            "commit": SOURCE_COMMIT,
            "subdir": "zeta23",
        })
        self.assertIsInstance(git, GitAuthority)
        self.assertEqual(git.source_revision, SOURCE_COMMIT)

        with self.assertRaises(TypeError):
            source_authority_from_dict({
                "url": "https://example.invalid/source.zip",
                "sha256": "4" * 64,
                "format": "zip",
                "subdir": "pkg",
            })

    def test_archive_authority_parses_explicit_discriminator(self):
        from theseus_repo_search.model import (
            ArchiveAuthority,
            source_authority_from_dict,
        )

        authority = source_authority_from_dict({
            "kind": "archive",
            "url": "https://zenodo.org/records/23160921/files/decreasing-diagrams-lean.zip",
            "sha256": "4" * 64,
            "format": "zip",
            "subdir": "decreasing-diagrams-lean",
        })
        self.assertIsInstance(authority, ArchiveAuthority)
        self.assertEqual(authority.source_revision, "4" * 64)

    def test_authoritative_artifact_without_receipt_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "artifact"
            with self.assertRaisesRegex(RepoSearchError, "authoritative artifact requires authority receipt"):
                write_artifact(
                    path,
                    nodes=sample_nodes(),
                    edges=sample_edges(),
                    sources=sample_sources(),
                    source_repo="anthropics/formal-math",
                    source_commit=SOURCE_COMMIT,
                    source_subdir="zeta23",
                    producer=PRODUCER,
                    scope=SCOPE,
                    created_from_authoritative_commit=True,
                )

    def test_authority_bit_participates_in_artifact_identity(self):
        manifest = ArtifactManifest(
            schema="theseus.repo-index.v1",
            source_repo="anthropics/formal-math",
            source_commit=SOURCE_COMMIT,
            source_subdir="zeta23",
            producer=PRODUCER,
            scope=SCOPE,
            nodes_sha256="n" * 64,
            edges_sha256="e" * 64,
            sources_sha256=None,
            nodes_count=1,
            edges_count=1,
            created_from_authoritative_commit=False,
            authority_receipt_sha256=None,
        )
        promoted = replace(manifest, created_from_authoritative_commit=True)
        self.assertNotEqual(artifact_identity(manifest), artifact_identity(promoted))

    def test_write_is_deterministic_across_input_order(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            p1, p2 = Path(d1), Path(d2)
            m1 = write_sample(p1)
            m2 = write_sample(
                p2,
                nodes=list(reversed(sample_nodes())),
                edges=list(reversed(sample_edges())),
                sources=list(reversed(sample_sources())),
            )
            self.assertEqual((p1 / "nodes.jsonl").read_bytes(), (p2 / "nodes.jsonl").read_bytes())
            self.assertEqual((p1 / "edges.jsonl").read_bytes(), (p2 / "edges.jsonl").read_bytes())
            self.assertEqual((p1 / "sources.jsonl").read_bytes(), (p2 / "sources.jsonl").read_bytes())
            self.assertEqual(artifact_identity(m1), artifact_identity(m2))

    def test_published_artifact_is_immutable_and_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "artifact"
            old_manifest = write_sample(path)
            old_identity = artifact_identity(old_manifest)
            extra = Node.from_lean(
                full_name="Zeta23.Tiny.c",
                name="c",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit=SOURCE_COMMIT,
            )

            with self.assertRaises(RepoSearchError) as caught:
                write_sample(path, nodes=sample_nodes() + [extra])
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("immutable", str(caught.exception))

            manifest, _, _, _ = load_artifact(path)
            self.assertEqual(artifact_identity(manifest), old_identity)

    def test_load_round_trips_written_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            manifest = write_sample(path)
            loaded_manifest, nodes, edges, sources = load_artifact(path)
            self.assertEqual(loaded_manifest, manifest)
            self.assertEqual([n.id for n in nodes], ["lean:Zeta23.Tiny.a", "lean:Zeta23.Tiny.b"])
            self.assertEqual(edges, sample_edges())
            self.assertEqual(sources, sample_sources())

    def test_authoritative_exact_artifact_requires_raw_derivation_member(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            receipt = {
                "schema": "theseus.raw-depgraph-receipt.v2",
                "source": {"repo": "anthropics/formal-math", "commit": SOURCE_COMMIT, "subdir": "zeta23"},
                "scope": {"root_modules": ["Zeta23"]},
                "producer": {
                    "kind": PRODUCER.kind,
                    "tool_repo": PRODUCER.tool_repo,
                    "tool_commit": PRODUCER.tool_commit,
                    "tool_hash": PRODUCER.tool_hash,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.33.0"},
                "raw_depgraph": {"sha256": "0" * 64},
            }
            receipt_bytes = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
            _write_artifact_contents(
                path,
                nodes=sample_nodes(),
                edges=sample_edges(),
                sources=sample_sources(),
                source_repo="anthropics/formal-math",
                source_commit=SOURCE_COMMIT,
                source_subdir="zeta23",
                producer=PRODUCER,
                scope=SCOPE,
                created_from_authoritative_commit=True,
                authority_receipt=receipt_bytes,
            )
            with self.assertRaisesRegex(RepoSearchError, "raw dependency graph member"):
                load_artifact(path)

    def test_normalized_graph_must_derive_from_bound_raw_graph(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            raw = raw_depgraph_bytes(sample_nodes(), sample_edges())
            receipt = {
                "schema": "theseus.raw-depgraph-receipt.v2",
                "source": {"repo": "anthropics/formal-math", "commit": SOURCE_COMMIT, "subdir": "zeta23"},
                "scope": {"root_modules": ["Zeta23"]},
                "producer": {
                    "kind": PRODUCER.kind,
                    "tool_repo": PRODUCER.tool_repo,
                    "tool_commit": PRODUCER.tool_commit,
                    "tool_hash": PRODUCER.tool_hash,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.33.0"},
                "raw_depgraph": {"sha256": hashlib.sha256(raw).hexdigest()},
            }
            receipt_bytes = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
            extra = Node.from_lean(
                full_name="Zeta23.Tiny.extra",
                name="extra",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit=SOURCE_COMMIT,
            )
            _write_artifact_contents(
                path,
                nodes=sample_nodes() + [extra],
                edges=sample_edges(),
                sources=sample_sources(),
                source_repo="anthropics/formal-math",
                source_commit=SOURCE_COMMIT,
                source_subdir="zeta23",
                producer=PRODUCER,
                scope=SCOPE,
                created_from_authoritative_commit=True,
                authority_receipt=receipt_bytes,
                raw_depgraph=raw,
            )
            with self.assertRaisesRegex(RepoSearchError, "do not derive from raw dependency graph"):
                load_artifact(path)

    def test_authority_receipt_must_match_manifest_semantics(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            raw = raw_depgraph_bytes(sample_nodes(), sample_edges())
            receipt = {
                "schema": "theseus.raw-depgraph-receipt.v2",
                "source": {"repo": "anthropics/formal-math", "commit": SOURCE_COMMIT, "subdir": "zeta23"},
                "scope": {"root_modules": ["Zeta23"]},
                "producer": {
                    "kind": PRODUCER.kind,
                    "tool_repo": PRODUCER.tool_repo,
                    "tool_commit": PRODUCER.tool_commit,
                    "tool_hash": PRODUCER.tool_hash,
                },
                "observed": {"lean_toolchain": "leanprover/lean4:v4.33.0"},
                "raw_depgraph": {"sha256": hashlib.sha256(raw).hexdigest()},
            }
            receipt_bytes = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
            write_artifact(
                path,
                nodes=sample_nodes(),
                edges=sample_edges(),
                sources=sample_sources(),
                source_repo="anthropics/formal-math",
                source_commit=SOURCE_COMMIT,
                source_subdir="zeta23",
                producer=PRODUCER,
                scope=SCOPE,
                created_from_authoritative_commit=True,
                authority_receipt=receipt_bytes,
                raw_depgraph=raw,
            )
            receipt["source"]["commit"] = "wrong-commit"
            tampered = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
            (path / "authority-receipt.json").write_bytes(tampered)
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["members"]["authority_receipt"]["sha256"] = hashlib.sha256(tampered).hexdigest()
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RepoSearchError, "authority receipt"):
                load_artifact(path)

    def test_tampered_member_hash_blocks_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_sample(path)
            (path / "edges.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_missing_edge_endpoint_blocks_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = Edge(
                source_id="lean:Zeta23.Tiny.b",
                target_id="lean:Zeta23.Tiny.missing",
                relation="value_dependency",
                evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                producer="cameronfreer/LeanDepViz@deadbeef",
            )
            write_sample_raw(path, edges=[edge])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_wrong_node_source_commit_blocks_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            nodes[0] = Node.from_lean(
                full_name="Zeta23.Tiny.b",
                name="b",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit="wrong",
            )
            write_sample_raw(path, nodes=nodes)
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_manifest_schema_and_boundary_are_verified(self):
        for field, value in (("schema", "wrong.schema"), ("boundary", "full_environment")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as d:
                path = Path(d)
                write_sample(path)
                manifest_path = path / "manifest.json"
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if field == "schema":
                    data["schema"] = value
                else:
                    data["scope"]["dependency_boundary"] = value
                manifest_path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(RepoSearchError) as caught:
                    load_artifact(path)
                self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")

    def test_none_sources_omits_optional_member_and_loads_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            manifest = write_sample(path, sources=None)
            self.assertFalse((path / "sources.jsonl").exists())
            self.assertIsNone(manifest.sources_sha256)
            _, _, _, sources = load_artifact(path)
            self.assertEqual(sources, [])

class SourceIntegrityTests(unittest.TestCase):
    def test_source_commit_must_match_manifest_commit(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            original = sample_sources()[0]
            bad = SourceChunk(
                id=original.id,
                source_commit="wrong",
                source_path=original.source_path,
                source_start_line=original.source_start_line,
                source_end_line=original.source_end_line,
                declaration_hint=original.declaration_hint,
                text=original.text,
                content_sha256=original.content_sha256,
            )
            write_sample_raw(path, sources=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("source chunk commit mismatch", str(caught.exception))

    def test_source_content_hash_must_match_text(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            original = sample_sources()[0]
            bad = SourceChunk(
                id=original.id,
                source_commit=original.source_commit,
                source_path=original.source_path,
                source_start_line=original.source_start_line,
                source_end_line=original.source_end_line,
                declaration_hint=original.declaration_hint,
                text=original.text,
                content_sha256="0" * 64,
            )
            write_sample_raw(path, sources=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("source chunk content hash mismatch", str(caught.exception))

class StructuralIntegrityTests(unittest.TestCase):
    def test_duplicate_node_ids_are_blocked_before_projection(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            write_sample_raw(path, nodes=[nodes[0], nodes[0], nodes[1]])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("duplicate node id", str(caught.exception))

    def test_out_of_scope_node_is_blocked_for_internal_only_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes() + [
                Node.from_lean(
                    full_name="Mathlib.Algebra.outside",
                    name="outside",
                    kind="thm",
                    module="Mathlib.Algebra",
                    source_commit=SOURCE_COMMIT,
                )
            ]
            edges = sample_edges() + [
                Edge(
                    source_id="lean:Zeta23.Tiny.b",
                    target_id="lean:Mathlib.Algebra.outside",
                    relation="value_dependency",
                    evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                    producer="cameronfreer/LeanDepViz@deadbeef",
                )
            ]
            write_sample_raw(path, nodes=nodes, edges=edges)
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("node outside declared root-module scope", str(caught.exception))


    def test_malformed_quoted_module_rejects_artifact_with_structured_cli_error(self):
        for malformed in ("Zeta23.Tiny»", "Zeta23.«Tiny", "Zeta23.««Tiny»"):
            with self.subTest(module=malformed), tempfile.TemporaryDirectory() as d:
                path = Path(d)
                nodes = sample_nodes()
                bad = replace(
                    nodes[0],
                    module=malformed,
                    source_path="Zeta23/Tiny.lean",
                    source_start_line=1,
                    source_end_line=1,
                )
                write_sample_raw(path, nodes=[bad, nodes[1]])
                with self.assertRaises(RepoSearchError) as caught:
                    load_artifact(path)
                self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
                result = subprocess.run(
                    [sys.executable, "-m", "theseus_repo_search", "verify-artifact", "--artifact", str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                error = json.loads(result.stderr)
                self.assertEqual(error["code"], "BLOCKED_ARTIFACT_INTEGRITY")

    def test_node_source_location_must_match_unique_source_chunk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            bad = Node(
                id=nodes[0].id,
                full_name=nodes[0].full_name,
                name=nodes[0].name,
                kind=nodes[0].kind,
                module=nodes[0].module,
                source_path="Zeta23/Tiny.lean",
                source_start_line=1,
                source_end_line=1,
                source_commit=nodes[0].source_commit,
            )
            write_sample_raw(path, nodes=[bad, nodes[1]])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("node source location mismatch", str(caught.exception))

    def test_duplicate_short_names_cannot_swap_module_source_locations(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            text_a = "theorem foo : True := by trivial\n"
            text_b = "theorem foo : True := by trivial\n"
            sources = [
                SourceChunk(
                    id="src:Zeta23/A.lean:1:1",
                    source_commit=SOURCE_COMMIT,
                    source_path="Zeta23/A.lean",
                    source_start_line=1,
                    source_end_line=1,
                    declaration_hint="foo",
                    text=text_a,
                    content_sha256=sha256(text_a.encode()).hexdigest(),
                ),
                SourceChunk(
                    id="src:Zeta23/B.lean:1:1",
                    source_commit=SOURCE_COMMIT,
                    source_path="Zeta23/B.lean",
                    source_start_line=1,
                    source_end_line=1,
                    declaration_hint="foo",
                    text=text_b,
                    content_sha256=sha256(text_b.encode()).hexdigest(),
                ),
            ]
            nodes = [
                Node(
                    id="lean:Zeta23.A.foo",
                    full_name="Zeta23.A.foo",
                    name="foo",
                    kind="thm",
                    module="Zeta23.A",
                    source_path="Zeta23/B.lean",
                    source_start_line=1,
                    source_end_line=1,
                    source_commit=SOURCE_COMMIT,
                ),
                Node(
                    id="lean:Zeta23.B.foo",
                    full_name="Zeta23.B.foo",
                    name="foo",
                    kind="thm",
                    module="Zeta23.B",
                    source_path="Zeta23/A.lean",
                    source_start_line=1,
                    source_end_line=1,
                    source_commit=SOURCE_COMMIT,
                ),
            ]
            write_sample_raw(path, nodes=nodes, edges=[], sources=sources)
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("node source location mismatch", str(caught.exception))

    def test_partial_node_source_location_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            bad = Node(
                id=nodes[0].id,
                full_name=nodes[0].full_name,
                name=nodes[0].name,
                kind=nodes[0].kind,
                module=nodes[0].module,
                source_path="Zeta23/Tiny.lean",
                source_start_line=None,
                source_end_line=None,
                source_commit=nodes[0].source_commit,
            )
            write_sample_raw(path, nodes=[bad, nodes[1]])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("partial node source location", str(caught.exception))

    def test_noncanonical_lean_node_id_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            bad = Node(
                id="python:Zeta23.Tiny.b",
                full_name=nodes[0].full_name,
                name=nodes[0].name,
                kind=nodes[0].kind,
                module=nodes[0].module,
                source_path=nodes[0].source_path,
                source_start_line=nodes[0].source_start_line,
                source_end_line=nodes[0].source_end_line,
                source_commit=nodes[0].source_commit,
            )
            write_sample_raw(path, nodes=[bad, nodes[1]], edges=[])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("non-canonical Lean node id", str(caught.exception))

    def test_lean_node_id_must_match_full_declaration_identity(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            nodes = sample_nodes()
            bad = replace(nodes[0], id="lean:Zeta23.Forged.not_b")
            write_sample_raw(path, nodes=[bad, nodes[1]], edges=[])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("node id/full-name mismatch", str(caught.exception))

    def test_edge_producer_must_match_manifest_pin(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = sample_edges()[0]
            bad = Edge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                relation=edge.relation,
                evidence_grade=edge.evidence_grade,
                producer="other/extractor@badc0de",
            )
            write_sample_raw(path, edges=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("edge producer mismatch", str(caught.exception))

    def test_duplicate_edge_rows_are_blocked_before_projection(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = sample_edges()[0]
            write_sample_raw(path, edges=[edge, edge])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("duplicate edge", str(caught.exception))

    def test_duplicate_source_ids_are_blocked_before_projection(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            source = sample_sources()[0]
            write_sample_raw(path, sources=[source, source])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("duplicate source id", str(caught.exception))

    def test_dependency_relation_must_match_evidence_grade(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = Edge(
                source_id="lean:Zeta23.Tiny.b",
                target_id="lean:Zeta23.Tiny.a",
                relation="value_dependency",
                evidence_grade=EvidenceGrade.ELABORATED_TYPE_DEPENDENCY,
                producer="cameronfreer/LeanDepViz@deadbeef",
            )
            write_sample_raw(path, edges=[edge])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("relation/evidence mismatch", str(caught.exception))

    def test_unknown_dependency_relation_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            edge = Edge(
                source_id="lean:Zeta23.Tiny.b",
                target_id="lean:Zeta23.Tiny.a",
                relation="mystery_dependency",
                evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                producer="cameronfreer/LeanDepViz@deadbeef",
            )
            write_sample_raw(path, edges=[edge])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("unsupported dependency relation", str(caught.exception))

    def test_invalid_source_line_range_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            original = sample_sources()[0]
            bad = SourceChunk(
                id="src:Zeta23/Tiny.lean:2:1",
                source_commit=original.source_commit,
                source_path=original.source_path,
                source_start_line=2,
                source_end_line=1,
                declaration_hint=original.declaration_hint,
                text=original.text,
                content_sha256=original.content_sha256,
            )
            write_sample_raw(path, sources=[bad])
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("invalid source range", str(caught.exception))

    def test_null_node_id_is_blocked_instead_of_coerced_to_string(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            write_sample(path)
            nodes_path = path / "nodes.jsonl"
            rows = [json.loads(line) for line in nodes_path.read_text(encoding="utf-8").splitlines()]
            rows[0]["id"] = None
            data = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
            nodes_path.write_text(data, encoding="utf-8")
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["members"]["nodes"]["sha256"] = hashlib.sha256(data.encode()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            with self.assertRaises(RepoSearchError) as caught:
                load_artifact(path)
            self.assertEqual(caught.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
            self.assertIn("invalid artifact record", str(caught.exception))
