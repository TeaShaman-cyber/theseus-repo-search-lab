# Archive Source Provenance V2 Implementation Plan

> **For agentic workers:** execute task-by-task. Every task uses RED -> GREEN -> canonical QA and must leave existing Git v1 production/consumption runnable. Do not enable archive workflow rows before the downstream v2 path they require is already verified.

**Goal:** Add honest end-to-end provenance for immutable archive-backed Lean sources from verified materialization through raw-dependency receipt, normalized artifact, projection, registered replay, and consumer receipt, while preserving existing Git-backed v1 bytes and behavior.

**Architecture:** Existing `theseus.repo-index.v1` remains Git-shaped and byte-compatible. Archive-backed authoritative artifacts use additive `theseus.repo-index.v2` with explicit structured archive authority and generic `source_revision`. Both schemas normalize into one internal source-authority model. Acquisition differs by source kind; build/extraction, artifact verification, projection, replay, and consumer acceptance share source-neutral mechanics after authority-aware boundaries.

**Tech Stack:** Python 3.11+, stdlib `dataclasses/json/pathlib/hashlib/tempfile/zipfile`, Lean/Lake, pinned LeanDepViz, SQLite FTS5, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-06-archive-source-provenance-v2-design.md`

**Issue:** #68

## Baseline already present on PR #69

Do not reimplement these acquisition pieces:

- strict `LeanArchiveSource` descriptor and generic source loader;
- exact HTTPS archive URL + SHA-256 validation;
- safe ZIP extraction with traversal/symlink/special-member/type-marker rejection;
- path-disjoint materialization destination/receipt;
- invocation-unique receipt staging + atomic no-clobber final receipt publication;
- exact #67 Zenodo descriptor;
- existing Git source path unchanged;
- canonical QA green on the acquisition/spec head.

The remaining work begins at the provenance boundary after verified materialization.

## Global constraints

- `theseus.repo-index.v1` serialized bytes and historical `artifact_identity()` results MUST remain unchanged.
- Archive digests MUST never be serialized or projected under `source_commit`.
- No source-kind guessing from string shape. Dispatch only from explicit schema/discriminator.
- Internal generic revision is an equality/join key, not full authority; full authority remains structured.
- Existing Git descriptors/artifacts/replays are not migrated merely for vocabulary cleanup.
- No bulk artifact migration; SQLite projections are disposable and rebuilt.
- No Zenodo-specific branch after archive acquisition; publisher-specific theorem logic is forbidden.
- No Isabelle support, source registry, mirror authority, combined corpus DB, ranking changes, or heavy-Python debt cleanup in this plan.
- Every promised RED must fail for the intended reason before production code changes.
- Use existing QA first: focused unittest, touched Ruff where applicable, `./tools/dev/check`, then existing hosted producer/consumer checks.
- Heavy-Python remains advisory under #54/#56/#57/#74; only a PR-introduced diagnostic is a blocker.

---

## Task 1: Introduce the internal source-authority model without changing v1 bytes

**Files:**
- Modify: `src/theseus_repo_search/model.py`
- Modify: `src/theseus_repo_search/artifact.py`
- Modify: `tests/test_artifact.py`
- Add tests in the smallest existing model/artifact test surface; create `tests/test_model.py` only if needed.

**Produces:**
- explicit internal `GitAuthority` and `ArchiveAuthority` (exact class names may vary);
- one `SourceAuthority` union/dispatch boundary;
- generic internal `source_revision` / `created_from_authoritative_source` meaning;
- compatibility access for v1 callers without emitting archive data under Git terminology.

- [x] Add regression asserting a known v1 manifest serialization and `artifact_identity()` are unchanged.
- [x] RED: parse an explicit archive authority object into the internal authority model; fail because no archive authority model exists yet.
- [x] Add the minimal authority classes/helpers and v1 adapters.
- [x] Keep `ArtifactManifest.to_dict()` v1 behavior byte-for-byte unchanged.
- [x] GREEN focused artifact/model tests.
- [x] Run `./tools/dev/check` and `git diff --check`.

**Checkpoint:** existing v1 artifact production and all existing replay/consumer paths still run unchanged; no v2 writer exists yet.

---

## Task 2: Add dual-schema artifact loading and v2 archive serialization

**Files:**
- Modify: `src/theseus_repo_search/model.py`
- Modify: `src/theseus_repo_search/artifact.py`
- Modify: `tests/test_artifact.py`

**Produces:**
- loader dispatch for `theseus.repo-index.v1` and `theseus.repo-index.v2`;
- archive v2 manifest source object: `{kind,url,sha256,format,subdir}`;
- v2 node/source records using `source_revision`, never `source_commit`;
- v2 `created_from_authoritative_source`;
- schema-aware artifact identity binding full structured authority + producer + scope + members + authority state.

- [x] RED: load a minimal v2 archive fixture and require structured archive authority + `source_revision`.
- [x] RED: reject v2 archive rows containing `source_commit` instead of `source_revision`.
- [x] RED: changing URL, SHA-256, format, or subdir changes identity or fails validation as appropriate.
- [x] Implement schema-aware decode/encode while leaving v1 serializer/identity path untouched.
- [x] Add fail-closed cross-schema tests: v1 shape under v2 schema and v2 archive source under v1 schema must reject.
- [x] GREEN focused artifact tests.
- [x] Run `./tools/dev/check` and `git diff --check`.

**Checkpoint:** v2 archive artifacts can be written/loaded synthetically; existing CLI/workflow still produces only v1 Git artifacts.

---

## Task 3: Persist and verify the authoritative archive member manifest

**Files:**
- Modify: `scripts/materialize_archive_source.py`
- Modify: `tests/test_archive_source.py`
- Modify as required: `src/theseus_repo_search/producer_config.py`

**Produces:**
- deterministic member-level manifest of normalized archive member path + SHA-256;
- digest of that manifest in the materialization receipt;
- exact source-root-relative mapping needed by downstream extraction/normalization.

- [ ] RED: successful materialization receipt lacks a persisted member manifest / manifest digest.
- [ ] RED: tamper an extracted authoritative member and require verification failure against persisted member hash.
- [ ] Implement deterministic manifest persistence inside the same logical materialization publication contract.
- [ ] Verify cleanup/rollback still preserves no-clobber semantics from PR #69.
- [ ] GREEN full `tests.test_archive_source`.
- [ ] Run touched Ruff, `./tools/dev/check`, and `git diff --check`.

**Checkpoint:** verified materialization exposes a stable authoritative file set; no raw-depgraph v3 exists yet.

---

## Task 4: Add archive raw-dependency receipt v3 and pre/post extraction revalidation

**Files:**
- Modify: `scripts/producer_guard.py`
- Modify: `src/theseus_repo_search/cli.py`
- Modify: `tests/test_producer_guard.py`
- Modify: `tests/test_cli.py`

**Produces:**
- `theseus.raw-depgraph-receipt.v3` for archive authority;
- structured archive source identity;
- materialization tree/member-manifest evidence;
- pre-extraction and post-extraction authoritative-member verification;
- existing Git receipt v2 unchanged.

- [ ] RED: archive extraction cannot produce/validate a structured raw receipt without fake repo/commit fields.
- [ ] RED: mutate, remove, or type-change an authoritative member before extraction and require fail-closed.
- [ ] RED: mutate an authoritative member during extraction and require post-extraction failure before receipt publication.
- [ ] Implement source-kind-aware producer guard/receipt validation.
- [ ] Preserve Git v2 receipt behavior and tests exactly.
- [ ] GREEN focused producer/CLI tests.
- [ ] Run `./tools/dev/check` and `git diff --check`.

**Checkpoint:** raw graph evidence is honestly archive-bound, but archive artifact construction is not yet enabled in workflow.

---

## Task 5: Normalize archive sources only from manifest-backed bytes and reject unbacked nodes

**Files:**
- Modify: `src/theseus_repo_search/sources.py`
- Modify: `src/theseus_repo_search/normalize.py` only if the source-neutral boundary requires it
- Modify: `src/theseus_repo_search/cli.py`
- Modify: `tests/test_sources.py`
- Modify: `tests/test_cli.py`
- Modify/add narrow normalization tests only where required.

**Produces:**
- archive source enumeration from persisted member-manifest paths only;
- hash-at-consumption check for the exact bytes parsed/serialized;
- generic `source_revision` for v2 rows;
- every serialized in-scope node/module binds to exactly one manifest-backed authoritative source path/chunk;
- dependent edges to generated/unmanifested nodes fail closed.

- [ ] RED: generated `.lean` outside the member manifest is visible to the current recursive non-Git scanner.
- [ ] RED: change an authoritative `.lean` after raw-receipt publication but before source normalization; require mismatch failure.
- [ ] RED: inject an in-scope raw node/module whose source is not manifest-backed; require artifact construction failure.
- [ ] Add an explicit manifest-backed archive scan path; do not weaken Git `tracked_only` semantics.
- [ ] Require exact node-to-authoritative-source binding before v2 archive serialization.
- [ ] GREEN focused source/CLI tests.
- [ ] Run `./tools/dev/check` and `git diff --check`.

**Checkpoint:** one local synthetic archive can produce a fully validated v2 artifact; projection/replay/consumer still intentionally reject/ignore v2 until migrated.

---

## Task 6: Make projection and retrieval schema-aware without fake `source_commit`

**Files:**
- Modify: `src/theseus_repo_search/projection.py`
- Modify: `src/theseus_repo_search/retrieval.py`
- Modify if needed: `src/theseus_repo_search/graph.py`
- Modify: `tests/test_projection.py`
- Modify: `tests/test_retrieval.py`
- Modify graph tests only if generic authority state reaches graph results.

**Produces for v2 archive projections:**
- `artifact_schema`;
- `source_kind`;
- `source_revision`;
- canonical `source_authority_json`;
- `created_from_authoritative_source`;
- `artifact_identity`.

- [ ] RED: build projection from v2 archive artifact and assert no archive digest is stored/exposed in any `source_commit` field/column/view.
- [ ] RED: retrieval result from v2 exposes generic revision + structured authority, not Git terminology.
- [ ] Implement schema-aware projection layout/reader boundary; keep v1 DB layout and readers working.
- [ ] Do not migrate historical projection DB files.
- [ ] GREEN projection/retrieval tests for both schemas.
- [ ] Run `./tools/dev/check` and `git diff --check`.

**Checkpoint:** artifact-only query path works for both v1 Git and v2 archive artifacts.

---

## Task 7: Make registered replay and consumer receipts source-kind aware

**Files:**
- Modify: `src/theseus_repo_search/replay_contract.py`
- Modify: registered replay scripts only for generic provenance output where needed
- Modify: `scripts/write_consumer_receipt.py`
- Modify: `tests/test_consumer_receipt.py`
- Modify: registered replay tests.

**Produces:**
- descriptor dispatch through `LeanSource = LeanGitSource | LeanArchiveSource`;
- structured authority equality checks per source kind;
- exact replay `artifact_identity` equality retained from #70/#71/#73;
- archive consumer receipt with structured archive authority and no `source_commit` lie;
- v1 Git consumer receipt compatibility retained.

- [ ] RED: archive artifact validated against Git descriptor rejects.
- [ ] RED: Git artifact validated against archive descriptor rejects.
- [ ] RED: archive replay with mismatched URL/SHA/subdir/member evidence rejects.
- [ ] RED: archive consumer receipt cannot be emitted with Git-shaped provenance fields.
- [ ] Implement source-kind-aware replay validation and receipt serialization.
- [ ] Re-run all registered replay tests, including PrimeGaps artifact-identity coverage from #73.
- [ ] Run `./tools/dev/check` and `git diff --check`.

**Checkpoint:** all downstream consumers required by the workflow understand v2 archive authority. Only now may the archive workflow row be enabled.

---

## Task 8: Wire archive acquisition into the generic hosted producer

**Files:**
- Modify: `.github/workflows/lean-source-producer-smoke.yml`
- Modify: `scripts/load_producer_env.py` only if the current generic mapping is insufficient
- Modify: `tests/test_workflow_structure.py`
- Modify: `tests/test_load_producer_env.py`
- Use existing: `producer/sources/decreasing-diagrams-complete.json`

**Rules:**
- branch only at acquisition/readback (`git checkout` vs verified archive materialization);
- after a verified source root exists, share source-owned toolchain observation, Lake build/cache, LeanDepViz extraction, artifact build, projection, replay, and consumer flow;
- no Zenodo special-case;
- archive workflow row stays disabled until Tasks 1-7 are merged/verified in the same branch state.

- [ ] RED structural test requiring explicit source-kind acquisition dispatch and forbidding archive SHA in Git commit env/arguments.
- [ ] Add archive row for #67 exact descriptor.
- [ ] Preserve all existing Git matrix rows unchanged.
- [ ] Run `./tools/dev/check` and workflow-structure tests.
- [ ] Push exact head and inspect hosted checks; do not infer hosted success from local QA.

**Checkpoint:** hosted producer can attempt the archive corpus through the generic path.

---

## Task 9: Exact #67 hosted producer + fresh artifact-only consumer acceptance

**Authority input:**
- DOI/versioned record: `10.5281/zenodo.23160921`
- file: `decreasing-diagrams-lean.zip`
- SHA-256: `4601cfef943144d27e4cd0daef5a2b8308f23dddec8cc6f997274585211e7c0f`

**Acceptance:**
- [ ] hosted producer succeeds on exact PR head and records exact source identity;
- [ ] produced v2 manifest contains explicit archive authority and no fabricated Git fields;
- [ ] authority/raw receipts bind archive digest + member manifest + pre/post checks;
- [ ] fresh consumer downloads only the produced artifact, rebuilds projection, runs registered replay, and emits consumer receipt;
- [ ] consumer replay identity == projection identity == freshly validated artifact identity;
- [ ] consumer receipt reports structured archive authority;
- [ ] existing Git matrix producer/consumer rows remain green;
- [ ] exact remote/head/check readback recorded on #68/#69 or successor implementation PR.

If the formal corpus cannot complete for a source-owned/toolchain reason, record the exact bounded blocker; do not weaken provenance to make the smoke green.

---

## Task 10: Terminal disposition and handoff to #67

- [ ] Run final `./tools/dev/check` and `git diff --check` on exact implementation head.
- [ ] Separate any known advisory heavy-Python baseline from PR-introduced diagnostics per #74.
- [ ] Confirm no P0/P1/blocker remains in current scope.
- [ ] Confirm v1 identity regression fixture still matches the pre-v2 value.
- [ ] Record exact artifact identity + hosted producer/consumer receipts on #68.
- [ ] Close #68 only after the generic archive provenance path is verified end-to-end.
- [ ] Return the accepted artifact to #67 for the research-value smoke; archive infrastructure success does not imply useful mathematical signal.

## Explicit non-step

Do **not** migrate Git artifacts to v2 in this plan. If later evidence shows value, open/reuse a separate issue for Git-v2 convergence rather than expanding #68.
