import unittest
from dataclasses import replace

from theseus_repo_search.accepted_catalog import AcceptedCorpus
from theseus_repo_search.split_intake import motherduck_targets
from theseus_repo_search.split_turso import split_database_name, split_projection_sha256


class SplitSelectionTests(unittest.TestCase):
    def setUp(self):
        self.split = {"profile":"repo-search-split-v1","artifact_identity":"a"*64,
                      "full_projection_fingerprint":"b"*64,"search_projection_fingerprint":"c"*64,
                      "nodes":3,"edges":2,"sources":4}
        self.item = AcceptedCorpus("fixture","tag","asset","receipt","descriptor","a"*64,"d"*64,
                                   split_projection_sha256=split_projection_sha256(self.split))
        self.evidence = {"split":self.split,
                         "search":{"status":"CREATED","database":split_database_name(self.item),
                                   "artifact_identity":self.item.artifact_identity,
                                   "split_projection_sha256":self.item.split_projection_sha256},
                         "graph":{"status":"CREATED","tables":list(motherduck_targets(self.item,"my_db")),
                                  "nodes":3,"edges":2}}
        self.report = {"graph_nodes":3,"graph_edges":2}

    def test_exact_split_evidence_required_and_target_identity_checked(self):
        from scripts.check_corpus_selection import validate_split_evidence

        validate_split_evidence(self.evidence,self.item,self.report)
        for bad in (None,{**self.evidence,"split":{**self.split,"edges":9}},
                    {**self.evidence,"search":{**self.evidence["search"],"database":"old-full"}},
                    {**self.evidence,"graph":{**self.evidence["graph"],"edges":1}},
                    {**self.evidence,"graph":{**self.evidence["graph"],"tables":["my_db.main.wrong"]}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                validate_split_evidence(bad,self.item,self.report)

    def test_legacy_entry_cannot_carry_split_evidence(self):
        from scripts.check_corpus_selection import validate_split_evidence

        with self.assertRaises(ValueError):
            validate_split_evidence(self.evidence,replace(self.item,split_projection_sha256=None),self.report)

    def test_context_requires_local_split_route(self):
        from scripts.check_corpus_selection import validate_context

        w={"declaration":"M.a","source_path":"M.lean"}
        c={"source_id":"fixture","artifact_identity":"a"*64,"status":"FOUND","target_id":"lean:M.a",
           "chunks":[{"declaration_id":"lean:M.a","source_path":"M.lean"}]}
        with self.assertRaisesRegex(ValueError,"local"):
            validate_context(c,w,self.item)
        validate_context({**c,"graph_backend":"local-duckdb","split_projection_sha256":self.item.split_projection_sha256},w,self.item)
