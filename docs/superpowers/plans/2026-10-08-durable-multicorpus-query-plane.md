# Durable Multi-Corpus Query Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all currently accepted Repository Search corpora queryable through one recoverable local runtime and one durable Neon generation, with exact accepted-release provenance and no dependence on one MarcoPolo shell lifetime.

**Architecture:** A strict repository-owned accepted-corpus catalog binds the eight immutable GitHub Releases. The existing SQLite/FTS5 consumer remains the rebuildable local/reference path; a separate Neon Postgres schema stores immutable generation-scoped search rows and an atomically selected active generation for thin read-only query surfaces. Release authority never moves into Neon, and the first implementation uses PostgreSQL built-in full-text search rather than adding Lakebase/vector extensions.

**Tech Stack:** Python 3.11+ stdlib; existing SQLite FTS5 consumer; GitHub CLI for immutable-release acquisition; PostgreSQL 17 built-in `tsvector`/GIN/functions on Neon; existing MarcoPolo PostgreSQL connector and native Neon connector for live canaries; unittest; GitHub Actions only after a Neon CI credential route is independently verified.

**Spec:** `docs/superpowers/specs/2026-10-08-multicorpus-durable-query-plane-design.md`

## Global Constraints

- Accepted immutable GitHub Releases + the versioned accepted-corpus catalog remain authority; SQLite and Neon are projections only.
- Catalog schema is `theseus.repo-search.accepted-catalog.v1`; its canonical digest is SHA-256 over normalized canonical JSON with corpora sorted by `source_id`.
- Current accepted set contains exactly eight entries and binds exact release tag, package asset, receipt asset, source descriptor path, artifact identity, and accepted fingerprint for each corpus.
- Runtime acceptance is SEARCH-ONLY. No theorem proving, proof certification, theorem-level axiom gate, embeddings, vector retrieval, AI Gateway, or Object Storage is introduced here.
- Existing Git v1 and archive v2 artifact/projection bytes and identities are not migrated merely to support the multi-corpus runtime.
- Local SQLite projections remain independently rebuildable from accepted releases and do not become durable authority.
- Neon projection schema version starts at `repo-search-query-v1`; any behavior-affecting tokenizer/index/query-function change requires a schema-version change and therefore a new deterministic generation-content identity.
- A READY Neon generation is immutable. New corpus rows are written only to a candidate generation.
- Active-generation publication is compare-and-switch: a concurrent/stale activator must not overwrite a newer active generation.
- Query results expose `catalog_sha256`, `generation_content_sha256`, `generation_id` (Neon) or local catalog digest, `source_id`, exact artifact/source provenance, declaration/source identity, evidence/match mode, and explicit unavailable-corpus information.
- Raw relevance scores from separate SQLite corpora are never compared globally. Local federation orders exact hits first, then by 1-based corpus-local rank, then `source_id`, then stable candidate ID.
- Broken/stale derived state must not prevent repair from accepted releases. Inspection failure of an old projection is a rebuild signal, not a reason to trust or preserve it.
- No GitHub Actions Neon write is added until an independent CI credential route is VERIFIED. Current ability to list repository secret names is BLOCKED by GitHub integration 403, so CI credential availability is UNKNOWN.

## Review Focus

1. **Catalog/release substitution:** a tag, asset, fingerprint, source descriptor, or artifact identity differing from the catalog must fail before cache reuse or Neon generation verification. Covered in Tasks 1, 2, and 4.
2. **Derived-state drift:** corrupted/unreadable SQLite or Neon derived state must remain repairable from accepted releases; verification errors cannot trap rebuild behind an equivalence check against bad state. Covered in Tasks 2 and 4.
3. **Concurrent generation activation:** two candidate builders must not let an older/stale candidate overwrite the generation activated by the winner. Covered in Tasks 3 and 4.
4. **Behavioral search-schema drift:** tokenizer/index/function-definition changes must change `projection_schema_version`/generation identity rather than silently preserving VERIFIED status. Covered in Task 3.
5. **Partial corpus availability:** one failed corpus must never disappear silently; local search reports searched/unavailable counts, while Neon activation rejects an incomplete generation. Covered in Tasks 2, 4, and 5.

---

## File Structure

