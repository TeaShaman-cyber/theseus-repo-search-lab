# Durable Multi-Corpus Query Plane Design

## Purpose

Issue #117 exists because accepted Repository Search corpora are durable, but the
searchable runtime state is not. A MarcoPolo execution-instance transition lost
temporary SQLite projections and made the accepted collection unavailable until
manual recovery.

This design separates three concerns that must not be collapsed:

```text
corpus authority != durable query plane != engineering workbench
```

The goal is a search surface that survives loss or replacement of a shell/runtime,
while preserving immutable accepted releases as authority and keeping every search
projection reproducible.

## Product boundary

Repository Search remains a search-only tool for locating and contextualizing
existing formalized mathematics. This design does not add theorem proving, proof
certification, mandatory axiom auditing, embeddings, or a general hosted platform.

The durable query plane is an operational projection. It is never mathematical or
corpus authority.

## Authorities and projections

```text
versioned accepted-corpus catalog
             +
immutable accepted GitHub Releases
             |
             | authoritative identity + provenance
             v
      normalized accepted artifacts
             |
      +------+-------------------+
      |                          |
      v                          v
local SQLite/FTS5          Neon Postgres
rebuildable reference      durable shared projection
      |                          |
      +------------+-------------+
                   v
          bounded query contract
```

Authority remains the accepted catalog plus immutable release receipts/assets.
SQLite and Neon are derived projections. Either can be discarded and rebuilt from
accepted artifacts without changing corpus identity.


## Accepted catalog contract

The accepted set is an explicit repository-owned versioned file, initially
`producer/accepted-corpora.json`. Runtime code must not infer acceptance by
listing mutable GitHub Releases. Each catalog entry binds a stable `source_id`
to the exact release tag, package asset, receipt asset, current source descriptor
path, and accepted artifact identity/fingerprint needed by
`consume_accepted_release.py`. Unknown keys, duplicate source IDs, duplicate
release tags, missing assets, or identity disagreement fail closed.

The catalog has its own canonical SHA-256. A deterministic
`generation_content_sha256` is derived from that catalog digest plus the
query-projection schema/version. Each materialization attempt has a separate
immutable `generation_id` instance identity bound to that content identity. A query
result and canary receipt report the active generation ID, generation content
identity, and catalog digest, so a PASS from one accepted set or build instance
cannot be attributed to another.

## Primary architecture decision

The first pain to remove is runtime-local state loss. Therefore accepted corpus
promotion eagerly materializes the durable Neon query projection as a final
operational stage.

The process is:

```text
producer QA
-> immutable accepted release
-> verify receipt/provenance/fingerprint
-> artifact acceptance
-> materialize candidate Neon generation
-> verify completeness/identity
-> atomically mark generation READY (freeze; still inactive)
-> read-only search canary + provenance readback on immutable READY candidate
-> atomically activate generation
-> searchable-corpus promotion complete
```

Artifact acceptance and query-plane readiness are distinct states:

```text
ARTIFACT_ACCEPTED
QUERY_PLANE_PENDING | QUERY_PLANE_READY | QUERY_PLANE_DEGRADED
```

A Neon outage or materialization failure does not invalidate an already verified
immutable release. It prevents the corpus from being declared operationally ready
in the shared search plane until the downstream stage succeeds.

This distinction preserves authority while making downstream usability an explicit
promotion postcondition.

## Generation model

Neon publication is generation-based. A new generation is built without mutating
the currently READY generation in place.

```text
READY generation N
        |
        +--> build N+1
             -> verify accepted corpus identities
             -> build/update lexical projection
             -> completeness/identity verification
             -> mark N+1 READY (immutable, inactive)
             -> canary search through generation-scoped production semantics
             -> exact provenance readback
             -> atomically switch active_generation N -> N+1
```

If any pre-switch step fails, generation N remains active and the failure is
reported. Partial new state must never silently become the active search surface.

The generation metadata binds at minimum:

- catalog revision/digest;
- accepted corpus identity;
- immutable release tag and receipt identity;
- source repository/revision;
- artifact schema and producer fingerprint;
- projection schema/version;
- build timestamp and verification state.


### Activation concurrency and immutability

A READY generation is immutable query state. Materialization never updates its
corpus/search rows in place. PostgreSQL enforces this boundary at the database
layer: once a generation is READY, INSERT/UPDATE and row-level DELETE against its
generation, `corpora`, or `search_docs` rows is rejected except for the one-way
BUILDING -> READY transition performed by the guarded publication function. The
only READY deletion exception is guarded whole-generation garbage collection of an
inactive, unreferenced generation after exact identity readback; active generations
and partial child-row deletion remain forbidden. Concurrent builders may
create candidate generations,
but activation uses one atomic compare-and-switch from the previously observed
active generation/catalog digest to the fully verified candidate. A loser must not
overwrite a newer active pointer. Re-running the same accepted catalog is idempotent at the content level: it may
reuse an already verified generation with the same `generation_content_sha256`.
If that derived instance is stale/corrupt, recovery may build a new immutable
`generation_id` carrying the same content identity and activate it only after fresh
verification.

