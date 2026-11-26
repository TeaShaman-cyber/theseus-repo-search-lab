import unittest
from pathlib import Path

from theseus_repo_search.producer_config import load_lean_git_source

DESCRIPTOR = Path("producer/pilots/riemann-hypothesis-lemmata.json")


class RiemannPilotDescriptorTests(unittest.TestCase):
    def test_source_excludes_use_actual_src_relative_directories(self):
        source = load_lean_git_source(DESCRIPTOR)
        self.assertEqual(
            source.exclude_source_prefixes,
            (
                "src/CompanionTheory/",
                "src/Development/",
                "src/Formalism/",
                "src/GroundTheory/",
                "src/PositivityTheory/",
                "src/ReciprocityTheory/",
                "src/Supplements/",
            ),
        )


if __name__ == "__main__":
    unittest.main()