```text
producer/
    accepted-corpora.json                exact accepted-release catalog
src/theseus_repo_search/
    accepted_catalog.py                  strict catalog model/load/digest
    multicorpus.py                       local cache recovery + federated SQLite search
    cli.py                               `multicorpus-search` command
scripts/
    build_neon_generation_sql.py         verified artifacts -> candidate-generation SQL
sql/neon/
    001_query_plane_v1.sql               schema, indexes, read/mutation functions, activation contract
    002_query_plane_privileges.sql        explicit PUBLIC revoke + reader/writer capability roles
    queries/
        search.sql                       connector-friendly bounded read query
        generation_status.sql            active/catalog/generation readback
qa/query-plane/
    marton-zeta-seam-v1.json             bounded real canary queries/expected provenance class
tests/
    test_accepted_catalog.py
    test_multicorpus.py
    test_neon_generation_export.py
    test_neon_sql_contract.py
    test_workflow_structure.py            only later CI-plan changes; no Neon secret assumption here
```

No production Postgres driver is added to the Python package in this plan. Neon writes are emitted as deterministic SQL and executed by the already-governed PostgreSQL workbench path during the live canary. Thin query routes call stable SQL functions/views.

---

### Task 1: Add the strict accepted-corpus catalog

**Files:**
- Create: `producer/accepted-corpora.json`
- Create: `src/theseus_repo_search/accepted_catalog.py`
- Create: `tests/test_accepted_catalog.py`

**Interfaces:**
- Produces `AcceptedCorpus`, `AcceptedCatalog`, `load_accepted_catalog(path: Path) -> AcceptedCatalog`, and `accepted_catalog_sha256(catalog: AcceptedCatalog) -> str`.
- Later tasks consume only these typed entries; they do not scrape the GitHub Releases list to decide what is accepted.

Catalog top-level shape:

```json
{
  "schema": "theseus.repo-search.accepted-catalog.v1",
  "release_repository": "TeaShaman-cyber/theseus-repo-search-lab",
  "corpora": []
}
```

Each corpus entry contains exactly:

```text
source_id
release_tag
package_asset
receipt_asset
source_descriptor
artifact_identity
fingerprint_sha256
```

Populate the eight current entries from the already published receipts. At minimum the exact identity bindings are:

```text
annals-challenge-marton                 1d0d8ce2fbeb279fc9024856a94f788a2ed015dca5aeaee94786040b89d8f17c
zeta23                                  786b5ab339dd85b7817642d6f4921747e5e0cdcdd3ffed2dd9d02bd79277ae2c
openai-ten-proofs-multicolor            3f2d8edd1077cc4db1d2f991c6d51b0b1259840a83542bf2eb49612335cc9690
openai-long-gaps                        9c062327474441f85bbb3cbbf6a7b9cc5ec4d79af45af9ac524ce27c93272b5d
openai-cdc-lean                          154e254c84a94b262f8df022e5e81bea4ee544677830a7de6e1d44b2ea15d978
leanprover-community-con-nf              e6bf117b57945fd451c432e0ddb6d569356e6c9c8e7b8dcb598607a9c3e639a6
leanprover-community-flt-regular         41bfa1d236ee59a856a93288d997b6cf19b7f42f2c7dd630fb2ce2482f9b3844
decreasing-diagrams-complete             abd7c1b4e2586c8c7641a459f07a6c0026682796a6e4222fe7bc0a0307d5a0bb
```

- [ ] **Step 1: Write catalog RED tests**

Add tests that require: exact schema; exact field sets; valid 64-lowercase-hex identities/fingerprints; repository-relative descriptor paths under `producer/sources/`; unique `source_id`, release tag, package asset within a tag, and artifact identity; eight current source IDs; deterministic digest independent of input object-key order. Also reject unknown keys and line-breaking transport fields.

- [ ] **Step 2: Run the targeted tests and observe the intended import/behavior failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_accepted_catalog -v
```

Expected: RED because `accepted_catalog` does not exist yet.

- [ ] **Step 3: Add the smallest importable model/loader skeleton**

Required signatures:

```python
@dataclass(frozen=True)
class AcceptedCorpus:
    source_id: str
    release_tag: str
    package_asset: str
    receipt_asset: str
    source_descriptor: str
    artifact_identity: str
    fingerprint_sha256: str

@dataclass(frozen=True)
class AcceptedCatalog:
    schema: str
    release_repository: str
    corpora: tuple[AcceptedCorpus, ...]

