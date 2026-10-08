import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts.package_accepted_artifact import build_release_package
from tests.test_release_package import (
    DESCRIPTOR,
    _accepted_context,
    _write_replay,
    write_fixture,
)
from theseus_repo_search import multicorpus
from theseus_repo_search.accepted_catalog import (
    AcceptedCatalog,
    AcceptedCorpus,
    accepted_catalog_sha256,
)
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.model import EvidenceGrade
from theseus_repo_search.multicorpus import (
    LocalCorpusState,
    UnavailableCorpus,
    ensure_local_catalog,
    search_local_catalog,
)
from theseus_repo_search.projection import SQLITE_PROJECTION_SCHEMA_VERSION
from theseus_repo_search.retrieval import SearchHit


def _search_state(source_id: str, db_name: str) -> LocalCorpusState:
    return LocalCorpusState(
        source_id=source_id,
        release_tag=f"tag/{source_id}",
        artifact_identity="a" * 64,
        fingerprint_sha256="b" * 64,
        artifact_dir=Path(f"/{source_id}/artifact"),
        db_path=Path(f"/{db_name}.sqlite"),
        projection_fingerprint="c" * 64,
    )


def _search_hit(
    *,
    declaration_id: str | None,
    source_row_id: str | None,
    match_mode: str = "single_term",
    score: float = 0.0,
) -> SearchHit:
    return SearchHit(
        declaration_id=declaration_id,
        declaration_hint=None,
        source_kind="git",
        source_revision="abc123",
        source_authority=None,
        source_path="Pkg/Main.lean",
        source_start_line=1,
        source_end_line=1,
        evidence_grade=EvidenceGrade.LEXICAL_HIT,
        score=score,
        text="needle",
        created_from_authoritative_source=True,
        match_mode=match_mode,
        source_row_id=source_row_id,
    )


class MultiCorpusFederationTests(unittest.TestCase):
    def test_exact_hits_precede_lexical_hits_and_candidate_ids_are_stable(self):
        states = (
            _search_state("beta", "beta"),
            _search_state("alpha", "alpha"),
        )

        def fake_search(db_path, query, *, limit, mode):
            del query, limit, mode
            if db_path.name == "alpha.sqlite":
                return [
                    _search_hit(
                        declaration_id="lean:Pkg.Exact.target",
                        source_row_id="src:alpha",
                        match_mode="exact",
                        score=500.0,
                    )
                ]
            return [
                _search_hit(
                    declaration_id=None,
                    source_row_id="src:beta",
                    match_mode="single_term",
                    score=-999.0,
                )
            ]

        with mock.patch("theseus_repo_search.multicorpus.search", side_effect=fake_search):
            result = search_local_catalog(
                states,
                (),
                catalog_sha256="d" * 64,
                query="target",
                limit=20,
            )

        self.assertEqual(result.searched_corpora, 2)
        self.assertEqual(result.unavailable_corpora, ())
        self.assertEqual(
            [hit.candidate_id for hit in result.hits],
            ["alpha::lean:Pkg.Exact.target", "beta::src:beta"],
        )

    def test_lexical_order_uses_local_rank_then_source_and_never_cross_corpus_score(self):
        states = (
            _search_state("zeta", "zeta"),
            _search_state("alpha", "alpha"),
        )

        def fake_search(db_path, query, *, limit, mode):
            del query, limit, mode
            if db_path.name == "alpha.sqlite":
                return [
                    _search_hit(
                        declaration_id=None,
                        source_row_id="src:alpha:rank1",
                        score=1000.0,
                    )
                ]
            return [
                _search_hit(
                    declaration_id=None,
                    source_row_id="src:zeta:rank1",
                    score=-1000.0,
                ),
                _search_hit(
                    declaration_id=None,
                    source_row_id="src:zeta:rank2",
                    score=-999999.0,
                ),
            ]

        prior = (UnavailableCorpus("missing", "RELEASE_EVIDENCE_INVALID", "bad"),)
        with mock.patch("theseus_repo_search.multicorpus.search", side_effect=fake_search):
            result = search_local_catalog(
                states,
                prior,
                catalog_sha256="d" * 64,
                query="needle",
                limit=20,
            )

        self.assertEqual(result.searched_corpora, 2)
        self.assertEqual(result.unavailable_corpora, prior)
        self.assertEqual(
            [(hit.local_rank, hit.source_id, hit.candidate_id) for hit in result.hits],
            [
                (1, "alpha", "alpha::src:alpha:rank1"),
                (1, "zeta", "zeta::src:zeta:rank1"),
                (2, "zeta", "zeta::src:zeta:rank2"),
            ],
        )

    def test_missing_declaration_and_source_row_identity_is_blocked(self):
        state = _search_state("broken", "broken")
        bad_hit = _search_hit(declaration_id=None, source_row_id=None)
        with mock.patch(
            "theseus_repo_search.multicorpus.search", return_value=[bad_hit]
        ), self.assertRaises(RepoSearchError) as caught:
            search_local_catalog(
                (state,),
                (),
                catalog_sha256="d" * 64,
                query="needle",
            )
        self.assertEqual(caught.exception.code, "BLOCKED_PROJECTION_INTEGRITY")


