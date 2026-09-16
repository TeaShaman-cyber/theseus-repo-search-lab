import sqlite3
import tempfile
import unittest
from unittest import mock
from hashlib import sha256
from pathlib import Path

from theseus_repo_search.artifact import artifact_identity, write_artifact
from theseus_repo_search.model import (
    ArtifactScope,
    Edge,
    EvidenceGrade,
    Node,
    ProducerPin,
    SourceChunk,
)
from theseus_repo_search.projection import build_projection, projection_fingerprint


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
                producer="LeanDepViz@deadbeef",
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
        return write_artifact(
            path,
            nodes=nodes,
            edges=edges,
            sources=sources,
            source_repo="anthropics/formal-math",
            source_commit="abc123",
            source_subdir="zeta23",
            producer=ProducerPin(
                kind="lean-dep-viz",
                tool_repo="cameronfreer/LeanDepViz",
                tool_commit="deadbeef",
                tool_hash="f" * 64,
            ),
            scope=ArtifactScope(
                root_modules=("Zeta23",),
                dependency_boundary="internal_only",
            ),
            created_from_authoritative_commit=True,
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