def load_accepted_catalog(path: Path) -> AcceptedCatalog: ...
def accepted_catalog_sha256(catalog: AcceptedCatalog) -> str: ...
```

- [ ] **Step 4: Add malformed/duplicate RED cases and run them before validation code**

Expected: the new cases fail for missing strict validation, not import failure.

- [ ] **Step 5: Implement strict validation and the exact eight-entry catalog**

The canonical digest serializes normalized data with sorted object keys and corpora sorted by `source_id`; the on-disk array should also be sorted for reviewability.

- [ ] **Step 6: Verify targeted and canonical QA**

```bash
PYTHONPATH=src python3 -m unittest tests.test_accepted_catalog -v
./tools/dev/check
git diff --check
```

Expected: PASS / `DEV_CHECK_PASS`.

- [ ] **Step 7: Commit**

```bash
git add producer/accepted-corpora.json src/theseus_repo_search/accepted_catalog.py tests/test_accepted_catalog.py
git commit -m "feat: add accepted corpus catalog"
```

---

### Task 2: Add automatic local recovery and federated SQLite search

**Files:**
- Create: `src/theseus_repo_search/multicorpus.py`
- Modify: `src/theseus_repo_search/projection.py`
- Modify: `src/theseus_repo_search/retrieval.py`
- Modify: `src/theseus_repo_search/cli.py`
- Modify: `tests/test_projection.py`
- Modify: `tests/test_retrieval.py`
- Create: `tests/test_multicorpus.py`
- Modify: `README.md`

**Interfaces:**
- Consumes Task 1 `AcceptedCatalog` / `AcceptedCorpus`.
- Reuses `scripts.consume_accepted_release.verify_and_extract_release`, `projection.build_projection`, `projection.projection_fingerprint`, and `retrieval.search`.
- Extends `retrieval.SearchHit` with `source_row_id: str | None`, populated from the already-selected `sources.id` for source-backed hits. This is projection identity, not the accepted-corpus `source_id`.
- Produces:

```python
@dataclass(frozen=True)
class LocalCorpusState:
    source_id: str
    release_tag: str
    artifact_identity: str
    fingerprint_sha256: str
    artifact_dir: Path
    db_path: Path
    projection_fingerprint: str

@dataclass(frozen=True)
class UnavailableCorpus:
    source_id: str
    code: str
    message: str

@dataclass(frozen=True)
class MultiCorpusHit:
    candidate_id: str
    source_id: str
    local_rank: int
    hit: SearchHit

@dataclass(frozen=True)
class MultiCorpusResult:
    catalog_sha256: str
    searched_corpora: int
    unavailable_corpora: tuple[UnavailableCorpus, ...]
    hits: tuple[MultiCorpusHit, ...]

def ensure_local_catalog(
    catalog: AcceptedCatalog,
    *,
    repository_root: Path,
    cache_root: Path,
    gh_executable: str = "gh",
) -> tuple[tuple[LocalCorpusState, ...], tuple[UnavailableCorpus, ...]]: ...

def search_local_catalog(
    states: tuple[LocalCorpusState, ...],
    unavailable_corpora: tuple[UnavailableCorpus, ...],
    *,
    catalog_sha256: str,
    query: str,
    limit: int = 20,
    mode: SearchMode = "discovery",
) -> MultiCorpusResult: ...
```

Cache identity:

```text
<cache_root>/<catalog_sha256>/<sqlite_projection_schema_version>/<source_id>/<artifact_identity>/
    release/
    index.sqlite
    verified.json
```

`projection.py` exposes a repository-owned `SQLITE_PROJECTION_SCHEMA_VERSION` (initial value `repo-search-sqlite-v1`) covering all behavior-affecting SQLite projection choices: table/index shape, FTS tokenizer/configuration, projection metadata contract, and retrieval assumptions that require rebuild. `verified.json` binds that current version together with catalog digest, source ID, release tag, package/receipt SHA observed by the release verifier, artifact identity, accepted fingerprint, projection fingerprint, `source_descriptor_sha256`, and `runner_config_sha256`. Warm reuse must require exact equality with the **current code's** `SQLITE_PROJECTION_SCHEMA_VERSION` and recompute the current descriptor and `producer/runner.json` digests before serving the cache; this preserves both projection-contract and producer-drift gates. Cache reuse also requires successful projection provenance/fingerprint checks. Any producer-input drift, unreadable projection, or derived-state mismatch discards/rebuilds the cache from accepted authority rather than comparing bad state as authority.

- [ ] **Step 1: Write RED tests with a fake `gh` executable and tiny accepted-release fixtures**

Cover cold materialization, warm reuse without a second download, catalog-digest cache separation, **SQLite projection-version mismatch forcing rebuild**, **source-descriptor mutation and runner-config mutation forcing `REBUILD_REQUIRED`/rebuild before reuse**, artifact/fingerprint mismatch, unreadable SQLite rebuild, and one unavailable corpus alongside one searchable corpus. Add repair regressions for (a) invalid final replaced by a verified staged candidate, (b) two concurrent repairers serialized by the sibling SQLite coordination lock so only one publishes and the loser re-verifies/reuses the winner, (c) releasing/closing the lock holder permits a later repairer to acquire the coordination transaction, and (d) a known-good final never being quarantined or overwritten. Add `tests/test_projection.py` coverage that pins the version constant and requires an intentional version bump whenever the persisted SQLite search contract changes.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_multicorpus -v
```

