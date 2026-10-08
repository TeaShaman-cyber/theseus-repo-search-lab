import json
import tempfile
import unittest
from pathlib import Path

from theseus_repo_search.accepted_catalog import (
    accepted_catalog_sha256,
    load_accepted_catalog,
)


EXPECTED_SOURCE_IDS = {
    "annals-challenge-marton",
    "decreasing-diagrams-complete",
    "leanprover-community-con-nf",
    "leanprover-community-flt-regular",
    "openai-cdc-lean",
    "openai-long-gaps",
    "openai-ten-proofs-multicolor",
    "zeta23",
}


def _entry(source_id="annals-challenge-marton", **overrides):
    entry = {
        "source_id": source_id,
        "release_tag": f"accepted-artifact/{source_id}/0123456789abcdef",
        "package_asset": "accepted-artifact.tar.gz",
        "receipt_asset": "release-receipt.json",
        "source_descriptor": f"producer/sources/{source_id}.json",
        "artifact_identity": "1" * 64,
        "fingerprint_sha256": "2" * 64,
    }
    entry.update(overrides)
    return entry


def _payload(corpora=None, **overrides):
    payload = {
        "schema": "theseus.repo-search.accepted-catalog.v1",
        "release_repository": "TeaShaman-cyber/theseus-repo-search-lab",
        "corpora": [_entry()] if corpora is None else corpora,
    }
    payload.update(overrides)
    return payload


class AcceptedCatalogTests(unittest.TestCase):
    def _load(self, payload):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "catalog.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_accepted_catalog(path)

    def test_rejects_wrong_schema_and_unknown_fields(self):
        cases = [
            _payload(schema="theseus.repo-search.accepted-catalog.v2"),
            _payload(extra=True),
            _payload(corpora=[_entry(extra=True)]),
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self._load(payload)

    def test_rejects_bad_hex_descriptor_paths_and_line_breaks(self):
        cases = [
            _payload(corpora=[_entry(artifact_identity="A" * 64)]),
            _payload(corpora=[_entry(fingerprint_sha256="f" * 63)]),
            _payload(corpora=[_entry(source_descriptor="/tmp/source.json")]),
            _payload(corpora=[_entry(source_descriptor="producer/sources/../evil.json")]),
            _payload(corpora=[_entry(source_descriptor="producer/other/source.json")]),
            _payload(corpora=[_entry(release_tag="tag\nnext")]),
            _payload(corpora=[_entry(package_asset="archive.tar.gz\rspoof")]),
            _payload(release_repository="TeaShaman-cyber/repo\nother"),
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self._load(payload)

    def test_rejects_duplicate_identity_keys(self):
        base = _entry("a")
        cases = [
            [base, _entry("a", release_tag="tag-b", artifact_identity="3" * 64)],
            [base, _entry("b", release_tag=base["release_tag"], artifact_identity="3" * 64)],
            [base, _entry("b", release_tag="tag-b", artifact_identity=base["artifact_identity"])],
        ]
        for corpora in cases:
            with self.subTest(corpora=corpora), self.assertRaises(ValueError):
                self._load(_payload(corpora=corpora))

    def test_digest_is_canonical_across_key_and_corpus_order(self):
        a = _entry("a", release_tag="tag-a", artifact_identity="a" * 64)
        b = _entry("b", release_tag="tag-b", artifact_identity="b" * 64)
        left = self._load(_payload(corpora=[a, b]))
        reversed_keys = [dict(reversed(list(item.items()))) for item in (b, a)]
        right_payload = {
            "corpora": reversed_keys,
            "release_repository": "TeaShaman-cyber/theseus-repo-search-lab",
            "schema": "theseus.repo-search.accepted-catalog.v1",
        }
        right = self._load(right_payload)
        self.assertEqual(accepted_catalog_sha256(left), accepted_catalog_sha256(right))

    def test_repository_catalog_has_exact_sorted_eight_sources(self):
        path = Path(__file__).resolve().parents[1] / "producer" / "accepted-corpora.json"
        catalog = load_accepted_catalog(path)
        source_ids = [entry.source_id for entry in catalog.corpora]
        self.assertEqual(set(source_ids), EXPECTED_SOURCE_IDS)
        self.assertEqual(source_ids, sorted(source_ids))
        self.assertEqual(len(source_ids), 8)


if __name__ == "__main__":
    unittest.main()
