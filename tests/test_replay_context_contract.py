import unittest

from theseus_repo_search import replay_contract


class ReplayContextContractTests(unittest.TestCase):
    def test_require_context_chunks_returns_list_of_object_rows(self):
        context = {"chunks": [{"declaration_hint": "target"}]}

        chunks = replay_contract.require_context_chunks(context)

        self.assertEqual(chunks, [{"declaration_hint": "target"}])

    def test_require_context_chunks_rejects_non_list_payload(self):
        with self.assertRaisesRegex(AssertionError, "context chunks"):
            replay_contract.require_context_chunks({"chunks": "not-a-list"})

    def test_require_context_chunks_rejects_non_object_row(self):
        with self.assertRaisesRegex(AssertionError, "context chunks"):
            replay_contract.require_context_chunks({"chunks": ["not-an-object"]})


if __name__ == "__main__":
    unittest.main()
