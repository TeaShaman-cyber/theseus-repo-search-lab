import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.run_research_smoke import _search_probe, load_scenario, run_scenario
from tests.test_replay_flt_regular import build_fixture as build_flt_fixture
from theseus_repo_search.producer_config import load_lean_git_source


class ResearchSmokeTests(unittest.TestCase):
    def test_discovery_candidates_are_not_promoted_but_evidence_hits_are(self):
        hit = SimpleNamespace(
            declaration_id=None, declaration_hint="candidate", source_path="Pkg/Main.lean",
            source_start_line=1, source_end_line=1, evidence_grade=SimpleNamespace(value="LEXICAL_HIT"),
            score=1.0, query_mode="discovery", match_mode="any_terms",
        )
        with patch("scripts.run_research_smoke.search", return_value=[hit]) as mocked:
            discovery = _search_probe(
                Path("unused.sqlite"),
                {"kind": "search", "query": "truth predicate", "query_mode": "discovery"},
            )
        mocked.assert_called_once_with(
            Path("unused.sqlite"), "truth predicate", limit=10, mode="discovery"
        )
        self.assertEqual(discovery["state"], "CANDIDATE_SIGNAL")

        hit.query_mode = "evidence"
        hit.match_mode = "all_terms"
        with patch("scripts.run_research_smoke.search", return_value=[hit]) as mocked:
            evidence = _search_probe(
                Path("unused.sqlite"),
                {"kind": "search", "query": "truth predicate", "query_mode": "evidence"},
            )
        mocked.assert_called_once_with(
            Path("unused.sqlite"), "truth predicate", limit=10, mode="evidence"
        )
        self.assertEqual(evidence["state"], "FOUND_USEFUL_STRUCTURE")

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

    def test_scenario_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "scenario.json"
            path.write_text(
                '{"schema":"theseus.repo-search-research-smoke.v1",'
                '"scenario_id":"a","scenario_id":"b"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
                load_scenario(path)

    def test_scenario_rejects_nonstandard_json_constants(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "scenario.json"
            path.write_text(
                '{"schema":"theseus.repo-search-research-smoke.v1",'
                '"scenario_id":"a","version":NaN}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-standard JSON constant"):
                load_scenario(path)

    def test_receipt_binds_exact_scenario_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            artifact, db = build_flt_fixture(root)
            scenario_path = root / "scenario.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema": "theseus.repo-search-research-smoke.v1",
                        "scenario_id": "hash-bound-v0",
                        "version": 0,
                        "research_refs": ["example#hash"],
                        "source_repo": "leanprover-community/flt-regular",
                        "evidence_classes": ["HASH_BOUND"],
                        "probes": [
                            {
                                "id": "boundary",
                                "kind": "declared_boundary",
                                "basis": "fixture",
                            }
                        ],
                    },
                    sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
            receipt = run_scenario(
                db=db,
                artifact=artifact,
                scenario=load_scenario(scenario_path),
                tool_commit="e" * 40,
            )
            self.assertEqual(
                receipt["scenario"]["sha256"],
                sha256(scenario_path.read_bytes()).hexdigest(),
            )

    def test_rejects_unknown_scenario_schema(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "scenario.json"
            path.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "scenario schema"):
                load_scenario(path)

    def test_marton_seam_is_persisted_in_nonblocking_qa_and_owned_by_math(self):
        scenario = load_scenario(Path("qa/research-smoke/annals-marton-seam-v0.json"))
        source = load_lean_git_source(Path("producer/sources/annals-challenge-marton.json"))
        self.assertEqual(scenario["scenario_id"], "annals-marton-seam-v0")
        self.assertEqual(scenario["source_repo"], source.source_repo)
        self.assertEqual(source.source_commit, "e32eb1411db0d700ca874dd695aea92f78699db8")
        self.assertIn(
            "TeaShaman-cyber/theseus-math-research-lab#56",
            scenario["research_refs"],
        )
        self.assertIn("FORMAL_STATEMENT_NOT_PROOF", scenario["evidence_classes"])
        self.assertTrue(scenario["probes"])
        self.assertTrue(all(not p["regression_guard"] for p in scenario["probes"]))
        queries = [p for p in scenario["probes"] if p["kind"] == "search"]
        self.assertGreaterEqual(len(queries), 2)
        self.assertTrue(all("required_declaration_hints" not in p for p in queries))
        for probe in queries:
            with self.subTest(probe=probe["id"]), patch(
                "scripts.run_research_smoke.search", return_value=[]
            ):
                result = _search_probe(Path("unavailable.sqlite"), probe)
            self.assertEqual(result["state"], "NO_SIGNAL")
            self.assertFalse(result["regression"])

    def test_repository_scenarios_load_and_cover_two_research_families(self):
        paths = sorted(Path("qa/research-smoke").glob("*.json"))
        scenarios = [load_scenario(path) for path in paths]
        self.assertGreaterEqual(len(scenarios), 2)
        repos = {scenario["source_repo"] for scenario in scenarios}
        self.assertIn("anthropics/formal-math", repos)
        self.assertIn("leanprover-community/flt-regular", repos)
