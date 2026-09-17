# Generic Lean Git Source Design

**Issue:** #5  
**Status:** design approved in chat; implementation blocked on corrected Zeta23 bootstrap acceptance  
**Primary goal:** make the existing repository lens consume more than one Lean Git corpus without changing retrieval semantics or turning the tool into a source registry/product platform.

## 1. Problem

The bootstrap producer is operationally tied to `anthropics/formal-math/zeta23`: its workflow and producer JSON mix source identity, build facts, extractor pins, and Zeta-specific replay configuration.

The repository lens itself is already source-neutral after normalization. The architectural gap is therefore before the artifact boundary: source checkout/build/extraction must be driven by a small versioned descriptor rather than publisher-specific workflow code.

The second proving corpus is `openai/LongGapsBetweenPrimes`, a public Lean/Lake number-theory formalization. At the design checkpoint its observed default-branch commit is:

`03a1190d0bc5502d9f54eeb60ad3e45e22b0df0b`

At that snapshot the repository declares Lean `v4.33.0`, uses Lake, and has `LongGapsBetweenPrimes` as its default/main library target.

## 2. Invariants

```text
Git repository @ exact commit = source authority
source descriptor             = versioned orchestration input
LeanDepViz / FTS / SQLite     = reproducible projections
normalized artifact           = portable evidence boundary
query result                  = candidate evidence with exact provenance
```

The source descriptor MUST NOT become an alternate source of truth for repository contents. Any duplicated observable source fact is checked against the exact checkout or omitted.

One source snapshot produces one normalized artifact and one disposable SQLite projection. V1 does not combine corpora into one database.

Publisher is not an adapter boundary. Anthropic and OpenAI Lean repositories use the same source path when their mechanics are both Git + Lean + Lake.

## 3. `LeanGitSource` descriptor

V1 descriptors are JSON under `producer/sources/` and use schema `theseus.lean-git-source.v1`.

Required fields:

```json
{
  "schema": "theseus.lean-git-source.v1",
  "source_id": "openai-long-gaps",
  "source_repo": "openai/LongGapsBetweenPrimes",
  "source_commit": "03a1190d0bc5502d9f54eeb60ad3e45e22b0df0b",
  "source_subdir": "",
  "root_modules": ["LongGapsBetweenPrimes"],
  "build_target": "LongGapsBetweenPrimes"
}
```

`source_id` is an orchestration label only. Artifact identity remains based on source repository, exact commit, subdir, producer pin, scope, and member hashes.

`source_subdir` is relative to the Git repository root; empty string means repository root.

`root_modules` controls LeanDepViz extraction scope and normalized graph scope.

`build_target` is passed as one Lake target. V1 therefore supports Lean Git sources whose selected source root is a Lake project. Supporting non-Lake Lean projects is a future adapter boundary, not part of this slice.

The Lean toolchain is read from the exact source snapshot through its `lean-toolchain` file. The descriptor does not duplicate a floating Lean version. The producer receipt records the observed toolchain string for diagnostics, while the source commit remains the authority that binds it.

## 4. Producer boundary

The current Zeta23 workflow becomes a generic Lean-source producer path with these stages:

1. load and validate one source descriptor;
2. checkout `source_repo` at `source_commit`;
3. resolve `source_subdir` and verify exact Git readback;
4. read the source-owned `lean-toolchain` from that checkout;
5. run `lake exe cache get` in the selected source root;
6. run `lake build <build_target>`;
7. run the pinned standalone LeanDepViz extractor for `root_modules`;
8. build the existing normalized artifact from Git-tracked Lean files only;
9. verify artifact integrity;
10. build a disposable SQLite projection;
11. run a source-specific acceptance replay;
12. delete raw extractor output and SQLite before artifact upload.

Extractor and runner bootstrap pins remain producer configuration, not source descriptor fields. They are versioned separately because they describe how evidence is produced, not what repository snapshot is being indexed.

## 5. Replay separation

Source descriptors define source mechanics only. They do not contain research assertions or ranking expectations.

The existing `replay_zeta23.py` remains a Zeta23 acceptance fixture. A small `replay_long_gaps.py` provides the second-corpus acceptance fixture.

The OpenAI replay MUST prove only generic capabilities:

- at least one exact declaration resolves to exact source path/line provenance;
- at least one elaborated dependency query returns edges with explicit evidence grades;
- at least one lexical query finds a source-grounded theorem/definition;
- bounded context returns source chunks from that same artifact;
- returned artifact provenance identifies `openai/LongGapsBetweenPrimes` and the exact pinned commit.

It MUST NOT introduce OpenAI-specific logic into `retrieval.py`, `graph.py`, `projection.py`, or the artifact schema.

## 6. CI shape and cost boundary

V1 may use one generic workflow implementation with two proving descriptors. Source-specific replay remains separate.

The second corpus is an acceptance/regression smoke, not an excuse to build a registry or continuously index every public Lean repository. Additional corpora are added only when a research task creates a concrete need.

No embeddings, federated ranking, combined SQLite database, hosted service, source registry, multi-user isolation, ACL layer, or background indexing is introduced in this slice.

## 7. MarcoPolo integration

`RULES.md` is changed only after both Anthropic Zeta23 and OpenAI LongGaps pass through the same descriptor-driven producer path and their artifacts are independently consumable without Lean at query time.

The cookbook change is separate because it changes project-level routing. It gets its own narrow cookbook issue/PR.

The rule remains thin and analogous to Session Search:

```text
prior conversations / branch continuity -> Session Search
repository / Lean declaration evidence  -> Repository Search
current repository state / mutation      -> Git or GitHub authority
```

Repository Search remains candidate evidence. A lexical miss is `UNKNOWN`, not proof of absence. Elaborated dependency evidence is distinguished from lexical evidence. Git at exact commit remains authority.

The expected runtime route will be a stable wrapper under `/workspace/tools/repo-search/`; the canonical cookbook rule points to the wrapper rather than implementation internals.

## 8. Acceptance criteria

This design is accepted when all of the following are observed:

1. the corrected Zeta23 bootstrap artifact passes CI and independent Lean-free consumption;
2. both Zeta23 and LongGaps use `theseus.lean-git-source.v1` descriptors;
3. both use the same generic checkout/build/extract/normalize code path;
4. both produce the existing normalized artifact schema and disposable SQLite projection;
5. both replay exact source provenance, lexical retrieval, elaborated graph traversal, and bounded context successfully;
6. no publisher-specific branch exists in query/retrieval code;
7. no combined multi-corpus state is introduced;
8. only after 1–7, a separate cookbook change installs the thin `/workspace/tools/repo-search/` route and corresponding canonical `workspace.RULES.md` section.

## 9. Non-goals

- generic multi-language source adapters;
- automatic discovery of every Lean repository;
- floating source branches or unpinned toolchains;
- source registry/server/database;
- cross-corpus semantic ranking;
- product/UI/security/multi-user features;
- replacing Git as authority;
- making Lean available in the query-time consumer runtime.
