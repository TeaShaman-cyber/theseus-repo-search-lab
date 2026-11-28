import unittest
from unittest.mock import Mock, patch

from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.kusto_query import federated_reach


class FederatedGraphTests(unittest.TestCase):
    def setUp(self):
        self.items = tuple(AcceptedCorpus(s, "tag", "asset", "receipt", "descriptor", x*64, "b"*64)
                           for s,x in (("first","a"),("second","c")))
        self.catalog = AcceptedCatalog("theseus.repo-search.accepted-catalog.v1","owner/repo",self.items)

    def test_same_name_never_collapses_distinct_origins(self):
        def result(item, *_args, **_kw):
            return {"status":"FOUND","found":True,"complete_within_scope":True,
                    "source_id":item.source_id,"artifact_identity":item.artifact_identity,
                    "nodes":[{"id":"lean:B","depth":1}]}
        with patch("theseus_repo_search.kusto_query.reach", side_effect=result):
            r = federated_reach(self.catalog, Mock(), "A")
        self.assertEqual(r["status"],"FOUND")
        self.assertEqual(r["searched_corpora"],2)
        self.assertEqual([x["source_id"] for x in r["results"]],["first","second"])
        self.assertEqual([x["artifact_identity"] for x in r["results"]],["a"*64,"c"*64])

    def test_unavailable_corpus_not_reported_as_no_signal(self):
        def result(item, *_args, **_kwargs):
            if item.source_id == "second":
                raise RepoSearchError("UNAVAILABLE_KUSTO_QUERY","unavailable")
            return {"status":"UNKNOWN","found":False,"complete_within_scope":False,"nodes":[]}
        with patch("theseus_repo_search.kusto_query.reach", side_effect=result):
            r=federated_reach(self.catalog, Mock(), "A")
        self.assertEqual(r["status"],"DEGRADED")
        self.assertEqual(r["searched_corpora"],1)
        self.assertEqual(r["unavailable"][0]["source_id"],"second")

    def test_all_absent_is_unknown(self):
        with patch("theseus_repo_search.kusto_query.reach", return_value={
                "status":"UNKNOWN","found":False,"complete_within_scope":False,"nodes":[]}):
            r=federated_reach(self.catalog, Mock(), "A")
        self.assertEqual(r["status"],"UNKNOWN")
        self.assertEqual(r["searched_corpora"],2)

    def test_truncation_is_degraded(self):
        with patch("theseus_repo_search.kusto_query.reach", return_value={
                "status":"DEGRADED","found":True,"complete_within_scope":False,"nodes":[]}):
            r=federated_reach(self.catalog, Mock(), "A",limit=1)
        self.assertEqual(r["status"],"DEGRADED")
        self.assertFalse(r["complete_within_scope"])

    def test_expansion_keeps_occurrences_and_rejects_revision_drift(self):
        from types import SimpleNamespace

        from theseus_repo_search.kusto_query import expand_search_hits
        hits = [
            SimpleNamespace(source_id=s, candidate_id=s+"::lean:A",
                            hit=SimpleNamespace(declaration_id="lean:A", source_revision="rev"))
            for s in ("first","second","first")
        ]
        def result(item, *_args, **_kwargs):
            return {"status":"FOUND","found":True,"complete_within_scope":True,
                    "source_revision":"rev" if item.source_id == "first" else "different","nodes":[]}
        with patch("theseus_repo_search.kusto_query.reach", side_effect=result) as call:
            r=expand_search_hits(self.catalog, Mock(), hits, depth=1, limit=10)
        self.assertEqual(call.call_count,2)
        self.assertEqual(r["graph_status"],"DEGRADED")
        self.assertEqual(r["graph_seeds"],2)
        self.assertEqual(r["graphs"][0]["source_id"],"first")
        self.assertEqual(r["graph_unavailable"][0]["source_id"],"second")

    def test_lexical_only_hit_is_explicit_not_a_graph(self):
        from types import SimpleNamespace

        from theseus_repo_search.kusto_query import expand_search_hits
        hit=SimpleNamespace(source_id="first",candidate_id="first::row",
                            hit=SimpleNamespace(declaration_id=None,source_revision="rev"))
        with patch("theseus_repo_search.kusto_query.reach") as call:
            r=expand_search_hits(self.catalog, Mock(), [hit])
        call.assert_not_called()
        self.assertEqual(r["graph_status"],"UNAVAILABLE")
        self.assertEqual(r["graph_unavailable"][0]["code"],"NO_DECLARATION_ID")
