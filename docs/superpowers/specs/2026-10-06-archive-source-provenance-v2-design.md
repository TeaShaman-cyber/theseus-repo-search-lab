# Archive Source Provenance V2 Design

**Issue:** #68
**Parent corpus:** #67
**Status:** design approved; written specification self-reviewed; implementation plan pending
**Primary goal:** represent immutable archive-backed Lean sources honestly through producer, artifact, projection, replay, and consumer receipts without fabricating Git authority or breaking existing Git-backed artifacts.

## 1. Problem

The generic Lean producer was designed around one authority shape:

```text
Git repository @ exact commit = source authority
```

Issue #67 found a canonical source that does not have that shape. The Lean formalization accompanying arXiv:2610.06368 is published by the authors as an immutable Zenodo software archive:

```text
https://doi.org/10.5281/zenodo.23160921
decreasing-diagrams-lean.zip
sha256 4601cfef943144d27e4cd0daef5a2b8308f23dddec8cc6f997274585211e7c0f
```

The Lean mechanics are ordinary Lean 4 + Lake + Mathlib. The incompatibility is source authority and provenance representation.

The current normalized artifact schema `theseus.repo-index.v1` is Git-shaped throughout:

- manifest source = `repo + commit + subdir`;
- `Node.source_commit`;
- `SourceChunk.source_commit`;
- raw dependency-graph receipt source = `repo + commit + subdir`;
- projection metadata contains `source_commit`;
- registered replay validates against `LeanGitSource`;
- consumer receipts expose `source_repo` and `source_commit`;
- authoritative state is named `created_from_authoritative_commit`.

Encoding an archive SHA-256 as a fake Git commit would make transport mechanics indistinguishable from source authority. Mirroring the archive into a project-owned Git repository would not repair that semantic error: the mirror commit would describe the mirror, not the upstream publication.

## 2. Invariants

The authority invariant becomes:

```text
source authority
  = exact externally verifiable source identity

Git:
  repository + exact commit + subdir

Archive:
  immutable/versioned URL + SHA-256 + format + subdir
```

The rest of the evidence flow remains:

```text
source authority
      ↓
verified materialization
      ↓
Lean build / exact extraction
      ↓
normalized immutable artifact
      ↓
disposable SQLite projection
      ↓
replay / query candidate evidence
```

A materialized checkout or extraction directory is mechanics, not authority.

A project-owned mirror MAY be used later as a cache or transport optimization, but MUST NOT replace or rewrite the upstream authority identity.

Publisher identity is not an adapter boundary. Zenodo is not special in the normalized model; the archive contract is based on immutable URL + checksum semantics.

## 3. Compatibility strategy

### 3.1 Existing Git artifacts

`theseus.repo-index.v1` remains supported and unchanged.

Existing v1 artifacts MUST:

- continue to load;
- preserve the existing artifact identity algorithm;
- preserve existing projection/retrieval/replay behavior;
- require no migration or republishing.

No existing Git source descriptor is rewritten merely to adopt the new vocabulary.

### 3.2 Archive artifacts

Archive-backed authoritative artifacts use a new schema:

`theseus.repo-index.v2`.

V2 is introduced because a sidecar-only archive receipt would leave the manifest itself making a false Git claim. The manifest authority representation must therefore become explicit.

V2 support is additive. The loader dispatches by artifact schema and normalizes both versions into one internal authority model.

## 4. Source authority model

Introduce an explicit internal sum type conceptually equivalent to:

```text
SourceAuthority =
    GitAuthority(
        repo,
        commit,
        subdir
    )
  | ArchiveAuthority(
        url,
        sha256,
        format,
        subdir
    )
```

The exact Python class layout is an implementation detail. The important rule is that callers do not infer source kind from string shape.

### Git serialized form

V1 remains:

```json
{
  "source": {
    "repo": "owner/repository",
    "commit": "<40-char git sha>",
    "subdir": "..."
  }
}
```

### Archive serialized form

V2 uses an explicit discriminator:

```json
{
  "source": {
    "kind": "archive",
    "url": "https://...",
    "sha256": "<64-char sha256>",
    "format": "zip",
    "subdir": "..."
  }
}
```

If a future v2 Git serialization is introduced, it MUST also be explicitly discriminated. This issue does not require republishing existing Git artifacts as v2.