class MultiCorpusCacheTests(unittest.TestCase):
    def _fixture(self, root: Path):
        artifact = write_fixture(root)
        replay = root / "replay.json"
        _write_replay(replay, artifact)
        repo, descriptor, runner, consumer, _ = _accepted_context(
            root, artifact, replay, descriptor_source=DESCRIPTOR
        )

        assets = root / "release-assets"
        assets.mkdir()
        archive = assets / "fixture-accepted.tar.gz"
        receipt = assets / "fixture-release-receipt.json"
        build_release_package(
            artifact=artifact,
            replay=replay,
            source_descriptor=descriptor,
            runner_config=runner,
            consumer_receipt=consumer,
            repository_root=repo,
            archive=archive,
            receipt=receipt,
        )
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        source_id = "openai-ten-proofs-multicolor"
        catalog = AcceptedCatalog(
            schema="theseus.repo-search.accepted-catalog.v1",
            release_repository="TeaShaman-cyber/theseus-repo-search-lab",
            corpora=(
                AcceptedCorpus(
                    source_id=source_id,
                    release_tag=(
                        f"accepted-artifact/{source_id}/"
                        f"{payload['artifact']['identity'][:16]}"
                    ),
                    package_asset=archive.name,
                    receipt_asset=receipt.name,
                    source_descriptor="producer/sources/source.json",
                    artifact_identity=payload["artifact"]["identity"],
                    fingerprint_sha256=payload["fingerprint"]["sha256"],
                ),
            ),
        )

        fake_gh = root / "fake-gh"
        fake_gh.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "dest=\n"
            "asset=\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    -D) dest=\"$2\"; shift 2 ;;\n"
            "    -p) asset=\"$2\"; shift 2 ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "test -n \"$dest\"\n"
            "test -n \"$asset\"\n"
            "mkdir -p \"$dest\"\n"
            "cp \"$FAKE_GH_ASSETS/$asset\" \"$dest/$asset\"\n"
            "printf 'download %s\\n' \"$asset\" >> \"$FAKE_GH_COUNT\"\n",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)
        count = root / "gh-count.txt"
        return catalog, repo, descriptor, runner, assets, fake_gh, count

    def _ensure(self, catalog, repo, assets, fake_gh, count, cache):
        with mock.patch.dict(
            os.environ,
            {
                "FAKE_GH_ASSETS": str(assets),
                "FAKE_GH_COUNT": str(count),
            },
            clear=False,
        ):
            return ensure_local_catalog(
                catalog,
                repository_root=repo,
                cache_root=cache,
                gh_executable=str(fake_gh),
            )

    def test_cold_materialization_then_warm_reuse_without_download(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog, repo, _descriptor, _runner, assets, fake_gh, count = self._fixture(root)
            cache = root / "cache"

            first, unavailable = self._ensure(
                catalog, repo, assets, fake_gh, count, cache
            )
            self.assertEqual(unavailable, ())
            self.assertEqual(len(first), 1)
            self.assertTrue(first[0].db_path.is_file())
            self.assertTrue((first[0].artifact_dir / "manifest.json").is_file())
            self.assertEqual(len(count.read_text().splitlines()), 2)

            second, unavailable2 = self._ensure(
                catalog, repo, assets, fake_gh, count, cache
            )
            self.assertEqual(unavailable2, ())
            self.assertEqual(second, first)
            self.assertEqual(len(count.read_text().splitlines()), 2)

    def test_projection_version_mismatch_forces_rebuild(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog, repo, _descriptor, _runner, assets, fake_gh, count = self._fixture(root)
            cache = root / "cache"
            states, unavailable = self._ensure(
                catalog, repo, assets, fake_gh, count, cache
            )
            self.assertEqual(unavailable, ())
            verified_path = states[0].db_path.parent / "verified.json"
            verified = json.loads(verified_path.read_text(encoding="utf-8"))
            verified["sqlite_projection_schema_version"] = "repo-search-sqlite-v0"
            verified_path.write_text(json.dumps(verified), encoding="utf-8")

            rebuilt, unavailable2 = self._ensure(
                catalog, repo, assets, fake_gh, count, cache
            )

            self.assertEqual(unavailable2, ())
            self.assertEqual(len(rebuilt), 1)
            self.assertEqual(len(count.read_text().splitlines()), 4)
            repaired = json.loads(
                (rebuilt[0].db_path.parent / "verified.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                repaired["sqlite_projection_schema_version"],
                SQLITE_PROJECTION_SCHEMA_VERSION,
            )

    def test_current_producer_input_drift_is_not_warm_reused(self):
        for which in ("descriptor", "runner"):
            with self.subTest(which=which), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                catalog, repo, descriptor, runner, assets, fake_gh, count = self._fixture(root)
                cache = root / "cache"
                states, unavailable = self._ensure(
                    catalog, repo, assets, fake_gh, count, cache
                )
                self.assertEqual(unavailable, ())
                self.assertEqual(len(states), 1)

                target = descriptor if which == "descriptor" else runner
                target.write_text(
                    target.read_text(encoding="utf-8") + "\n",
                    encoding="utf-8",
                )

                states2, unavailable2 = self._ensure(
                    catalog, repo, assets, fake_gh, count, cache
                )

                self.assertEqual(states2, ())
                self.assertEqual(len(unavailable2), 1)
                self.assertEqual(unavailable2[0].source_id, catalog.corpora[0].source_id)
                self.assertEqual(unavailable2[0].code, "REBUILD_REQUIRED")
                self.assertEqual(len(count.read_text().splitlines()), 4)

    def test_repair_lock_releases_when_holder_closes(self):
        with tempfile.TemporaryDirectory() as d:
            final = Path(d) / "artifact-final"
            waiter_started = threading.Event()
            waiter_acquired = threading.Event()

            def waiter():
                waiter_started.set()
                with multicorpus._repair_lock(final):
                    waiter_acquired.set()

            with multicorpus._repair_lock(final):
                thread = threading.Thread(target=waiter)
                thread.start()
                self.assertTrue(waiter_started.wait(1.0))
                self.assertFalse(waiter_acquired.wait(0.1))

            self.assertTrue(waiter_acquired.wait(2.0))
            thread.join(2.0)
            self.assertFalse(thread.is_alive())

    def test_known_good_final_is_reused_not_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog, repo, _descriptor, _runner, assets, fake_gh, count = self._fixture(root)
            cache = root / "cache"
            states, unavailable = self._ensure(
                catalog, repo, assets, fake_gh, count, cache
            )
            self.assertEqual(unavailable, ())
            final = states[0].db_path.parent
            stage = final.with_name(f".{final.name}.staged-extra")
            shutil.copytree(final, stage)
            corpus = catalog.corpora[0]
            digest = accepted_catalog_sha256(catalog)
            real_rename = os.rename

            with mock.patch(
                "theseus_repo_search.multicorpus.os.rename", wraps=real_rename
            ) as rename:
                reused = multicorpus._publish_staged_cache(
                    stage,
                    final,
                    corpus=corpus,
                    repository_root=repo,
                    catalog_sha256=digest,
                )

            quarantines = [
                call
                for call in rename.call_args_list
                if Path(call.args[0]) == final
                and Path(call.args[1]).name.startswith(f".{final.name}.invalid-")
            ]
            self.assertEqual(quarantines, [])
            self.assertFalse(stage.exists())
            self.assertEqual(reused.db_path, final / "index.sqlite")

    def test_two_repairers_quarantine_invalid_final_only_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog, repo, _descriptor, _runner, assets, fake_gh, count = self._fixture(root)
            cache = root / "cache"
            states, unavailable = self._ensure(
                catalog, repo, assets, fake_gh, count, cache
            )
            self.assertEqual(unavailable, ())
            final = states[0].db_path.parent
            corpus = catalog.corpora[0]
            digest = accepted_catalog_sha256(catalog)
            stage1 = final.with_name(f".{final.name}.staged-one")
            stage2 = final.with_name(f".{final.name}.staged-two")
            shutil.copytree(final, stage1)
            shutil.copytree(final, stage2)

            verified_path = final / "verified.json"
            verified = json.loads(verified_path.read_text(encoding="utf-8"))
            verified["sqlite_projection_schema_version"] = "repo-search-sqlite-v0"
            verified_path.write_text(json.dumps(verified), encoding="utf-8")

            barrier = threading.Barrier(3)
            results = []
            errors = []

            def repair(stage):
                try:
                    barrier.wait()
                    results.append(
                        multicorpus._publish_staged_cache(
                            stage,
                            final,
                            corpus=corpus,
                            repository_root=repo,
                            catalog_sha256=digest,
                        )
                    )
                except (OSError, ValueError, RepoSearchError) as exc:
                    errors.append(exc)

            real_rename = os.rename
            with mock.patch(
                "theseus_repo_search.multicorpus.os.rename", wraps=real_rename
            ) as rename:
                first = threading.Thread(target=repair, args=(stage1,))
                second = threading.Thread(target=repair, args=(stage2,))
                first.start()
                second.start()
                barrier.wait()
                first.join(5.0)
                second.join(5.0)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            quarantines = [
                call
                for call in rename.call_args_list
                if Path(call.args[0]) == final
                and Path(call.args[1]).name.startswith(f".{final.name}.invalid-")
            ]
            self.assertEqual(len(quarantines), 1)
            self.assertFalse(stage1.exists())
            self.assertFalse(stage2.exists())
            self.assertEqual(
                list(final.parent.glob(f".{final.name}.invalid-*")), []
            )
            repaired = json.loads((final / "verified.json").read_text(encoding="utf-8"))
            self.assertEqual(
                repaired["sqlite_projection_schema_version"],
                SQLITE_PROJECTION_SCHEMA_VERSION,
            )



if __name__ == "__main__":
    unittest.main()
