import unittest

from theseus_repo_search.sharding import (
    ModuleWeight,
    merge_raw_depgraph_shards,
    partition_modules,
)


class ShardPlanningTests(unittest.TestCase):
    def test_greedy_partition_is_deterministic_and_balances_weight(self):
        modules = [
            ModuleWeight("A", 9),
            ModuleWeight("B", 8),
            ModuleWeight("C", 7),
            ModuleWeight("D", 6),
            ModuleWeight("E", 5),
            ModuleWeight("F", 4),
        ]
        shards = partition_modules(modules, 3)
        self.assertEqual(
            [[item.module for item in shard.modules] for shard in shards],
            [["A", "F"], ["B", "E"], ["C", "D"]],
        )
        self.assertEqual([shard.total_bytes for shard in shards], [13, 13, 13])

    def test_partition_rejects_more_shards_than_modules(self):
        with self.assertRaisesRegex(ValueError, "shard_count"):
            partition_modules([ModuleWeight("A", 1)], 2)


class RawShardMergeTests(unittest.TestCase):
    def test_merge_deduplicates_identical_nodes_and_edges_deterministically(self):
        shard_a = {
            "nodes": [
                {"module": "A", "fullName": "A.x", "name": "x", "kind": "theorem"},
                {"module": "B", "fullName": "B.y", "name": "y", "kind": "theorem"},
            ],
            "edges": [{"kind": "value", "source": "A.x", "target": "B.y"}],
        }
        shard_b = {
            "nodes": [
                {"module": "B", "fullName": "B.y", "name": "y", "kind": "theorem"},
                {"module": "C", "fullName": "C.z", "name": "z", "kind": "theorem"},
            ],
            "edges": [
                {"kind": "value", "source": "A.x", "target": "B.y"},
                {"kind": "type", "source": "B.y", "target": "C.z"},
            ],
        }
        merged = merge_raw_depgraph_shards([shard_b, shard_a])
        self.assertEqual(
            [row["fullName"] for row in merged["nodes"]],
            ["A.x", "B.y", "C.z"],
        )
        self.assertEqual(
            merged["edges"],
            [
                {"kind": "value", "source": "A.x", "target": "B.y"},
                {"kind": "type", "source": "B.y", "target": "C.z"},
            ],
        )

    def test_merge_rejects_conflicting_duplicate_node(self):
        a = {
            "nodes": [
                {"module": "A", "fullName": "A.x", "name": "x", "kind": "theorem"}
            ],
            "edges": [],
        }
        b = {
            "nodes": [
                {"module": "A", "fullName": "A.x", "name": "x", "kind": "def"}
            ],
            "edges": [],
        }
        with self.assertRaisesRegex(ValueError, "conflicting duplicate node"):
            merge_raw_depgraph_shards([a, b])


if __name__ == "__main__":
    unittest.main()