## 5. Generic exact revision inside normalized records

Today `Node` and `SourceChunk` use a field named `source_commit`. For archive sources that name is false.

V2 introduces the generic concept:

```text
source_revision
```

Semantics:

```text
Git v1       -> source_revision = exact Git commit
Archive v2   -> source_revision = archive SHA-256
```

For v1 decoding, the loader maps serialized `source_commit` into the internal revision value.

For v2 serialization, nodes and source chunks use `source_revision`, not `source_commit`.

The revision is a compact equality/join key inside the normalized artifact. It is NOT sufficient source authority by itself. Full authority remains the structured manifest `source` object.

This avoids duplicating URL/repository metadata in every node while still preventing archive data from being mislabeled as Git.

## 6. Authoritative-state naming

`created_from_authoritative_commit` is also Git-specific.

V2 uses:

```text
created_from_authoritative_source
```

V1 decoding preserves current behavior by mapping:

```text
created_from_authoritative_commit
  -> created_from_authoritative_source
```

No v1 manifest bytes or identity calculation are changed.

Consumers should use the generic internal meaning. Compatibility properties may remain temporarily where needed, but new archive logic MUST NOT emit `created_from_authoritative_commit=true`.

## 7. Archive materialization boundary

The acquisition slice already defines `theseus.lean-archive-source.v1` and verifies:

- HTTPS archive URL;
- exact SHA-256 before extraction;
- supported archive format;
- traversal rejection;
- symlink/device rejection;
- duplicate/unsafe member rejection;
- source-root containment;
- deterministic materialization receipt.

For issue #68, archive materialization must additionally persist a deterministic **member-level hash manifest** for the authoritative archive members. The manifest records each normalized archive member path and its content SHA-256 and has its own digest. Build outputs created after extraction are not added to this authority manifest.

The archive materialization receipt becomes an input to exact extraction rather than an isolated preprocessing receipt.

The extraction stage MUST verify that:

1. the selected source root belongs to the verified materialization;
2. the materialization receipt and member-hash manifest match the selected source descriptor and archive SHA-256;
3. every authoritative archive member still matches the persisted member hash **immediately before LeanDepViz extraction**;
4. the source-owned `lean-toolchain` is observed from that verified root;
5. the raw dependency graph is produced only after those checks;
6. every authoritative archive member is revalidated **immediately after LeanDepViz extraction and before the raw graph receipt is published**;
7. any changed, missing, type-changed, or newly shadowing authoritative member blocks publication of the raw graph receipt;
8. separately classified build outputs such as `.lake` products may exist without entering authority, provided they do not replace or mutate an authoritative archive member;
9. no Git cleanliness/readback check is required or fabricated for archive sources.

This closes the archive TOCTOU boundary: `lake exe cache get`, `lake build`, build hooks, or the extractor itself cannot mutate an authoritative source member while the producer continues to attest the original archive tree.

## 8. Raw dependency-graph authority receipt

Git receipts currently use `theseus.raw-depgraph-receipt.v2` with a Git-shaped source object.

Archive extraction introduces a new receipt version or a backward-compatible explicitly discriminated version. The preferred design is a new version to keep old receipt bytes/validation stable.

Conceptual archive receipt:

```json
{
  "schema": "theseus.raw-depgraph-receipt.v3",
  "source": {
    "kind": "archive",
    "url": "https://...",
    "sha256": "<archive sha256>",
    "format": "zip",
    "subdir": "decreasing-diagrams-lean"
  },
  "materialization": {
    "tree_sha256": "<deterministic extracted-tree digest>",
    "member_manifest_sha256": "<digest of persisted path/content-hash manifest>"
  },
  "scope": {
    "root_modules": ["Regular", "Singular", "Audit"]
  },
  "producer": {
    "kind": "lean-dep-viz",
    "tool_repo": "...",
    "tool_commit": "...",
    "tool_hash": "..."
  },
  "observed": {
    "lean_toolchain": "leanprover/lean4:v4.30.0"
  },
  "raw_depgraph": {
    "sha256": "..."
  }
}
```

The materialized tree hash is evidence that extraction used the same verified archive tree. The member-manifest hash binds the exact authoritative file set used for pre/post extraction revalidation. Neither replaces the archive SHA-256 as upstream authority.

