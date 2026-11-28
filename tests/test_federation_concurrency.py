import threading
import unittest
from unittest.mock import Mock, patch

from theseus_repo_search.accepted_catalog import AcceptedCatalog, AcceptedCorpus
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.kusto_query import federated_reach
from theseus_repo_search.turso_query import search_turso_catalog


class ConcurrentFederationTests(unittest.TestCase):
    def setUp(self):
        self.items = tuple(AcceptedCorpus(str(i), "tag", "asset", "receipt",
                           "descriptor", "a"*64, "b"*64) for i in range(6))
        self.catalog = AcceptedCatalog("theseus.repo-search.accepted-catalog.v1", "owner/repo", self.items)

    def exercise(self, run, target, result):
        barrier = threading.Barrier(2, timeout=3)
        lock = threading.Lock()
        active = peak = 0
        def work(item, *_args, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait()
                if item.source_id in ("1", "3"):
                    raise RepoSearchError("UNAVAILABLE_TEST", "fault")
                return result
            finally:
                with lock:
                    active -= 1
        with patch(target, side_effect=work):
            output = run()
        self.assertEqual(peak, 2)
        return output

    def test_lexical_parallel_bound_and_ordered_partial_coverage(self):
        r = self.exercise(lambda: search_turso_catalog(self.catalog, Mock(), query="x", workers=2),
                          "theseus_repo_search.turso_query._search_one", [])
        self.assertEqual(r.searched_corpora, 4)
        self.assertEqual([x.source_id for x in r.unavailable_corpora], ["1", "3"])

    def test_graph_parallel_bound_and_ordered_partial_coverage(self):
        r = self.exercise(lambda: federated_reach(self.catalog, Mock(), "x", workers=2),
                          "theseus_repo_search.kusto_query.reach",
                          {"status":"UNKNOWN", "found":False, "complete_within_scope":False, "nodes":[]})
        self.assertEqual(r["status"], "DEGRADED")
        self.assertEqual([x["source_id"] for x in r["results"]], ["0","2","4","5"])
        self.assertEqual([x["source_id"] for x in r["unavailable"]], ["1","3"])

    def test_invalid_worker_bounds_fail_before_io(self):
        for workers in (0, 9, True):
            with self.subTest(workers=workers), patch("theseus_repo_search.turso_query._search_one") as search:
                with self.assertRaises(RepoSearchError):
                    search_turso_catalog(self.catalog, Mock(), query="x", workers=workers)
                search.assert_not_called()

    def test_graph_seed_expansion_parallel_preserves_occurrences(self):
        from types import SimpleNamespace

        from theseus_repo_search.kusto_query import expand_search_hits
        hits = [SimpleNamespace(source_id=s, candidate_id=s+"::lean:A",
                    hit=SimpleNamespace(declaration_id="lean:A", source_revision="rev"))
                for s in ("0","1","0","2","3","4","5")]
        r = self.exercise(lambda: expand_search_hits(self.catalog, Mock(), hits, workers=2),
                          "theseus_repo_search.kusto_query.reach",
                          {"status":"FOUND", "found":True, "source_revision":"rev",
                           "complete_within_scope":True, "nodes":[]})
        self.assertEqual(r["graph_seeds"], 6)
        self.assertEqual(r["graph_status"], "DEGRADED")
        self.assertEqual([x["source_id"] for x in r["graphs"]], ["0","2","4","5"])
        self.assertEqual([x["source_id"] for x in r["graph_unavailable"]], ["1","3"])

    def test_completion_order_does_not_change_global_top_k(self):
        from types import SimpleNamespace

        from theseus_repo_search.multicorpus import federate_search
        first = threading.Event()
        exact = SimpleNamespace(declaration_id="lean:A", match_mode="exact")
        other = SimpleNamespace(declaration_id="lean:B", match_mode="fts")
        def slow():
            self.assertTrue(first.wait(3))
            return [exact, other]
        def fast():
            first.set()
            return [other, exact]
        queries = (("a", slow), ("b", fast))
        parallel = federate_search(queries, (), catalog_sha256="x", limit=3, workers=2)
        serial = federate_search((("a", lambda: [exact, other]), ("b", lambda: [other, exact])),
                                 (), catalog_sha256="x", limit=3)
        self.assertEqual(parallel, serial)
        self.assertEqual([x.candidate_id for x in parallel.hits],
                         ["a::lean:A", "b::lean:A", "b::lean:B"])

    def test_unexpected_programming_error_is_not_no_signal(self):
        with patch("theseus_repo_search.turso_query._search_one", side_effect=RuntimeError("bug")), self.assertRaises(RuntimeError):
            search_turso_catalog(self.catalog, Mock(), query="x", workers=2)
