import unittest

from theseus_repo_search.overlap import Occurrence, overlap_groups, suppression_reason


def occ(corpus, name, kind="thm", parent=None, path=None):
    return Occurrence(corpus, corpus+"-artifact", "lean:"+name, kind, parent, path)


class OverlapTests(unittest.TestCase):
    def test_generated_theorems_filtered_before_limit(self):
        rows = [
            occ("a", "A.mk.injEq", parent="ctor"), occ("b", "B.mk.injEq", parent="ctor"),
            occ("a", "A.mono"), occ("b", "B.mono"),
            occ("a", "A.rec", "rec"), occ("b", "B.rec", "rec"),
        ]
        result = overlap_groups(rows, limit=1)
        self.assertEqual(result["raw_shared_names"], 3)
        self.assertEqual(result["shared_names"], 1)
        self.assertEqual(result["groups"][0]["name"], "mono")
        self.assertEqual(result["suppressed"], {"generated_lean_theorem": 2, "non_theorem": 2})
        self.assertEqual(overlap_groups(rows, raw=True)["shared_names"], 3)

    def test_same_name_not_enough_to_suppress(self):
        for row in (occ("a", "A.inj"), occ("a", "A.inj", parent="def"),
                    occ("a", "A.inj", parent="ctor", path="Math.lean"),
                    occ("a", "A.mono", parent="inductive")):
            self.assertIsNone(suppression_reason(row))

    def test_same_corpus_duplicates_not_cross_corpus(self):
        self.assertEqual(overlap_groups([occ("a","A.mono"),occ("a","B.mono")])["shared_names"],0)

    def test_occurrence_limit_preserves_total_and_marks_truncation(self):
        result = overlap_groups([occ("a","A.mono"),occ("b","B.mono"),occ("b","C.mono")], occurrence_limit=1)
        group = result["groups"][0]
        self.assertEqual(group["occurrence_count"],3)
        self.assertEqual(group["corpora"],["a","b"])
        self.assertTrue(group["truncated"])
        self.assertEqual(len(group["occurrences"]),1)

    def test_uncorroborated_generated_name_is_flagged_not_removed(self):
        rows = [occ("a", "A.congr_simp"), occ("b", "B.congr_simp")]
        result = overlap_groups(rows)
        self.assertEqual(result["shared_names"], 1)
        self.assertTrue(result["groups"][0]["possible_generated_name"])
