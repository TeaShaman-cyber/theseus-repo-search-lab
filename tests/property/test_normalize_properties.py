import unittest
from copy import deepcopy

from hypothesis import given, settings
from hypothesis import strategies as st

from theseus_repo_search.normalize import normalize_leandepviz

PROPERTY_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    derandomize=True,
    database=None,
)

IDENT = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=8,
).filter(lambda value: value[0].isalpha())


@st.composite
def valid_graphs(draw):
    names = draw(st.lists(IDENT, min_size=1, max_size=8, unique=True))
    nodes = []
    for index, name in enumerate(names):
        module = f"Root.M{index % 3}"
        nodes.append(
            {
                "module": module,
                "fullName": f"{module}.{name}",
                "name": name,
                "kind": "thm",
            }
        )
    possible_edges = [
        (source, target, kind)
        for source in range(len(nodes))
        for target in range(len(nodes))
        for kind in ("type", "value")
    ]
    chosen = draw(
        st.lists(
            st.sampled_from(possible_edges),
            min_size=1,
            max_size=min(20, len(possible_edges)),
        )
    )
    edges = [
        {
            "source": nodes[source]["fullName"],
            "target": nodes[target]["fullName"],
            "kind": kind,
        }
        for source, target, kind in chosen
    ]
    return {"nodes": nodes, "edges": edges}


class NormalizePropertyTests(unittest.TestCase):
    @PROPERTY_SETTINGS
    @given(valid_graphs(), st.randoms())
    def test_permutation_invariance(self, raw, random):
        expected = normalize_leandepviz(
            deepcopy(raw),
            source_commit="abc123",
            root_modules=("Root",),
            producer_ref="LeanDepViz@property",
        )
        shuffled = deepcopy(raw)
        random.shuffle(shuffled["nodes"])
        random.shuffle(shuffled["edges"])
        observed = normalize_leandepviz(
            shuffled,
            source_commit="abc123",
            root_modules=("Root",),
            producer_ref="LeanDepViz@property",
        )
        self.assertEqual(observed, expected)

    @PROPERTY_SETTINGS
    @given(valid_graphs())
    def test_duplicate_edge_idempotence(self, raw):
        expected = normalize_leandepviz(
            deepcopy(raw),
            source_commit="abc123",
            root_modules=("Root",),
            producer_ref="LeanDepViz@property",
        )
        duplicated = deepcopy(raw)
        duplicated["edges"].append(deepcopy(duplicated["edges"][0]))
        observed = normalize_leandepviz(
            duplicated,
            source_commit="abc123",
            root_modules=("Root",),
            producer_ref="LeanDepViz@property",
        )
        self.assertEqual(observed, expected)

    @PROPERTY_SETTINGS
    @given(root=IDENT, suffix=IDENT)
    def test_scope_dot_boundary_law(self, root, suffix):
        modules = [root, f"{root}.{suffix}", f"{root}{suffix}"]
        raw = {
            "nodes": [
                {
                    "module": module,
                    "fullName": f"{module}.n{index}",
                    "name": f"n{index}",
                    "kind": "thm",
                }
                for index, module in enumerate(modules)
            ],
            "edges": [],
        }
        nodes, edges = normalize_leandepviz(
            raw,
            source_commit="abc123",
            root_modules=(root,),
            producer_ref="LeanDepViz@property",
        )
        self.assertEqual(edges, [])
        kept_modules = {node.module for node in nodes}
        self.assertIn(root, kept_modules)
        self.assertIn(f"{root}.{suffix}", kept_modules)
        self.assertNotIn(f"{root}{suffix}", kept_modules)


if __name__ == "__main__":
    unittest.main()
