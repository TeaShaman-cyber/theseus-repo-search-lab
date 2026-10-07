import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest import mock

from tests.raw_fixture import raw_depgraph_bytes
from theseus_repo_search.artifact import (
    artifact_identity,
    write_archive_artifact_v2,
    write_artifact,
)
from theseus_repo_search.model import (
    ArchiveAuthority,
    ArtifactScope,
    Edge,
    EvidenceGrade,
    Node,
    ProducerPin,
    SourceChunk,
)
from theseus_repo_search.projection import build_projection, projection_fingerprint


def authority_receipt_bytes(producer: ProducerPin, raw: bytes) -> bytes:
    import json
    payload = {
        "schema": "theseus.raw-depgraph-receipt.v2",
        "source": {"repo": "anthropics/formal-math", "commit": "abc123", "subdir": "zeta23"},
        "scope": {"root_modules": ["Zeta23"]},
        "producer": {
            "kind": producer.kind,
            "tool_repo": producer.tool_repo,
            "tool_commit": producer.tool_commit,
            "tool_hash": producer.tool_hash,
        },
        "observed": {"lean_toolchain": "leanprover/lean4:test"},
        "raw_depgraph": {"sha256": sha256(raw).hexdigest()},
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


class ProjectionTests(unittest.TestCase):
    def build_artifact(self, path: Path):
        nodes = [
            Node.from_lean(
                full_name="Zeta23.Tiny.a",
                name="a",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit="abc123",
            ),
            Node.from_lean(
                full_name="Zeta23.Tiny.b",
                name="b",
                kind="thm",
                module="Zeta23.Tiny",
                source_commit="abc123",
            ),
        ]
        edges = [
            Edge(
                source_id="lean:Zeta23.Tiny.b",
                target_id="lean:Zeta23.Tiny.a",
                relation="value_dependency",
                evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                producer="cameronfreer/LeanDepViz@deadbeef",
            )
        ]
        text = "/-- Equality-family witness for rank trace tightness. -/\nlemma a : True := by trivial\n"
        sources = [
            SourceChunk(
                id="src:Zeta23/Tiny.lean:1:2",
                source_commit="abc123",
                source_path="Zeta23/Tiny.lean",
                source_start_line=1,
                source_end_line=2,
                declaration_hint="lemmaR_tight_two",
                text=text,
                content_sha256=sha256(text.encode()).hexdigest(),
            )
        ]
        producer = ProducerPin(
            kind="lean-dep-viz",
            tool_repo="cameronfreer/LeanDepViz",
            tool_commit="deadbeef",
            tool_hash="f" * 64,
        )
        raw = raw_depgraph_bytes(nodes, edges)
        return write_artifact(
            path,
            nodes=nodes,
            edges=edges,
            sources=sources,
            source_repo="anthropics/formal-math",
            source_commit="abc123",
            source_subdir="zeta23",
            producer=producer,
            scope=ArtifactScope(
                root_modules=("Zeta23",),
                dependency_boundary="internal_only",
            ),
            created_from_authoritative_commit=True,
            authority_receipt=authority_receipt_bytes(producer, raw),
            raw_depgraph=raw,
        )

    def test_projection_fingerprint_is_logically_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact_dir = root / "artifact"
            manifest = self.build_artifact(artifact_dir)
            db1, db2 = root / "one.db", root / "two.db"
            fp1 = build_projection(artifact_dir, db1)
            fp2 = build_projection(artifact_dir, db2)
            self.assertEqual(fp1, fp2)
            self.assertEqual(projection_fingerprint(db1), fp1)

            with sqlite3.connect(db1) as conn:
                meta = dict(conn.execute("SELECT key, value FROM meta"))
            self.assertEqual(meta["artifact_identity"], artifact_identity(manifest))
            self.assertEqual(meta["source_commit"], "abc123")
            self.assertEqual(meta["root_modules"], '["Zeta23"]')
            self.assertEqual(meta["dependency_boundary"], "internal_only")
            self.assertEqual(meta["producer.kind"], "lean-dep-viz")
            self.assertEqual(meta["created_from_authoritative_commit"], "true")

    def test_projection_contains_expected_rows_and_working_fts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact_dir = root / "artifact"
            self.build_artifact(artifact_dir)
            db = root / "projection.db"
            build_projection(artifact_dir, db)

            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM nodes").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT count(*) FROM edges").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT count(*) FROM sources").fetchone()[0], 1)
                hits = conn.execute(
                    "SELECT s.id FROM sources_fts JOIN sources s ON s.rowid = sources_fts.rowid "
                    "WHERE sources_fts MATCH ?",
                    ("tightness",),
                ).fetchall()
            self.assertEqual(hits, [("src:Zeta23/Tiny.lean:1:2",)])

    def test_v2_projection_uses_generic_revision_and_structured_authority_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = root / "artifact-v2"
            db = root / "projection-v2.db"
            revision = "a" * 64
            text = "theorem ok : True := by trivial\n"
            node = replace(
                Node.from_lean(
                    full_name="Pkg.Main.ok",
                    name="ok",
                    kind="thm",
                    module="Pkg.Main",
                    source_commit=revision,
                ),
                source_path="Pkg/Main.lean",
                source_start_line=1,
                source_end_line=1,
            )
            source = SourceChunk(
                id="src:Pkg/Main.lean:1:1",
                source_commit=revision,
                source_path="Pkg/Main.lean",
                source_start_line=1,
                source_end_line=1,
                declaration_hint="ok",
                text=text,
                content_sha256=sha256(text.encode()).hexdigest(),
            )
            authority = ArchiveAuthority(
                url="https://example.invalid/source.zip",
                sha256=revision,
                format="zip",
                subdir=".",
            )
            write_archive_artifact_v2(
                artifact,
                nodes=[node],
                edges=[],
                sources=[source],
                source_authority=authority,
                producer=ProducerPin(
                    kind="lean-dep-viz",
                    tool_repo="cameronfreer/LeanDepViz",
                    tool_commit="deadbeef",
                    tool_hash="f" * 64,
                ),
                scope=ArtifactScope(
                    root_modules=("Pkg",), dependency_boundary="internal_only"
                ),
            )

            build_projection(artifact, db)
            with sqlite3.connect(db) as conn:
                meta = dict(conn.execute("SELECT key, value FROM meta"))
                node_columns = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
                source_columns = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
                schema_sql = "\n".join(
                    row[0] or ""
                    for row in conn.execute(
                        "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY name"
                    )
                )

            self.assertEqual(meta["artifact_schema"], "theseus.repo-index.v2")
            self.assertEqual(meta["source_kind"], "archive")
            self.assertEqual(meta["source_revision"], revision)
            self.assertEqual(
                json.loads(meta["source_authority_json"]),
                {
                    "kind": "archive",
                    "url": authority.url,
                    "sha256": authority.sha256,
                    "format": authority.format,
                    "subdir": authority.subdir,
                },
            )
            self.assertEqual(meta["created_from_authoritative_source"], "false")
            self.assertIn("artifact_identity", meta)
            self.assertNotIn("source_commit", meta)
            self.assertNotIn("created_from_authoritative_commit", meta)
            self.assertIn("source_revision", node_columns)
            self.assertIn("source_revision", source_columns)
            self.assertNotIn("source_commit", node_columns)
            self.assertNotIn("source_commit", source_columns)
            self.assertNotIn("source_commit", schema_sql)

    def test_v2_provenance_reader_rejects_git_named_revision_column(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = root / "artifact-v2"
            db = root / "projection-v2.db"
            revision = "a" * 64
            authority = ArchiveAuthority(
                url="https://example.invalid/source.zip",
                sha256=revision,
                format="zip",
                subdir=".",
            )
            write_archive_artifact_v2(
                artifact,
                nodes=[],
                edges=[],
                sources=[],
                source_authority=authority,
                producer=ProducerPin(
                    kind="lean-dep-viz",
                    tool_repo="cameronfreer/LeanDepViz",
                    tool_commit="deadbeef",
                    tool_hash="f" * 64,
                ),
                scope=ArtifactScope(
                    root_modules=("Pkg",), dependency_boundary="internal_only"
                ),
            )
            build_projection(artifact, db)
            with sqlite3.connect(db) as conn:
                conn.execute("ALTER TABLE nodes RENAME COLUMN source_revision TO source_commit")
                conn.commit()
            from theseus_repo_search.errors import RepoSearchError
            from theseus_repo_search.projection import read_projection_provenance

            with sqlite3.connect(db) as conn, self.assertRaisesRegex(
                RepoSearchError, "revision columns"
            ):
                read_projection_provenance(conn)

    def test_v2_provenance_reader_rejects_mixed_git_archive_authority(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact = root / "artifact-v2"
            db = root / "projection-v2.db"
            revision = "a" * 64
            authority = ArchiveAuthority(
                url="https://example.invalid/source.zip",
                sha256=revision,
                format="zip",
                subdir=".",
            )
            write_archive_artifact_v2(
                artifact,
                nodes=[],
                edges=[],
                sources=[],
                source_authority=authority,
                producer=ProducerPin(
                    kind="lean-dep-viz",
                    tool_repo="cameronfreer/LeanDepViz",
                    tool_commit="deadbeef",
                    tool_hash="f" * 64,
                ),
                scope=ArtifactScope(
                    root_modules=("Pkg",), dependency_boundary="internal_only"
                ),
            )
            build_projection(artifact, db)
            with sqlite3.connect(db) as conn:
                payload = json.loads(
                    dict(conn.execute("SELECT key, value FROM meta"))[
                        "source_authority_json"
                    ]
                )
                payload["repo"] = "fake/repo"
                payload["commit"] = "b" * 40
                conn.execute(
                    "UPDATE meta SET value=? WHERE key='source_authority_json'",
                    (json.dumps(payload),),
                )
                conn.commit()
            from theseus_repo_search.errors import RepoSearchError
            from theseus_repo_search.projection import read_projection_provenance

            with sqlite3.connect(db) as conn, self.assertRaisesRegex(
                RepoSearchError, "exact archive object"
            ):
                read_projection_provenance(conn)

    def test_projection_fingerprint_changes_on_fts_posting_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact_dir = root / "artifact"
            self.build_artifact(artifact_dir)
            db = root / "projection.db"
            original = build_projection(artifact_dir, db)
            with sqlite3.connect(db) as conn:
                rowid, hint, text = conn.execute(
                    "SELECT rowid,declaration_hint,text FROM sources ORDER BY rowid LIMIT 1"
                ).fetchone()
                conn.execute(
                    "INSERT INTO sources_fts(sources_fts,rowid,declaration_hint,text) "
                    "VALUES('delete',?,?,?)",
                    (rowid, hint, text),
                )
                conn.execute(
                    "INSERT INTO sources_fts(rowid,declaration_hint,text) VALUES (?,?,?)",
                    (rowid, hint, "completely unrelated token"),
                )
                conn.commit()
                self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertNotEqual(projection_fingerprint(db), original)

    def test_projection_fingerprint_changes_on_fts_docsize_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact_dir = root / "artifact"
            self.build_artifact(artifact_dir)
            db = root / "projection.db"
            original = build_projection(artifact_dir, db)
            with sqlite3.connect(db) as conn:
                rowid, size_blob = conn.execute(
                    "SELECT id,sz FROM sources_fts_docsize ORDER BY id LIMIT 1"
                ).fetchone()
                conn.execute(
                    "UPDATE sources_fts_docsize SET sz=? WHERE id=?",
                    (sqlite3.Binary(b"\\x01" * max(1, len(size_blob))), rowid),
                )
                conn.commit()
                self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertNotEqual(projection_fingerprint(db), original)

    def test_projection_fingerprint_changes_on_fts_averages_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact_dir = root / "artifact"
            self.build_artifact(artifact_dir)
            db = root / "projection.db"
            original = build_projection(artifact_dir, db)
            with sqlite3.connect(db) as conn:
                averages = bytearray(
                    conn.execute(
                        "SELECT block FROM sources_fts_data WHERE id=1"
                    ).fetchone()[0]
                )
                self.assertGreaterEqual(len(averages), 2)
                averages[-1] = max(1, averages[-1] // 2)
                conn.execute(
                    "UPDATE sources_fts_data SET block=? WHERE id=1",
                    (sqlite3.Binary(bytes(averages)),),
                )
                conn.commit()
                self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertNotEqual(projection_fingerprint(db), original)

    def test_projection_fingerprint_rejects_orphan_fts_document(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact_dir = root / "artifact"
            self.build_artifact(artifact_dir)
            db = root / "projection.db"
            build_projection(artifact_dir, db)
            with sqlite3.connect(db) as conn:
                averages = conn.execute(
                    "SELECT block FROM sources_fts_data WHERE id=1"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO sources_fts(rowid,declaration_hint,text) VALUES (?,?,?)",
                    (999999, "orphan", "tightness unrelated"),
                )
                # Isolate the orphan-document defect from averages drift.
                conn.execute(
                    "UPDATE sources_fts_data SET block=? WHERE id=1",
                    (sqlite3.Binary(averages),),
                )
                conn.commit()
                self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
            with self.assertRaisesRegex(ValueError, "orphan FTS document"):
                projection_fingerprint(db)

    def test_failed_rebuild_preserves_last_valid_projection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact_dir = root / "artifact"
            self.build_artifact(artifact_dir)
            db = root / "projection.db"
            old_fingerprint = build_projection(artifact_dir, db)
            with mock.patch(
                "theseus_repo_search.projection._FTS_STATEMENT",
                "CREATE VIRTUAL TABLE sources_fts USING definitely_not_a_module(x)",
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    build_projection(artifact_dir, db)
            self.assertEqual(projection_fingerprint(db), old_fingerprint)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM sources").fetchone()[0], 1)

    def test_projection_fingerprint_includes_actual_fts_schema(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact_dir = root / "artifact"
            self.build_artifact(artifact_dir)
            db = root / "projection.db"
            original = build_projection(artifact_dir, db)
            with sqlite3.connect(db) as conn:
                conn.execute("DROP TABLE sources_fts")
                conn.execute(
                    "CREATE VIRTUAL TABLE sources_fts USING fts5("
                    "declaration_hint,text,content='sources',content_rowid='rowid',"
                    "tokenize='porter unicode61')"
                )
                conn.execute("INSERT INTO sources_fts(sources_fts) VALUES('rebuild')")
            changed = projection_fingerprint(db)
            self.assertNotEqual(changed, original)