Expected: RED because multicorpus runtime does not exist.

- [ ] **Step 3: Implement cold/warm cache materialization and atomic repair only**

Use invocation-unique staging directories under the target cache filesystem. A staged candidate must pass release verification, artifact load, SQLite `quick_check`, producer-input checks, projection-schema-version checks, and projection fingerprint computation **before** it can replace anything.

For a missing final path, publish with same-filesystem `os.rename(staged, final)`. If another publisher wins, verify the winner and reuse it.

For an existing final path that fails verification, serialize repair through a sibling coordination SQLite file (separate from `index.sqlite`) held with `BEGIN IMMEDIATE` for the repair transaction. The lock database is disposable coordination state, not authority; process exit/crash releases its OS file lock, so no persistent owner token can strand recovery. After acquiring the lock, **re-verify final** because another repair may already have completed. If final is now valid, discard staged and reuse final. If it is still invalid:

1. rename invalid `final` to an invocation-unique sibling quarantine path on the same filesystem;
2. atomically rename the already-verified `staged` directory to `final`;
3. verify/read back the published `final` again before returning it;
4. delete the quarantined invalid cache only after the new final passes readback.

A crash after quarantine but before publish leaves authority untouched and no cache trusted; the coordination transaction is released automatically, so the next invocation may acquire repair serialization and publish a freshly verified staged candidate. Never overwrite or remove a final cache that re-verifies as good under the repair lock.

- [ ] **Step 4: Run the cache tests GREEN before adding federation**

- [ ] **Step 5: Add federation RED tests**

Before federation, add retrieval regression coverage proving lexical hits expose the selected `sources.id` as `SearchHit.source_row_id`, including a chunk whose `declaration_id` is `None`. Build `candidate_id` as `<accepted_corpus_source_id>::<declaration_id>` when `declaration_id` exists, otherwise `<accepted_corpus_source_id>::<source_row_id>`; absence of both is `BLOCKED_PROJECTION_INTEGRITY`, never a synthesized path/line identity. Require exact hits before lexical hits; otherwise sort by `local_rank`, `source_id`, `candidate_id`; never compare separate SQLite `score` values. Require `searched_corpora` and explicit `unavailable_corpora` in all results.

- [ ] **Step 6: Implement `search_local_catalog` and CLI `multicorpus-search`**

CLI:

```text
python3 -m theseus_repo_search multicorpus-search \
  --catalog producer/accepted-corpora.json \
  --cache-dir <path> \
  --query <text> \
  --limit 20 \
  --mode discovery
```

Output is one JSON object with `status`, `catalog_sha256`, searched/unavailable counts, unavailable reasons, and provenance-rich hits.

- [ ] **Step 7: Run targeted QA, then a real cold/warm eight-release local canary in the actual workbench**

Record wall time, cache bytes, catalog digest, eight searched/unavailable counts, and a repeated warm query. Do not call #117 accepted if a second execution/runtime recovery has not yet been exercised.

- [ ] **Step 8: Run canonical QA and commit**

```bash
./tools/dev/check
git diff --check
git add src/theseus_repo_search/multicorpus.py src/theseus_repo_search/projection.py src/theseus_repo_search/retrieval.py src/theseus_repo_search/cli.py tests/test_projection.py tests/test_retrieval.py tests/test_multicorpus.py README.md
git commit -m "feat: add recoverable multicorpus search"
```

---

### Task 3: Define Neon query-plane schema and atomic generation contract

**Files:**
- Create: `sql/neon/001_query_plane_v1.sql`
- Create: `sql/neon/002_query_plane_privileges.sql`
- Create: `sql/neon/queries/search.sql`
- Create: `sql/neon/queries/generation_status.sql`
- Create: `tests/test_neon_sql_contract.py`

**Interfaces:**
- Consumes Task 1 catalog digest and stable source/candidate identities.
- Produces schema `repo_search` with immutable generation-scoped rows and read-only query functions.

Required objects:

```text
repo_search.generations
repo_search.corpora
repo_search.search_docs
repo_search.active_generation
repo_search.search(query_text text, result_limit integer, query_mode text)
repo_search.search_generation(generation_id text, query_text text, result_limit integer, query_mode text)
repo_search.search_generation_v1(generation_id text, query_text text, result_limit integer, query_mode text)
repo_search.generation_status()
repo_search.mark_generation_ready(generation_id text, expected_catalog_sha256 text, expected_corpus_count integer)
repo_search.activate_generation(generation_id text, expected_previous_generation_id text)
repo_search.gc_inactive_generation(generation_id text, expected_generation_content_sha256 text)
repo_search_reader NOLOGIN
repo_search_materializer NOLOGIN
```

