# Codex Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

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

- [ ] RED: add regression showing an exact artifact built with `created_from_authoritative_commit=false` remains distinguishable after SQLite projection and search/context output.
- [ ] GREEN: persist the attestation in projection metadata and expose it in query/context result metadata without changing evidence grades.
- [ ] Verify targeted tests and full suite.

### Task 2: Enforce declared root-module scope at artifact boundary

- [ ] RED: construct a hash-consistent `internal_only` artifact containing an out-of-scope `Mathlib.*` node and edge; require load to fail `BLOCKED_ARTIFACT_INTEGRITY`.
- [ ] GREEN: validate every node module against `scope.root_modules` before accepting/publishing an artifact.
- [ ] Verify targeted tests and full suite.

### Task 3: Validate dependency relation/evidence-grade pairs

- [ ] RED: construct mismatched pairs such as `value_dependency` + `ELABORATED_TYPE_DEPENDENCY` and unknown v1 relations; require fail-closed load.
- [ ] GREEN: allow only v1 dependency relations and their corresponding elaborated grades.
- [ ] Verify targeted tests and full suite.

### Task 4: Fail closed on malformed LeanDepViz payloads

- [ ] RED: cover non-list `nodes`/`edges`, non-object rows, null/non-string required node fields, malformed edge fields, and duplicate `fullName` entries.
- [ ] GREEN: replace `assert`/`str(...)` coercion with explicit JSON type validation and duplicate rejection; preserve known edge-direction normalization.
- [ ] Verify with normal Python and `python -O` targeted normalization tests, then full suite.

### Task 5: Make Zeta replay baseline use authoritative tracked source scope

- [ ] RED: add `.lake/packages/.../*.lean` and another untracked Lean file to replay fixture and prove baseline ignores both.
- [ ] GREEN: enumerate the same Git-tracked Lean files as artifact source scanning rather than `rglob("*.lean")`.
- [ ] Verify real pinned Zeta replay still passes and baseline metric now measures only authoritative source files.

### Task 6: Keep canonical artifact path continuously reachable during publication

- [ ] RED: reproduce interruption/failure in the publication swap window and assert the old canonical artifact remains reachable and valid.
- [ ] GREEN: publish with a crash-safe indirection or atomic replacement strategy appropriate to the current local single-consumer workflow; do not introduce a service/database layer.
- [ ] Verify regression plus full suite; document the exact filesystem atomicity assumption.

### Deferred from PR #4

`@[simp] theorem ...` / leading Lean attribute recognition is a real lexical-source compatibility gap, but both current pinned corpora contain zero such declaration lines. Track it under Issue #5 unless the fix becomes necessary for the OpenAI second-corpus smoke.

### Completion Gate

- [ ] `git diff --check` clean.
- [ ] Full unittest suite PASS from the implementation worktree.
- [ ] Real pinned Zeta23 rebuild/replay PASS.
- [ ] One consolidated push to PR #4.
- [ ] Request Codex exact-head re-review with the same narrow correctness rubric.
- [ ] Corrected GitHub artifact independently consumed without Lean before Issue #1 bootstrap acceptance.