If an active generation is unreadable, semantically drifted, or otherwise fails
query-plane verification, recovery is derived from accepted releases. Verification
of the disposable/derived old state must never make authoritative rebuild
impossible; inspection errors become `STALE_QUERY_PROJECTION`/degraded evidence and
the repair path constructs a fresh candidate generation before activation.

All behavior-affecting search configuration belongs to the projection schema
identity (table/function/index definitions and ranking/tokenization contract), not
just corpus rows. Changing that contract requires a new projection schema/version
and therefore a new generation identity.

## Query plane versus workbench plane

The query plane must not depend on the lifetime of one shell execution instance.
The workbench is where materialization, repair, diagnostics, batch work, and schema
changes happen.

Observed current surfaces already support this separation:

```text
thin/read-only query plane
  - native ChatGPT Neon connector
  - MarcoPolo governed `data_query` over saved PostgreSQL queries
  - future Repository Search MCP/plugin surface

heavy engineering workbench
  - MarcoPolo `workspace_shell`
  - Hermes or another shell/runtime
```

A shell failure is therefore not automatically a query-plane failure. Capability
health must be reported per surface.

Pre-activation verification uses a generation-scoped read function that shares the
same retrieval implementation as the active production query path; it differs only
in taking an explicit generation ID. It accepts only READY generations, so the
candidate is frozen before the canary observes it. This lets an inactive immutable
candidate be tested before the active pointer changes, without an ad hoc query or a
BUILDING-to-READY mutation window that could invalidate the canary.

The initial user/agent-facing read contract should remain small and bounded. The
future native shape is expected to resemble:

```text
search(query, limit, mode?)
fetch(candidate_id)
context(candidate_id, depth?, token_budget?)
```

No native MCP/API implementation is required by the first implementation step;
the design only requires that the underlying query state and contract do not make
such a surface depend on a shell-local cache.

## Neon role

Neon is the first durable shared query projection because current project evidence
already verifies two independent live routes to the same database state:

```text
ChatGPT native Neon connector -> Neon
MarcoPolo PostgreSQL connector/data_query -> Neon
```

This gives Repository Search a query state that can survive replacement of a
MarcoPolo execution instance.

Neon does not own accepted corpus identity and does not replace GitHub Releases.
A Neon row or index that cannot be reconciled to the accepted catalog/release
identity is stale or corrupt projection state and must fail closed.

## SQLite role

SQLite/FTS5 remains a first-class local/reproducible projection because it is
already implemented, deterministic, cheap, and part of #117's existing acceptance
contract.

Its roles are:

1. local/offline search when a shell/runtime can materialize accepted artifacts;
2. deterministic rebuild/reference path for verifying projection semantics;
3. recovery path when the durable query plane is unavailable, provided authority
   can still be verified from accepted releases;
4. differential/canary comparison when Neon search semantics are introduced.

SQLite is not required to remain alive across runtime replacement. Automatic
materialization from accepted releases is the recovery mechanism for the local
path. Warm-cache identity also binds a repository-owned SQLite projection schema
version covering behavior-affecting table/index/FTS-tokenizer and retrieval-contract
choices. A version mismatch is stale derived state and forces rebuild; a projection
must never validate indefinitely only against its own old fingerprint.

## Search semantics

The first durable query plane is lexical/declaration-aware search. Embeddings and
vector retrieval are explicitly deferred.

The existing Repository Search evidence boundary remains unchanged:

- exact declaration identity is stronger than an ordinary lexical hit;
- lexical search returns candidate evidence, never proof or theorem truth;
- every result carries corpus/source provenance and match/evidence mode;
- a missing result is not global absence unless scope completeness is separately
  established.

Neon Lakebase Search is available for later evaluation, but `lakebase_text` is not
currently installed in the existing Neon project and this design does not require
installing it before an implementation plan measures need and migration cost.

If multiple corpus-local rankers are used, raw scores from independent indexes must
not be assumed comparable. Rank fusion such as RRF is allowed only after each
corpus-local retriever is correct independently. Deterministic stable IDs must be
used as final tie-breakers.

## Candidate identity and result contract

Every result must retain a stable identity independent of the serving backend.
Conceptually:

```text
candidate_id = <accepted_corpus_source_id>::<declaration_id-or-source_row_id>
```

The local retrieval API must retain the projection `sources.id` selected for a source-backed hit. This row identity is used only when no stable declaration ID resolves; it must not be confused with the accepted corpus `source_id`. A result with neither declaration ID nor projection source-row ID fails closed instead of synthesizing identity from mutable path/line presentation fields.

The result contract includes at minimum:

- `candidate_id`;
- accepted corpus identity/name;
- source repository and exact revision;
- declaration/source identity;
- path/range when known;
- evidence grade / search match mode;
- local rank and deterministic global rank where applicable;
- active query-plane generation;
- explicit partial/unavailable corpus information.

Backend-specific raw relevance scores are diagnostic fields, not stable product
semantics.