Required generation states: `BUILDING`, `READY`. Failed BUILDING candidates may be deleted by maintenance. Database-level guards reject INSERT/UPDATE and row-level DELETE that would mutate any READY generation or its `corpora`/`search_docs` rows; the only content-state mutation is the guarded one-way `BUILDING -> READY` transition. The sole READY deletion exception is `gc_inactive_generation(...)`, which locks the active pointer and target generation, requires exact expected content identity, refuses the active generation or any externally referenced generation, and deletes the inactive generation plus its owned corpus/search rows as one transaction. The normal `search(...)` function reads only the active READY generation.

`search_docs` uses PostgreSQL built-in `to_tsvector('simple', ...)` and a GIN index. No Neon search extension is enabled in v1. Query semantics are versioned with the generation, not replaced globally in place. `search_generation_v1(...)` is the immutable v1 ranking/tokenization implementation for `projection_schema_version = 'repo-search-query-v1'`. Stable wrapper `search_generation(...)` first resolves the requested READY generation, reads its exact projection schema version, and dispatches only to the matching version-specific implementation; unknown versions fail closed. Stable wrapper `search(...)` resolves the active READY generation and delegates through the same version dispatcher. A future v2 adds `search_generation_v2(...)` alongside v1 and changes dispatch, but does not redefine v1 while any READY-v1 generation may still be queried. Exact declaration/candidate identity matches are returned before lexical matches. `query_mode=discovery` uses OR semantics for normalized terms; `evidence` requires all normalized terms. Ordering is deterministic with stable candidate ID tie-break.

Privilege boundary is explicit and fail-closed. `002_query_plane_privileges.sql` creates/reuses NOLOGIN capability roles `repo_search_reader` and `repo_search_materializer`, executes `REVOKE ALL ON ALL FUNCTIONS IN SCHEMA repo_search FROM PUBLIC`, and configures the object-creating schema owner with `ALTER DEFAULT PRIVILEGES IN SCHEMA repo_search REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC` so a later `search_generation_v2(...)` is not accidentally public. Direct table/sequence privileges remain revoked from `repo_search_reader`. `repo_search_materializer` receives only the generation-scoped table privileges required by Task 4's deterministic candidate INSERT/maintenance path (`generations`, `corpora`, `search_docs`), with no privilege to bypass the READY guards; `active_generation` remains mutation-only through guarded functions.

Reader-facing `search(...)`, `search_generation(...)`, and `generation_status()` are narrow `SECURITY DEFINER` functions owned by the schema/object owner, with a fixed safe `SET search_path = pg_catalog, repo_search, pg_temp`, no dynamic SQL, bounded inputs, and read-only bodies. That owner context permits the stable wrappers to call private version-specific helpers and read projection tables without granting those helpers/tables to the reader. Governed mutators are likewise callable only through guarded `SECURITY DEFINER` functions with the same fixed search-path discipline; the materializer capability gets only the guarded function execution plus the narrow generation-scoped table writes required to load/repair BUILDING candidates. `repo_search_reader` gets `USAGE` on schema `repo_search` plus `EXECUTE` only on the three stable read wrappers/status; version-specific helpers such as `search_generation_v1(...)` remain private. Any concrete login/connector principal receives capability-role membership only after that principal is independently identified and authorized; the schema migration must not infer a GitHub Actions or connector credential.

- [ ] **Step 1: Write structural, CAS, immutability, and candidate-query RED tests before adding SQL**

Tests require exact schema version marker `repo-search-query-v1`, no `CREATE EXTENSION`, all required objects, a generated/stored `tsvector` using `simple`, a GIN index, active-generation foreign-key/state guards, explicit result-limit validation, explicit compare-and-switch parameters, rejection of a stale expected previous generation, database-level rejection of mutations to READY generation/corpus/search rows, guarded whole-generation GC that rejects active/wrong-identity/referenced targets, schema-version metadata, provenance fields, a version-specific `search_generation_v1(...)`, and stable wrappers that dispatch by the selected generation's stored `projection_schema_version`. Unknown query-schema versions must fail closed. Privilege RED tests must require current and default function `EXECUTE` revocation from `PUBLIC`, NOLOGIN reader/materializer capability roles, no direct table/sequence privileges for the reader, reader `EXECUTE` only on stable read wrappers/status, no reader execute on version-specific helpers or mutators, explicit materializer grants limited to generation-scoped BUILDING writes plus guarded mutation/read functions, and `SECURITY DEFINER` + fixed `search_path` ending in `pg_temp` on every granted wrapper/mutator. Tests must reject dynamic SQL in those definer functions and prove the version dispatcher can invoke a private version-specific helper without granting that helper to the reader. The contract tests must also require child-write guards to lock the parent generation row before checking state and `mark_generation_ready(...)` to take a conflicting parent-row lock before completeness/state transition.

