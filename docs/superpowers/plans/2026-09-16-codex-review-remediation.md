# Codex Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Close the correctness gaps found by Codex review on PR #4 before bootstrap acceptance without expanding the repository lens into a product/platform.

**Architecture:** Preserve the current artifact/query design and harden only evidence integrity, scope/provenance, tracked-source replay semantics, and crash-safe publication. Keep publisher-neutral generic-source work in Issue #5 separate.

**Tech Stack:** Python 3.11 stdlib, SQLite FTS5, Git, LeanDepViz producer workflow, unittest.

**Spec:** `docs/superpowers/specs/2026-09-16-repository-lens-design.md`

## Global Constraints

- Git repository at exact commit remains authority.
- Normalized artifact and SQLite are derived projections.
- `internal_only` must never expose nodes outside declared `root_modules`.
- Lexical hit must not be promoted to elaborated dependency evidence.
- Malformed recognized input fails closed.
- Existing known-good artifact/projection must survive failed rebuild/publication.
- No multi-user security, source registry, embeddings, hosted service, federated ranking, or other product scope.

---

### Task 1: Preserve authoritative-readback attestation through projection/query

- [x] RED: add regression showing an exact artifact built with `created_from_authoritative_commit=false` remains distinguishable after SQLite projection and search/context output.
- [x] GREEN: persist the attestation in projection metadata and expose it in query/context result metadata without changing evidence grades.
- [x] Verify targeted tests and full suite.

### Task 2: Enforce declared root-module scope at artifact boundary

- [x] RED: construct a hash-consistent `internal_only` artifact containing an out-of-scope `Mathlib.*` node and edge; require load to fail `BLOCKED_ARTIFACT_INTEGRITY`.
- [x] GREEN: validate every node module against `scope.root_modules` before accepting/publishing an artifact.
- [x] Verify targeted tests and full suite.

### Task 3: Validate dependency relation/evidence-grade pairs

- [x] RED: construct mismatched pairs such as `value_dependency` + `ELABORATED_TYPE_DEPENDENCY` and unknown v1 relations; require fail-closed load.
- [x] GREEN: allow only v1 dependency relations and their corresponding elaborated grades.
- [x] Verify targeted tests and full suite.

### Task 4: Fail closed on malformed LeanDepViz payloads

- [x] RED: cover non-list `nodes`/`edges`, non-object rows, null/non-string required node fields, malformed edge fields, and duplicate `fullName` entries.
- [x] GREEN: replace `assert`/`str(...)` coercion with explicit JSON type validation and duplicate rejection; preserve known edge-direction normalization.
- [x] Verify with normal Python and `python -O` targeted normalization tests, then full suite.

### Task 5: Make Zeta replay baseline use authoritative tracked source scope

- [x] RED: add `.lake/packages/.../*.lean` and another untracked Lean file to replay fixture and prove baseline ignores both.
- [x] GREEN: enumerate the same Git-tracked Lean files as artifact source scanning rather than `rglob("*.lean")`.
- [x] Verify real pinned Zeta replay still passes and baseline metric now measures only authoritative source files.

### Task 6: Keep canonical artifact path continuously reachable during publication

- [x] RED: reproduce interruption/failure in the publication swap window and assert the old canonical artifact remains reachable and valid.
- [x] GREEN: make published artifacts immutable; stage and verify in the target parent filesystem, then use one `os.replace` only for a new/empty output path. Existing non-empty artifacts fail closed instead of entering a swap window.
- [x] Verify regression plus full suite; staging is created in `out_dir.parent`, so first publication uses one same-filesystem `os.replace`; published non-empty paths are immutable.

### Deferred from PR #4

`@[simp] theorem ...` / leading Lean attribute recognition is a real lexical-source compatibility gap, but both current pinned corpora contain zero such declaration lines. Track it under Issue #5 unless the fix becomes necessary for the OpenAI second-corpus smoke.

### Completion Gate

- [x] `git diff --check` clean.
- [x] Full unittest suite PASS from the implementation worktree.
- [x] Real pinned Zeta23 rebuild/replay PASS.
- [ ] One consolidated push to PR #4.
- [ ] Request Codex exact-head re-review with the same narrow correctness rubric.
- [ ] Corrected GitHub artifact independently consumed without Lean before Issue #1 bootstrap acceptance.

## Second Codex Review Addendum

### Task 7: Bind authoritative raw dependency graphs to the verified source snapshot

- [ ] RED: prove an authoritative build can currently stamp a raw graph from commit A as commit B.
- [ ] GREEN: require a producer receipt for authoritative exact-mode builds that binds the raw graph SHA-256, source repository/commit/subdir, root modules, and producer pin to the guarded extraction operation.
- [ ] Update the producer workflow to generate and consume the receipt; direct authoritative exact mode without a valid receipt must fail closed.
- [ ] Verify receipt tampering, commit mismatch, graph hash mismatch, and happy path.

### Task 8: Distinguish bounded no-path from a valid zero-length graph path

- [ ] RED: disconnected declarations within `max_depth` must not serialize as `FOUND` with an empty edge list.
- [ ] GREEN: return an explicit path-found outcome; CLI reports `UNKNOWN` for no bounded path while source==target remains a valid zero-edge `FOUND` path.
- [ ] Verify graph library and JSON CLI behavior.

### Task 9: Resolve lexical hits using source location when declaration names collide

- [ ] RED: two in-scope declarations with the same short name but different source paths must resolve each FTS hit to the correct declaration ID.
- [ ] GREEN: use source path/range binding before falling back to globally unique short-name resolution.
- [ ] Verify duplicate-name lexical provenance and preserve existing exact-name ambiguity behavior.

### Task 10: Make Lean comment detection lexical-state aware

- [ ] RED: block-comment delimiters inside strings or line comments must not hide following declarations.
- [ ] GREEN: track Lean string, line-comment, and nested block-comment state so only real block delimiters affect declaration visibility.
- [ ] Verify nested comments, delimiter strings, line comments, and the existing theorem-shaped block-comment regression.

### Task 11: Reject unknown LeanDepViz dependency endpoints

- [ ] RED: a raw edge referencing a declaration absent from the raw `nodes` table must fail closed, while an edge to a known external node remains intentionally filtered.
- [ ] GREEN: retain raw full-name/module membership separately from the in-scope normalized node map; reject truly unknown endpoints before scope filtering.
- [ ] Verify the new regression, existing external-node filtering, and the full suite.

### Task 12: Validate node source locations against source chunks

- [ ] RED: a node bound to another declaration's source range must fail artifact loading; partial source location fields must also fail closed.
- [ ] GREEN: when a node carries source location metadata, require a complete path/start/end triple and, when source chunks are present, exactly one chunk with the same path/range and declaration hint.
- [ ] Verify malformed bindings are blocked while legitimately unbound/generated nodes remain accepted.