## Failure semantics

Failures are explicit and layer-specific.

Examples:

```text
release/receipt mismatch
  -> BLOCKED_AUTHORITY_INTEGRITY

Neon generation does not match accepted catalog
  -> STALE_QUERY_PROJECTION

new generation build/canary fails before switch
  -> QUERY_PLANE_DEGRADED; previous READY generation remains active

Neon unavailable, accepted releases available
  -> durable query plane DEGRADED; local SQLite recovery may remain AVAILABLE

MarcoPolo shell unavailable, data_query/native Neon still works
  -> WORKBENCH_UNAVAILABLE; QUERY_PLANE_AVAILABLE

one accepted corpus cannot be served
  -> explicit partial result with searched/unavailable counts and corpus reason
```

No failure may silently drop a corpus or silently substitute guessed/cached
authority.

## Free-tier constraints

Current official Neon Free-plan constraints relevant to this design include:

- 1 GB Postgres storage per project;
- 100 CU-hours/project/month;
- mandatory scale-to-zero after 5 minutes;
- 5 GB public network transfer/project/month;
- 10 branches/project.

The eight currently accepted compressed corpus artifacts total about 8.49 MiB.
That does not establish Postgres/index size. The implementation plan must measure
actual table/index inflation before assuming the full accepted collection fits the
Free storage budget.

Mandatory scale-to-zero is acceptable for an intermittent research query plane if
cold-wake latency remains within the measured usability target. Because each READY
generation duplicates derived query rows, the query plane must support guarded
whole-generation garbage collection for inactive READY generations. Retention is
bounded and explicit rather than allowing immutable historical projections to grow
until the Free storage ceiling blocks publication.

## Corpus acceptance integration

The durable query-plane stage becomes part of the normal corpus promotion process,
not a manual cleanup task after release publication.

A corpus is permitted to have a valid immutable artifact while query publication is
pending. However, the normal promotion workflow must not report the corpus as fully
search-ready until the shared query plane has observed and served it.

Final promotion evidence must include:

1. accepted release identity verified;
2. durable generation containing the corpus;
3. bounded search smoke returning expected corpus/source provenance;
4. exact generation/catalog readback;
5. active-generation switch confirmation;
6. local/reference canary coverage where required by #117.

This stage belongs in repository-owned QA/promotion automation so a future corpus
cannot be accepted into the searchable set while silently missing from the durable
query plane.


## Promotion credential boundary

Repository-owned promotion automation needs an independently configured Neon write
credential/connection route. Interactive ChatGPT Neon and MarcoPolo connectors do
not prove that GitHub Actions has that authority. Until the CI credential path is
observed and verified, automated Neon publication is `BLOCKED_USER_SETUP` rather
than silently falling back to a different authority or storing credentials in the
repository. Query credentials, where exposed to native consumers, should be
read-only.

## Implementation sequencing

Implementation should remain small and reversible.

### Slice 1 — schema and generation contract

Define the durable projection schema, catalog/generation metadata, stable result
contract, and fail-closed identity checks. Use one accepted corpus for the first
non-production materialization probe.

### Slice 2 — one-corpus differential canary

Materialize one accepted corpus into Neon, query it through both native Neon and
MarcoPolo `data_query`, and compare identity/provenance/results with the existing
SQLite projection. Measure storage and cold/warm latency.

### Slice 3 — eight-corpus generation

Build a complete generation from the current accepted catalog, verify all eight
corpora, and exercise partial-failure behavior before switching the active
pointer.

### Slice 4 — promotion integration

Add the eager generation/materialization/canary stage to accepted-corpus promotion
QA. A newly accepted corpus reaches `QUERY_PLANE_READY` only after the durable
postcondition is observed.

### Slice 5 — actual research-runtime canary

Run the #117 real cold/warm canary from the actual research runtime. Independently
exercise a second query surface/runtime so loss of one workbench does not invalidate
the result.

Native Repository Search MCP/plugin work is intentionally deferred until the durable
query contract is stable and useful.

## Verification

Repository QA remains `./tools/dev/check` plus the issue-specific operational
canaries. Green unit tests alone are insufficient.

The implementation is not accepted until observable postconditions include:

- immutable accepted authority remains independently verifiable;
- local SQLite projections can still rebuild from accepted artifacts;
- the active Neon generation exactly matches the accepted catalog revision;
- a search through the durable query plane returns exact provenance;
- the same saved read-only query can execute through the MarcoPolo connector plane;
- a second independent query route can read the same active generation;
- workbench/shell loss is exercised without silently losing the durable query
  capability;
- partial corpus failure is explicit;
- cold/warm behavior and storage consumption are measured, not assumed.

## Non-goals for this issue

Do not add without a separately justified change:

- theorem proving or proof certification;
- embeddings/vector search;
- AI Gateway;
- Object Storage solely to duplicate immutable GitHub Releases;
- a public hosted Repository Search service;
- a new MCP server before the durable query contract is stable;
- replacing the existing accepted artifact producer;
- making Neon authoritative for corpus acceptance.