- [ ] **Step 2: Run RED and verify the failures are for the missing contract**

```bash
python3 -m unittest tests.test_neon_sql_contract -v
```

Expected: RED for missing schema/functions/guards; test discovery itself must succeed.

- [ ] **Step 3: Implement the minimal SQL contract**

Both `generation_id` and deterministic `generation_content_sha256` are supplied by the materializer and constrained as lowercase 64-hex. `generation_content_sha256` binds catalog digest + projection schema; `generation_id` additionally binds an invocation-unique nonce so a corrupt instance can be replaced without changing accepted content identity. `activate_generation` must lock the singleton active row and compare the caller's expected previous generation before switching. A stale caller raises and leaves the active row unchanged.

READY freeze is serialized against child-row writes, not merely checked sequentially. Before any INSERT/UPDATE/DELETE against `corpora` or `search_docs`, the guard trigger locks the parent `generations` row with `SELECT ... FOR SHARE`, then rechecks that the generation is still `BUILDING`. `mark_generation_ready(...)` locks that same parent row with `SELECT ... FOR UPDATE`, rechecks state/catalog/completeness while holding the lock, and only then performs the one-way `BUILDING -> READY` transition. Thus a child writer that acquires the lock first must commit before READY can proceed, while a writer arriving after READY waits and then fails its state recheck. Database guards continue to reject mutation of READY generation-scoped rows.

`gc_inactive_generation(...)` is the only READY deletion path: it locks/reads the active pointer and target, verifies `expected_generation_content_sha256`, refuses active or referenced targets, then deletes the generation and its owned rows transactionally; direct child-row deletion remains blocked. Apply `002_query_plane_privileges.sql` only after the function set exists; it must revoke current/default `PUBLIC` execution before granting the narrow reader/materializer capability surfaces. The migration must run under the same schema/object owner used to create repo_search functions so `ALTER DEFAULT PRIVILEGES` covers future versioned helpers; every future migration also explicitly revokes `PUBLIC` on newly created functions before exposure.

- [ ] **Step 4: Run the same targeted contract tests GREEN before live execution**

```bash
python3 -m unittest tests.test_neon_sql_contract -v
```

Expected: PASS with CAS, READY immutability, generation-scoped canary behavior, and version-dispatched query semantics covered before the live branch probe. Add a migration regression fixture that installs a synthetic v2 implementation/dispatcher alongside v1 and proves an active READY-v1 generation still returns the exact v1 ordering/match behavior until a READY-v2 generation is explicitly activated.

- [ ] **Step 5: Execute the schema on a temporary Neon child branch and run live SQL behavior canaries**

Through the native Neon development connector, create one temporary branch from current `main`, apply the schema, then verify:

1. incomplete BUILDING generation cannot activate;
2. READY generation can activate from expected previous state;
3. second stale activator fails and does not overwrite the winner;
4. after completeness verification, `mark_generation_ready(...)` freezes the candidate before any acceptance search;
5. `search_generation(...)` rejects BUILDING state and dispatches the now-immutable inactive READY candidate to the implementation matching its stored projection version; production `search(...)` uses the same dispatcher;
6. a live migration probe proves installing a synthetic/new query implementation does not change results for the still-active READY-v1 generation before a matching new-version generation is activated;
7. production `search(...)` reads only active READY rows;
8. UPDATE/late INSERT/direct child-row DELETE against READY generation rows is rejected at the database layer;
9. a two-session concurrency canary proves the freeze boundary: when a child writer holds the parent generation `FOR SHARE`, READY waits until that write commits; when `mark_generation_ready(...)` holds `FOR UPDATE` or has committed READY, a waiting child writer resumes only to fail the BUILDING-state recheck, leaving the READY rows unchanged;
10. `gc_inactive_generation(...)` refuses the active generation, refuses a wrong expected content identity, and removes a disposable inactive READY generation with all owned rows atomically;
11. a temporary login principal granted only `repo_search_reader` can execute `search(...)`, `search_generation(...)`, and `generation_status()` through the definer wrappers, including version dispatch to private `search_generation_v1(...)`, but receives permission denied for direct table reads/writes, direct `search_generation_v1(...)`, `mark_generation_ready(...)`, `activate_generation(...)`, and `gc_inactive_generation(...)`; remove the temporary principal/member grant after the branch test;
12. create a temporary future helper under the same schema owner and verify default privileges do not grant `PUBLIC EXECUTE`, then remove it;
13. schema/index/function/owner/ACL/search-path definitions can be read back exactly.