V2 Git receipts remain valid. There is no requirement to republish existing Git receipts as v3.

## 9. Artifact identity

Artifact identity MUST continue to bind:

- artifact schema;
- full structured source authority;
- producer pin;
- scope;
- authoritative-source state;
- normalized member hashes;
- authority receipt hash.

For v1 artifacts, the current identity algorithm remains byte-for-byte compatible.

For v2 archive artifacts, identity includes the structured archive authority rather than a synthetic `repo/commit` pair.

Changing any of:

- archive URL;
- archive SHA-256;
- format;
- subdir;
- root modules;
- producer pin;
- authority receipt;
- normalized members

must change the artifact identity or fail validation before identity is accepted.

## 10. Projection

The SQLite projection remains disposable and must not become an authority database.

V2 projection metadata adds generic provenance sufficient for consumers to reconstruct the artifact authority without schema guessing.

Preferred metadata shape:

```text
artifact_schema
source_kind
source_revision
source_authority_json
created_from_authoritative_source
artifact_identity
```

Existing v1 projection readers remain compatible. Migration of historical SQLite projections is unnecessary because projections are disposable and rebuildable.

V1 Git projections keep the existing `source_commit` layout unchanged. V2 archive projections MUST store the archive revision only as `source_revision`; an archive SHA-256 MUST NOT be stored or exposed in a `source_commit` column or compatibility view.

Shared retrieval code may normalize v1 `source_commit` and v2 `source_revision` into one internal revision field after schema-aware decoding. That compatibility belongs in the reader/model boundary, not in a misleading archive database column.

The acceptance criterion is semantic honesty, not a cosmetic rename: querying a v2 archive projection must never surface its digest under Git-commit terminology.

## 11. Registered replay

Registered replay becomes source-kind aware through the descriptor loader:

```text
LeanSource = LeanGitSource | LeanArchiveSource
```

Replay validation compares structured authority:

Git:
- manifest repo == descriptor repo;
- manifest commit == descriptor commit;
- subdir/scope/exclusions match.

Archive:
- manifest URL == descriptor URL;
- manifest SHA-256 == descriptor SHA-256;
- format/subdir/scope/exclusions match;
- artifact authority receipt validates the same source identity.

Replay MUST NOT accept an archive artifact against a Git descriptor or vice versa.

Source-specific research assertions remain in source-specific replay scripts. No archive-publisher special case enters retrieval or graph code.

## 12. Consumer receipt

Consumer receipts must report source authority without assuming Git.

For v1 Git artifacts, existing fields remain accepted.

For v2 archive artifacts, the receipt records a structured source object, for example:

```json
{
  "artifact": {
    "name": "...",
    "identity": "...",
    "source": {
      "kind": "archive",
      "url": "https://...",
      "sha256": "...",
      "format": "zip",
      "subdir": "..."
    }
  }
}
```

A consumer receipt must never emit `source_commit=<archive hash>`.

The replay payload MUST also carry the exact `artifact_identity` it consumed. Before reporting PASS, the consumer receipt builder MUST compare:

1. the freshly validated artifact identity;
2. the projection metadata artifact identity; and
3. the replay payload artifact identity.

All three must be identical. A `status=PASS` replay from another artifact is invalid evidence and must fail closed. This is a general consumer-acceptance correctness requirement, not an archive-only exception. The pre-existing v1 gap tracked by #70 is now closed on `main` by PRs #71 and #73; archive work must preserve that invariant rather than re-open it.

## 13. Workflow routing

The generic producer workflow branches only at acquisition/readback mechanics:

```text
descriptor
   ↓
source kind?
   ├─ git
   │   checkout exact commit
   │   git readback + tracked cleanliness
   │
   └─ archive
       download exact URL
       verify SHA-256
       safe extraction
       materialization receipt
       tree/source-root readback
   ↓
observe source-owned Lean toolchain
   ↓
lake cache/build
   ↓
LeanDepViz extraction
   ↓
normalized artifact
   ↓
artifact verification
   ↓
projection + replay
```

After verified materialization, Lean build/extraction logic is shared.

There must be no Zenodo-specific branch in the workflow.

## 14. Security and fail-closed behavior

Archive support increases the input boundary and therefore must fail closed.

Required negative cases include:

