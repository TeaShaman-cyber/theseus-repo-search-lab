import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import test_projection
from theseus_repo_search.projection import build_projection, projection_fingerprint
from theseus_repo_search.retrieval import search_connection


class SplitProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        artifact = self.root / "artifact"
        test_projection.ProjectionTests().build_artifact(artifact)
        self.full = self.root / "full.sqlite"
        self.fingerprint = build_projection(artifact, self.full)

    def test_split_preserves_search_and_exports_exact_typed_edges(self):
        from theseus_repo_search.split_projection import (
            build_split_projection,
            verify_split_projection,
        )

        out = self.root / "split"
        receipt = build_split_projection(self.full, out, self.fingerprint)
        self.assertEqual(verify_split_projection(out, self.fingerprint), receipt)
        with sqlite3.connect(self.full) as full, sqlite3.connect(out / "search.sqlite") as search:
            self.assertEqual(search_connection(full, "Bernoulli"), search_connection(search, "Bernoulli"))
            self.assertEqual(full.execute("SELECT * FROM nodes").fetchall(), search.execute("SELECT * FROM nodes").fetchall())
            self.assertEqual(full.execute("SELECT * FROM sources").fetchall(), search.execute("SELECT * FROM sources").fetchall())
            self.assertIsNone(search.execute("SELECT name FROM sqlite_master WHERE name='edges'").fetchone())
            with (out / "nodes.csv").open(newline="") as f:
                ids = {int(row["node_key"]): row["id"] for row in csv.DictReader(f)}
            with (out / "edges.csv").open(newline="") as f:
                edges = [(ids[int(r["source_key"])], ids[int(r["target_key"])], r["relation"], r["evidence_grade"], r["producer"]) for r in csv.DictReader(f)]
            self.assertEqual(edges, full.execute("SELECT * FROM edges ORDER BY source_id,target_id,relation,producer").fetchall())
        self.assertEqual(projection_fingerprint(self.full), self.fingerprint)
        with self.assertRaises((ValueError, sqlite3.Error)):
            projection_fingerprint(out / "search.sqlite")

    def test_wrong_parent_fingerprint_leaves_no_candidate(self):
        from theseus_repo_search.split_projection import build_split_projection

        out = self.root / "split"
        with self.assertRaises(ValueError):
            build_split_projection(self.full, out, "0" * 64)
        self.assertFalse(out.exists())

    def test_changed_edge_file_is_rejected(self):
        from theseus_repo_search.split_projection import (
            build_split_projection,
            verify_split_projection,
        )

        out = self.root / "split"
        build_split_projection(self.full, out, self.fingerprint)
        with (out / "edges.csv").open("a") as f:
            f.write("1,2,wrong,wrong,wrong\n")
        with self.assertRaises(ValueError):
            verify_split_projection(out, self.fingerprint)

    def test_rehashed_wrong_edge_still_rejected_by_parent_fingerprint(self):
        from hashlib import sha256

        from theseus_repo_search.split_projection import (
            build_split_projection,
            verify_split_projection,
        )

        out = self.root / "split"
        build_split_projection(self.full, out, self.fingerprint)
        edge_file = out / "edges.csv"
        original = edge_file.read_bytes()
        changed = original.replace(b"value_dependency", b"type_dependency")
        self.assertNotEqual(original, changed)
        edge_file.write_bytes(changed)
        p = out / "receipt.json"
        r = json.loads(p.read_text())
        r["files"]["edges.csv"] = {"sha256": sha256(changed).hexdigest(), "bytes": len(changed)}
        p.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError, "differs from accepted full"):
            verify_split_projection(out, self.fingerprint)

    def test_duplicate_meta_identity_rejected_even_with_matching_file_hash(self):
        from hashlib import file_digest

        from theseus_repo_search.split_projection import (
            build_split_projection,
            verify_split_projection,
        )

        out = self.root / "split"
        build_split_projection(self.full, out, self.fingerprint)
        search = out / "search.sqlite"
        with sqlite3.connect(search) as c:
            c.execute("ALTER TABLE meta RENAME TO original_meta")
            c.execute("CREATE TABLE meta (key TEXT, value TEXT)")
            c.execute("INSERT INTO meta VALUES ('artifact_identity', 'spoof')")
            c.execute("INSERT INTO meta SELECT * FROM original_meta")
            c.execute("DROP TABLE original_meta")
        receipt = out / "receipt.json"
        r = json.loads(receipt.read_text())
        with search.open("rb") as f:
            r["files"]["search.sqlite"] = {"sha256": file_digest(f, "sha256").hexdigest(), "bytes": search.stat().st_size}
        receipt.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError, "metadata"):
            verify_split_projection(out, self.fingerprint)

    def test_changed_search_identity_is_rejected(self):
        from theseus_repo_search.split_projection import (
            build_split_projection,
            verify_split_projection,
        )

        out = self.root / "split"
        build_split_projection(self.full, out, self.fingerprint)
        with sqlite3.connect(out / "search.sqlite") as c:
            c.execute("UPDATE meta SET value='wrong' WHERE key='artifact_identity'")
        with self.assertRaises(ValueError):
            verify_split_projection(out, self.fingerprint)

    def test_receipt_parent_cannot_be_substituted(self):
        from theseus_repo_search.split_projection import (
            build_split_projection,
            verify_split_projection,
        )

        out = self.root / "split"
        build_split_projection(self.full, out, self.fingerprint)
        p = out / "receipt.json"
        r = json.loads(p.read_text())
        r["full_projection_fingerprint"] = "0" * 64
        p.write_text(json.dumps(r))
        with self.assertRaises(ValueError):
            verify_split_projection(out, self.fingerprint)