Delete the temporary branch after the canary unless it is retained deliberately for the next task. Record branch ID and exact schema readback in #117.

- [ ] **Step 6: Run canonical QA and commit**

```bash
./tools/dev/check
git diff --check
git add sql/neon tests/test_neon_sql_contract.py
git commit -m "feat: define durable query generation schema"
```

---

### Task 4: Export verified accepted artifacts into immutable Neon candidate generations

**Files:**
- Create: `scripts/build_neon_generation_sql.py`
- Create: `tests/test_neon_generation_export.py`
- Modify: `src/theseus_repo_search/accepted_catalog.py` only if a reusable canonical generation-ID helper belongs with the catalog model.

**Interfaces:**
- Consumes a Task 1 catalog plus Task 2 `LocalCorpusState` objects whose release verification/fingerprint and extracted artifact identity have already been checked.
- Produces deterministic SQL for one candidate generation; it does not activate it.

Required signatures:

```python
def query_projection_schema_version() -> str: ...  # exactly "repo-search-query-v1"
def generation_content_sha256(*, catalog_sha256: str, projection_schema_version: str) -> str: ...
def generation_identity(*, generation_content_sha256: str, nonce: str) -> str: ...
def build_generation_sql(
    *,
    catalog: AcceptedCatalog,
    states: Mapping[str, LocalCorpusState],
    nonce: str,
) -> str: ...
```

Every emitted corpus row binds source ID, release tag, artifact identity, accepted fingerprint, source kind/revision/authority, and catalog digest. Every search row uses the same stable identity rule as local retrieval: `candidate_id = <accepted_corpus_source_id>::<declaration_id>` when a declaration resolves, otherwise `<accepted_corpus_source_id>::<source_row_id>`. It retains evidence grade/matchable declaration/source provenance. SQL emission must escape data safely and deterministically; no source text may become executable SQL syntax.

- [ ] **Step 1: Write exporter RED tests**