- checksum mismatch;
- HTTP or credential-bearing authority URL;
- absolute archive member path;
- parent traversal;
- symlink/device/special member;
- duplicate normalized member path;
- selected source root escaping extraction tree;
- materialization receipt or member-manifest mismatch;
- authoritative archive member mutation before or during extraction;
- descriptor/manifest source-kind mismatch;
- archive URL or digest tampering after artifact publication;
- raw dependency graph receipt not bound to the same archive authority;
- consumer receipt attempting Git-shaped provenance for archive artifact.

No network fallback is allowed after an authority mismatch.

## 15. Migration and rollout

Rollout is intentionally asymmetric:

1. retain v1 Git artifact production and consumption unchanged;
2. implement internal generic authority/revision types;
3. teach artifact loader to read both v1 and v2;
4. add v2 archive artifact writer/validator;
5. add archive raw-depgraph receipt validation;
6. add projection/replay/consumer support for archive authority;
7. wire the archive acquisition path into the generic hosted producer;
8. prove the path with the #67 Zenodo Lean corpus;
9. only then consider whether future Git artifacts should ever use v2.

Execution-state closure is part of the rollout contract:

- every intermediate commit/task state must keep existing v1 Git production and consumption runnable;
- archive workflow rows MUST NOT be enabled before the v2 loader, projection, replay, and consumer paths they depend on already exist and are verified;
- removing or renaming an input is allowed only after all surviving consumers have been migrated;
- every promised RED regression must be discoverable by the stated test command and fail for the intended reason before the production change;
- use the repository's existing deterministic QA surface first (`./tools/dev/check`, touched-surface checks, and the existing hosted producer/consumer workflow) rather than introducing a bespoke verifier unless an uncovered gap is demonstrated.

There is no bulk artifact migration.

## 16. Acceptance criteria

The architecture is accepted only when:

1. all existing v1 Git artifact/replay tests remain green;
2. historical v1 artifact identity remains unchanged;
3. an archive artifact contains no fabricated Git repository or Git commit;
4. changing archive URL/SHA-256/subdir causes validation failure or a distinct artifact identity;
5. raw dependency graph receipt is bound to the verified archive materialization, persisted member manifest, pre/post extraction member revalidation, and source authority;
6. normalized nodes/source chunks use generic revision semantics for v2;
7. v2 archive projections never expose archive digests as `source_commit`;
8. projection and registered replay preserve archive authority without source-kind guessing;
9. consumer receipt reports structured archive authority and rejects any replay whose `artifact_identity` differs from artifact/projection identity;
10. #70 is closed and merged fixes #71 + #73 establish the general replay-to-artifact consumer binding invariant before archive acceptance;
11. the exact Zenodo source from #67 completes hosted producer + fresh artifact-only consumer acceptance;
12. repository-native QA passes for the changed scope;
13. any hosted repository-wide baseline failure is separated from PR-introduced diagnostics;
14. no Isabelle adapter, source registry, mirror-authority scheme, or publisher-specific retrieval logic is introduced.

## 17. Non-goals

- changing theorem/research authority semantics;
- making Repository Search an authority over upstream source publication;
- supporting arbitrary archive formats in v1; ZIP is sufficient for the observed source;
- adding Isabelle ingestion;
- mirroring Zenodo into Git as canonical authority;
- migrating all existing Git artifacts to v2;
- combining corpora into one artifact/database;
- introducing a hosted source registry;
- changing lexical/graph ranking;
- fixing unrelated repository-wide heavy-Python baseline debt.

## 18. Multi-budget expectation

This design deliberately spends a small additional implementation and QA budget to reduce future epistemic and maintenance risk.

Expected conversion:

```text
extra implementation/CI cost now
        ↓
reusable immutable-archive acquisition + provenance
        ↓
less repeated model reasoning about source identity
less human provenance reconstruction
lower risk of false Git authority
lower future onboarding cost for archive-published formal corpora
```

The hypothesis is falsified or narrowed if the archive path remains a one-off for #67, requires pervasive source-kind branching in retrieval code, or materially increases maintenance/CI cost without reuse.

A successful build is not sufficient evidence of value. The downstream value gate remains #67 / the math-research smoke: the accepted corpus must expose a useful bounded formal seam or terminate as `NO_USEFUL_SIGNAL`.
