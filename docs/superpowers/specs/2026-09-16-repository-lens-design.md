# Repository Lens Design

## Purpose

`theseus-repo-search-lab` is a specialized repository-evidence tool. It indexes a repository snapshot, preserves exact provenance, exposes dependency structure where the language/runtime can prove it, and returns bounded context suitable for LLM research work.

MarcoPolo is an initial producer/consumer runtime, not the architectural owner. The tool must remain portable to another runtime that can read the same artifacts.

## Core invariant

```text
Git repository @ exact commit = authority

normalized artifact + search index + graph = rebuildable projections

retrieval hit = candidate evidence with exact provenance
```

No projection may silently become authority. A search miss is not proof of absence unless the indexed scope is explicitly complete for that query class.

## Evidence grades

Every relationship returned by the tool carries one of these grades:

1. `ELABORATED_VALUE_DEPENDENCY`
   - extracted from a compiled/elaborated environment;
   - source declaration value/proof directly references the target constant.
2. `ELABORATED_TYPE_DEPENDENCY`
   - extracted from a compiled/elaborated environment;
   - source declaration type directly references the target constant.
3. `STATIC_REFERENCE`
   - parser/source-derived reference without elaboration authority.
4. `LEXICAL_HIT`
   - search match only; no dependency claim.

The consumer may aggregate weaker evidence, but must not promote its grade.

## Architecture

```text
                       authority boundary
                              |
                              v
                    Git repo @ exact commit
                              |
                    +---------+---------+
                    |                   |
                    v                   v
            exact extractor       static extractor
            (language-aware)      (degraded mode)
                    |                   |
                    +---------+---------+
                              v
                 normalized repo-index artifact
                              |
              +---------------+----------------+
              |               |                |
              v               v                v
          lexical FTS    dependency graph   metadata/provenance
              |               |                |
              +---------------+----------------+
                              v
                    bounded retrieval API
                              |
               +--------------+--------------+
               |                             |
               v                             v
            MarcoPolo                   other consumers
```

The first language-specific exact producer is Lean. The first proving ground is `anthropics/formal-math/zeta23`.

## Producer boundary

Heavy language-specific work belongs in a producer runtime that can reproduce the source environment. For Lean this means the exact project toolchain and dependency lock/manifest.

The first exact graph producer composes `LeanDepViz` rather than reimplementing Lean environment traversal. The extractor version and file hash are pinned in the artifact manifest.

A producer run must:

1. checkout the exact target commit;
2. verify the checked-out commit before extraction;
3. reproduce/build the target language environment;
4. run the pinned extractor;
5. normalize the output;
6. emit a manifest binding source, extractor, scope, hashes, counts, and evidence grades;
7. publish the artifact for downstream consumption.

The consumer must not need Lean merely to search or traverse a previously produced artifact.

## Normalized artifact v1

The first artifact is a directory or archive containing:

```text
manifest.json
nodes.jsonl
edges.jsonl
sources.jsonl
```

### `manifest.json`

Required fields:

```text
schema
source.repo
source.commit
source.subdir
producer.kind
producer.tool_repo
producer.tool_commit
producer.tool_hash
scope.root_modules
scope.dependency_boundary
members.nodes.sha256
members.edges.sha256
members.sources.sha256
counts.nodes
counts.edges
created_from_authoritative_commit
```

`created_from_authoritative_commit` is `true` only when the producer independently read back the checked-out commit before extraction. `members.sources.sha256` is null when `sources.jsonl` is absent; null means absent, not unchecked.

Artifact identity is not the source commit alone. A v1 artifact identity is derived from the artifact schema, source repository/commit/subdir, extraction scope, producer kind/repository/commit/hash, and member hashes. Changing the extractor or scope at the same source commit therefore produces a different artifact identity.

### `nodes.jsonl`

One declaration/entity per row. Within one artifact, Lean declaration IDs use the canonical full declaration name prefixed by the language (`lean:<fullName>`); the artifact manifest supplies the source/scope identity. Other languages may define their own deterministic ID scheme in a later schema revision.

```text
id
name
kind
module
source_path
source_start_line
source_end_line
source_commit
```

Source ranges may be null when the exact producer cannot provide them. Null means unknown; consumers must not invent ranges.

### `edges.jsonl`

One directed relationship per row:

```text
source_id
target_id
relation
evidence_grade
producer
```

For the initial Lean exact graph, `relation` is `type_dependency` or `value_dependency` and the evidence grade is the corresponding elaborated grade.

The v1 Lean artifact uses `scope.dependency_boundary = internal_only`: only declarations inside the declared root-module scope become normalized nodes/edges. A dropped Mathlib/Lean/external edge is therefore **outside scope**, not evidence of absence. Graph-query responses must return the artifact scope/boundary and may claim completeness only *within that declared boundary*. A future schema may add external stubs or a full-environment artifact, but v1 does not silently pretend to contain them.

The normalized edge orientation is always **dependent declaration -> dependency**: `source_id` is the declaration whose type/value is being described, and `target_id` is the declaration it depends on. Extractors with the opposite native orientation are inverted during normalization. Therefore `dependencies(name)` follows outgoing edges and `reverse_dependencies(name)` follows incoming edges.

### `sources.jsonl`

Optional normalized textual source chunks used by lexical retrieval. Every row carries commit/path/range provenance and a content hash.

## Search projection

SQLite FTS5 is the first lexical projection because it is local, inspectable, cheap to rebuild, and already proven useful in the zeta23 replay.

The SQLite database is disposable and is never committed as authority. It can be recreated from the normalized artifact.

Search behavior:

- exact identifier query prefers exact-name matches;
- ordinary lexical query returns ranked declaration/source candidates;
- every result returns source commit, path/range when known, declaration identity, and evidence grade;
- semantic/vector retrieval is deferred until a replay demonstrates value beyond lexical + graph retrieval.

## Graph retrieval

The first graph operations are deliberately small:

```text
dependencies(name, depth=N)
reverse_dependencies(name, depth=N)
path(source, target, max_depth=N)
context(name, depth=N, token_budget=M)
```

Defaults must be bounded. No unbounded recursive traversal is exposed by the normal LLM-facing command. Every graph result also reports the artifact's `scope.root_modules` and `scope.dependency_boundary`; `complete_within_scope=true` never implies completeness outside that boundary.

`context` may later borrow ranking ideas from Aider RepoMap, but v1 does not require PageRank. The first version can rank exact graph distance before adding another algorithmic dependency.

## Incremental lifecycle

The source commit is a mandatory component of artifact identity, but not the whole identity. Schema, extraction scope/boundary, producer repository/commit/hash, and member hashes are also bound. A new source commit always produces a new immutable artifact identity, and a changed producer/scope at the same commit does too.

Local projections may use content-addressed reuse inspired by Continue, but incremental optimization is secondary to deterministic rebuild correctness.

The first implementation may rebuild the small zeta23 FTS projection from scratch. Incremental indexing is added only after rebuild cost is measured and becomes material.

## Degraded mode

If exact extraction is unavailable:

```text
Git source
  -> static parser / lexical index
  -> STATIC_REFERENCE / LEXICAL_HIT only
```

The system must state that exact elaborated dependencies are unavailable. It must not infer them from textual references.

External hosted services such as Prove2Me may enrich results, but authentication or availability failures must not block basic operation over a public Git snapshot.

## First replay set

The initial acceptance replay uses the pinned zeta23 source snapshot already studied in `marcopolo-cookbook#40`:

1. locate `lemmaR_tight_two` from a role/meaning query;
2. return its exact elaborated direct dependencies;
3. return reverse dependencies of `rank_trace_mult_k`;
4. locate the moment certificate neighborhood around `count_certificate`;
5. return at least one exact ancestor path from `count_certificate` into its load-bearing dependency graph;
6. locate the `ChebyshevMertens` interface from a role/meaning query.

The benchmark compares exact grep, repository-wide grep, declaration-aware lexical search, and graph traversal. Exact grep remains the expected winner when the exact identifier is already known.

## Acceptance criteria

The bootstrap implementation passes when:

- source and extractor commits/hashes are recorded and independently verifiable;
- the producer emits a normalized artifact without hidden mutable state;
- MarcoPolo consumes a previously built artifact without Lean installed in the query path;
- lexical replay reduces unrelated file scanning for meaning/role queries;
- exact graph replay answers the three registered dependency questions from elaborated edges;
- all returned evidence includes provenance, evidence grade, and declared scope/boundary;
- rebuilding the projection from the same artifact is deterministic for all contract-relevant fields;
- no hosted credential is required for public-repository baseline operation.

## Error handling

Failures are explicit states rather than silent fallbacks:

- source commit cannot be checked out -> `BLOCKED_SOURCE_BINDING`;
- checked-out commit differs from requested commit -> `BLOCKED_SOURCE_MISMATCH`;
- exact producer cannot build target environment -> `DEGRADED_EXACT_EXTRACTION_UNAVAILABLE`;
- artifact hash/manifest mismatch -> `BLOCKED_ARTIFACT_INTEGRITY`;
- requested exact graph operation on a static-only artifact -> `UNAVAILABLE_EVIDENCE_GRADE`;
- lexical search miss -> `UNKNOWN`, unless scope completeness is independently established;
- graph lookup crossing an omitted external boundary -> bounded result with explicit `internal_only` scope, never a global absence claim.

## Testing

Tests are layered:

1. **contract tests** — validate manifests and JSONL schemas;
2. **projection tests** — rebuild FTS and verify deterministic query fixtures;
3. **graph tests** — bounded traversal, reverse traversal, cycle safety, path limits;
4. **evidence-grade tests** — weaker inputs cannot yield stronger labels;
5. **replay tests** — the zeta23 research queries above;
6. **producer smoke** — one pinned Lean repository produces an exact artifact in CI;
7. **consumer smoke** — the artifact is downloaded and queried in an environment where Lean is not required.

## Upstream composition

Initial upstream roles:

- `cameronfreer/LeanDepViz` — exact Lean elaborated dependency extraction;
- `justincasher/lean-explore` — reference implementation for declaration-search models and retrieval UX;
- `Aider-AI/aider` — reference for graph-ranked token-budget context selection;
- `continuedev/continue` — reference for content-addressed incremental index lifecycle;
- `Julian/tree-sitter-lean` — candidate static degraded extractor;
- `yamadashy/repomix` — optional export/context packaging reference.

All external components remain replaceable. The normalized artifact contract is the stable boundary owned by this repository.

## Repository boundary

This repository owns:

- artifact schemas/contracts;
- producer adapters and pinned extractor integration;
- search/traversal projection code;
- replay corpus and acceptance tests;
- CLI/LLM-facing retrieval contract;
- runtime-neutral documentation.

`marcopolo-cookbook` owns only the thin route describing how MarcoPolo invokes/consumes this tool. It does not own this tool's algorithms, schemas, or replay suite.

## Non-goals

The bootstrap does not promise:

- proof verification beyond the evidence supplied by the producer;
- semantic embeddings;
- hosted search;
- automatic indexing of arbitrary languages;
- mutation or repair of indexed repositories;
- equivalence between lexical/static references and elaborated proof dependencies.