Cover deterministic output for a fixed nonce, different catalog digest -> different content identity, different nonce -> different generation ID with the same content identity, missing corpus state/artifact, artifact identity mismatch, accepted fingerprint mismatch, malicious quote/newline text escaping, duplicate candidate ID, and no activation statement in generated SQL. Encode arbitrary source text as UTF-8 hex decoded server-side (or an equivalently injection-proof representation), not ad-hoc quoted SQL.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_neon_generation_export -v
```

- [ ] **Step 3: Implement deterministic candidate export only**

Reuse `load_artifact` and the accepted catalog bindings. Do not re-parse Lean or infer provenance. Batch inserts deterministically by `source_id` then candidate ID.

- [ ] **Step 4: Run exporter tests GREEN and canonical QA**

- [ ] **Step 5: One-corpus disposable-branch canary before eight-corpus load**

Use one accepted corpus (Marton is smallest), but **never** publish a one-corpus generation on the Neon default branch and never point `active_generation` at it. The production exporter remains strict: `build_generation_sql(canonical_catalog, states, ...)` requires all eight canonical states. For this bounded preflight, create a canary-only one-entry catalog object from the exact canonical Marton entry, give it a distinct canary catalog digest, and use only a disposable Neon child branch created from current `main`.

On that disposable branch:

1. apply the exact `repo_search` schema already proven by Task 3;
2. generate/execute Marton-only canary SQL using the canary catalog identity, never the canonical eight-corpus digest;
3. verify corpus/artifact/catalog/generation/content identity through the native Neon development connector;
4. verify completeness for that canary catalog, mark the candidate READY to freeze it, but **do not activate it as production state**;
5. query it only through `repo_search.search_generation(...)` by explicit generation ID;
6. compare exact provenance/candidate identity with the existing Marton SQLite projection;
7. record `pg_total_relation_size` and cold/warm query latency, then delete the disposable branch.

The existing MarcoPolo PostgreSQL connection is bound to the default branch, so cross-route `data_query` verification is deliberately deferred to Task 5's complete eight-corpus generation. Raw SQLite and Postgres relevance scores need not match; exact identity/provenance must match and the expected canary candidate must be present.

- [ ] **Step 6: Commit**

```bash
git add scripts/build_neon_generation_sql.py tests/test_neon_generation_export.py src/theseus_repo_search/accepted_catalog.py
git commit -m "feat: export accepted query generations"
```

---

### Task 5: Build and verify the full eight-corpus durable generation

**Files:**
- Create: `qa/query-plane/marton-zeta-seam-v1.json`
- Modify: `README.md`
- No new service/runtime code unless a live canary exposes a concrete gap.

**Interfaces:**
- Consumes Tasks 1-4.
- Produces the first observable eight-corpus READY generation and operational receipt/checkpoint evidence; #117 remains open until second-runtime recovery and later promotion integration gates are completed.

- [ ] **Step 1: Add the bounded real canary fixture before the full load**

Fixture names concrete search queries and expected source IDs/declaration identities or explicit no-signal classes. It must not encode raw backend scores.

- [ ] **Step 2: Materialize all eight accepted releases from the catalog and independently verify every release/receipt/fingerprint**

Use the same exact catalog digest for local and Neon paths. Abort candidate publication on any missing/mismatched corpus; do not build a seven-of-eight active generation.

- [ ] **Step 3: Export and execute the full candidate generation**

Record row counts by corpus and total Postgres relation/index bytes. Confirm the result remains under the current 1 GB Free Postgres/project limit with measured, not estimated, bytes.

- [ ] **Step 4: Verify completeness, then mark the candidate READY to freeze it**

Before acceptance search, verify exact catalog digest, expected corpus count, row-count/integrity invariants, and generation content identity. Call `mark_generation_ready(...)` only after those checks pass. From this point the candidate is immutable but still inactive; a failed later canary leaves it inactive and eligible for guarded garbage collection.

- [ ] **Step 5: Run the real canary against the inactive immutable READY generation**

Query the candidate by generation ID through `repo_search.search_generation(...)`, which shares the exact ranking/filtering implementation used by production `repo_search.search(...)` but does not consult or mutate the active pointer and refuses BUILDING state. Compare exact corpus/source/artifact provenance against local SQLite results. Any mismatch leaves the prior active generation untouched.

- [ ] **Step 6: Atomically activate the canaried READY generation from the observed previous generation**

Only after the READY-generation canary passes, call `activate_generation(...)`. Immediately read back `generation_status()` and the catalog digest through both native Neon and MarcoPolo `data_query`. A stale compare-and-switch must be treated as a failed activation requiring fresh readback, never an unconditional retry.

- [ ] **Step 7: Exercise warm search, partial/degraded reporting, and guarded inactive-generation GC without mutating authority**

Use a temporary candidate generation or local fixture to prove missing corpus state is explicit. Do not corrupt the active READY generation to test failure handling. Separately create or retain a disposable inactive READY generation, prove `gc_inactive_generation(...)` cannot delete the active generation, then delete the disposable inactive target and read back that its generation/corpus/search rows are gone while the active generation is unchanged.

- [ ] **Step 8: Run canonical QA and update #117 checkpoint**

Record exact Git head, catalog digest, generation ID, eight corpus identities/counts, measured storage, local cold/warm canary, Neon query canary through both routes, and unresolved acceptance items.

- [ ] **Step 9: Commit documentation/canary fixture**

```bash
git add qa/query-plane/marton-zeta-seam-v1.json README.md
git commit -m "test: record multicorpus query-plane canary"
```

---

## Follow-on Plan Boundary: Promotion QA Integration

Do **not** add a GitHub Actions Neon write path in this plan. The approved design requires eager query-plane materialization as the final corpus-promotion QA stage, but current GitHub integration cannot read repository secret metadata (`403 Resource not accessible by integration`), so the CI credential route is presently UNKNOWN.

After a Neon CI write credential/connection is explicitly configured and independently probed, write a separate narrow plan that:

1. adds the promotion workflow stage after immutable release verification;
2. uses a direct/unpooled Neon connection for schema/materialization operations as required by Neon connection semantics;
3. builds + verifies a candidate generation, runs the canary, then compare-and-switches active generation;
4. records `ARTIFACT_ACCEPTED / QUERY_PLANE_PENDING|READY|DEGRADED` without invalidating an already accepted immutable release when Neon is unavailable;
5. applies a bounded retention policy (at minimum active plus one known-good previous generation when space permits) and uses only guarded inactive-generation GC under measured storage pressure;
6. adds exact-head hosted CI/readback acceptance.

Issue #117 must remain OPEN until that promotion stage plus the required second execution-instance/recovery canary are VERIFIED or explicitly reported UNVERIFIED per the issue acceptance contract.

## Plan Self-Review Gate

Before implementation begins, verify this plan against the spec and prior review history:

- every authority handoff compares exact identity instead of copying provenance labels;
- every task/commit is runnable without files deleted or matrix rows enabled before their consumers exist;
- each promised RED executes after test discovery succeeds and before the corresponding behavior is implemented;
- cache/generation publication never destroys the last known-good projection before replacement verification;
- search-behavior configuration participates in projection schema/generation identity;
- rebuild derives from accepted releases even if old derived state is unreadable;
- no task silently assumes GitHub Actions Neon credentials exist.
