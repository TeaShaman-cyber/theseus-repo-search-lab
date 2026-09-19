import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_research_smoke import load_scenario, run_scenario
from tests.test_replay_flt_regular import build_fixture as build_flt_fixture


class ResearchSmokeTests(unittest.TestCase):
    def test_flt_positive_capability_and_boundary_emit_machine_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_flt_fixture(root)
            scenario_path = root / "scenario.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema": "theseus.repo-search-research-smoke.v1",
                        "scenario_id": "flt-live-v0",
                        "version": 0,
                        "research_refs": ["TeaShaman-cyber/theseus-math-research-lab#18"],
                        "source_repo": "leanprover-community/flt-regular",
                        "evidence_classes": ["NON_RH_PROVENANCE_BRIDGE"],
                        "probes": [
                            {
                                "id": "flt-search",
                                "kind": "search",
                                "query": "Fermat last theorem regular primes",
                                "limit": 10,
                                "min_hits": 1,
                                "required_declaration_hints": ["flt_regular"],
                                "regression_guard": True,
                            },
                            {
                                "id": "flt-bridge",
                                "kind": "deps",
                                "declaration": "flt_regular",
                                "depth": 1,
                                "min_edges": 2,
                                "required_ids": [
                                    "lean:FltRegular.caseI",
                                    "lean:FltRegular.caseII",
                                ],
                                "regression_guard": True,
                            },
                            {
                                "id": "external-boundary",
                                "kind": "declared_boundary",
                                "basis": "fixture intentionally covers only flt-regular",
                                "regression_guard": False,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            scenario = load_scenario(scenario_path)
            receipt = run_scenario(
                db=db,
                artifact=artifact,
                scenario=scenario,
                tool_commit="a" * 40,
            )
            self.assertEqual(
                receipt["schema"], "theseus.repo-search-research-smoke-receipt.v1"
            )
            self.assertEqual(receipt["scenario"]["id"], "flt-live-v0")
            self.assertEqual(receipt["overall_disposition"], "FOUND_USEFUL_STRUCTURE")
            self.assertFalse(receipt["regression_detected"])
            self.assertIn("FOUND_USEFUL_STRUCTURE", receipt["observed_states"])
            self.assertIn("CORPUS_BOUNDARY", receipt["observed_states"])
            self.assertEqual(receipt["artifact"]["source_repo"], "leanprover-community/flt-regular")
            self.assertEqual(receipt["scientific_authority"], "NONE")

    def test_search_guard_requires_named_declaration_even_when_other_hits_exist(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_flt_fixture(root)
            scenario_path = root / "scenario.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema": "theseus.repo-search-research-smoke.v1",
                        "scenario_id": "search-guard-regression-v0",
                        "version": 0,
                        "research_refs": ["example#search-guard"],
                        "source_repo": "leanprover-community/flt-regular",
                        "evidence_classes": ["REGRESSION_FIXTURE"],
                        "probes": [
                            {
                                "id": "guarded-search",
                                "kind": "search",
                                "query": "Fermat last theorem regular primes",
                                "limit": 10,
                                "min_hits": 1,
                                "required_declaration_hints": ["missing_declaration"],
                                "regression_guard": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            receipt = run_scenario(
                db=db,
                artifact=artifact,
                scenario=load_scenario(scenario_path),
                tool_commit="d" * 40,
            )
            probe = receipt["probes"][0]
            self.assertGreaterEqual(probe["hit_count"], 1)
            self.assertEqual(
                probe["missing_required_declaration_hints"], ["missing_declaration"]
            )
            self.assertTrue(probe["regression"])
            self.assertTrue(receipt["regression_detected"])
            self.assertEqual(receipt["overall_disposition"], "DEGRADED")

    def test_missing_guarded_capability_is_degraded_but_receipt_still_emits(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_flt_fixture(root)
            scenario_path = root / "scenario.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema": "theseus.repo-search-research-smoke.v1",
                        "scenario_id": "guard-regression-v0",
                        "version": 0,
                        "research_refs": ["example#1"],
                        "source_repo": "leanprover-community/flt-regular",
                        "evidence_classes": ["REGRESSION_FIXTURE"],
                        "probes": [
                            {
                                "id": "missing-edge",
                                "kind": "deps",
                                "declaration": "flt_regular",
                                "depth": 1,
                                "required_ids": ["lean:Missing.bridge"],
                                "regression_guard": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            receipt = run_scenario(
                db=db,
                artifact=artifact,
                scenario=load_scenario(scenario_path),
                tool_commit="b" * 40,
            )
            self.assertTrue(receipt["regression_detected"])
            self.assertEqual(receipt["overall_disposition"], "DEGRADED")
            self.assertEqual(receipt["probes"][0]["state"], "FOUND_USEFUL_STRUCTURE")
            self.assertEqual(receipt["probes"][0]["regression"], True)

    def test_no_signal_and_declared_boundary_are_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_flt_fixture(root)
            scenario_path = root / "scenario.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema": "theseus.repo-search-research-smoke.v1",
                        "scenario_id": "states-v0",
                        "version": 0,
                        "research_refs": ["example#2"],
                        "source_repo": "leanprover-community/flt-regular",
                        "evidence_classes": ["STATE_DISTINCTION"],
                        "probes": [
                            {
                                "id": "no-signal",
                                "kind": "search",
                                "query": "xylophone quasar unobtainium",
                                "limit": 5,
                                "regression_guard": False,
                            },
                            {
                                "id": "boundary",
                                "kind": "declared_boundary",
                                "basis": "external corpus not represented by this artifact",
                                "regression_guard": False,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            receipt = run_scenario(
                db=db,
                artifact=artifact,
                scenario=load_scenario(scenario_path),
                tool_commit="c" * 40,
            )
            self.assertIn("NO_SIGNAL", receipt["observed_states"])
            self.assertIn("CORPUS_BOUNDARY", receipt["observed_states"])
            self.assertEqual(receipt["overall_disposition"], "CORPUS_BOUNDARY")

    def test_rejects_unknown_scenario_schema(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "scenario.json"
            path.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "scenario schema"):
                load_scenario(path)

    def test_repository_scenarios_load_and_cover_two_research_families(self):
        paths = sorted(Path("qa/research-smoke").glob("*.json"))
        scenarios = [load_scenario(path) for path in paths]
        self.assertGreaterEqual(len(scenarios), 2)
        repos = {scenario["source_repo"] for scenario in scenarios}
        self.assertIn("anthropics/formal-math", repos)
        self.assertIn("leanprover-community/flt-regular", repos)
