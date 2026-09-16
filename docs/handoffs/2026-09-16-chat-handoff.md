# Chat Handoff — Repository Search / RH Tooling

Date: 2026-09-16

This document is a continuity checkpoint for a fresh ChatGPT project chat. It is not an authority override. GitHub exact refs, CI artifacts, and issue/PR state remain authoritative.

## Start here

1. Prefer MarcoPolo for workspace/repository-local state. Read `/workspace/RULES.md` before operational work.
2. Work primarily in `TeaShaman-cyber/theseus-repo-search-lab`.
3. Re-read current GitHub state before acting; this handoff records a snapshot, not future proof.
4. Do not use built-in ChatGPT memory as proof of current repository state.
5. Keep scope narrow: this is a research repository lens for formal-math work, not a product/platform.

## Why this exists

Repository Search is being built as a sibling of Session Search for research tasks such as `TeaShaman-cyber/theseus-research#37` (Riemann/zeta work): exact Git source -> normalized evidence artifact -> disposable SQLite FTS5 + bounded dependency graph -> evidence-addressed search/context.

Core invariant:

```text
Git repository @ exact commit = authority
normalized artifact / SQLite / ranking = derived projection
lexical hit != elaborated dependency evidence
search miss = UNKNOWN unless completeness is established
```

GitHub reviews/issues are intentionally used as a regression-memory loop: prior failures become new tests/invariants when the same mechanism applies.

## Current bootstrap implementation — Issue #1 / PR #4

Canonical issue:
- `TeaShaman-cyber/theseus-repo-search-lab#1`

Implementation PR:
- PR #4 `Implement: bootstrap repository lens v1`
- head branch: `impl/bootstrap-v1`
- exact snapshot at handoff creation: `a4be5b639bc68eb7f874076df58ab0a939cb7eb4`
- base: `plan/bootstrap-v1`

Verified locally on that head before handoff:
- `85/85` full unittest suite PASS
- `28/28` artifact/normalizer tests PASS under `python -O`
- workflow YAML parses
- previous corrected Zeta artifact still verifies under the stricter loader
- consumer replay against that artifact passes

Previous exact GitHub artifact evidence from head `ec7c8fed2ddb411a659622b92e3a0f3498c694fc`:
- workflow run `35098296990` SUCCESS
- artifact id `10448253092`
- artifact name `zeta23-repo-index-v1`
- GitHub ZIP digest `sha256:6c77e2ec9989479d754036cb4f001751b6e5755de19627048b93e74a405da734`
- independent MarcoPolo download matched that digest exactly
- Lean removed from PATH: `lean=absent`
- artifact verified: 6046 nodes / 45424 edges / 5277 source chunks
- independent projection/replay PASS:
  - `lemmaR_tight_two`: 19 direct dependency edges
  - reverse `rank_trace_mult_k`: 1 edge to `rank_trace_mult_k_le`
  - `count_certificate` depth<=5: 97 edges and reaches `N0star_lower_moment`
  - `tight pairs extremal` -> `lemmaR_tight_two` rank 1
  - `Chebyshev Mertens` -> `ChebyshevMertens` rank 1
  - bounded context for `count_certificate`: 21 chunks / ~2589 estimated tokens

Important: that `ec7c8fe` artifact is good consumer evidence but is NOT the final acceptance artifact anymore because later review added producer/source-binding hardening.

### Remediation history already implemented

Codex review found multiple real correctness gaps. They were fixed with TDD and recorded in Git history. Relevant commits include:

- `80a7625` preserve authoritative-readback attestation through projection/query
- `5e3ace8` enforce root-module scope
- `e35d92d` validate relation/evidence-grade pairs
- `caf2705` fail closed on malformed LeanDepViz payloads
- `8ac7ec6` replay baseline uses tracked source scope
- `be9a517` immutable/crash-safe artifact publication boundary
- `072362a` bind raw depgraph to verified source snapshot; no-path semantics; duplicate-name lexical binding; Lean comment lexical state
- `a4be5b6` reject unknown raw graph endpoints and validate node/source location consistency

Remediation plan:
- `docs/superpowers/plans/2026-09-16-codex-review-remediation.md`

### OPEN at handoff time

Do not assume these have finished; re-read GitHub first.

- GitHub Actions run on exact head `a4be5b6...`:
  - run id `35103649537`
  - run number 8
  - state at handoff snapshot: `IN_PROGRESS`, during Mathlib/Zeta build
- Codex exact-head re-review was requested on PR #4 for `a4be5b6...` with a narrow integrity/provenance/retrieval rubric.
- At handoff snapshot, no new Codex review submission for `a4be5b6...` had appeared yet.

### Immediate next action for PR #4

1. Read current run `35103649537` and latest Codex PR #4 reviews/threads.
2. If run is SUCCESS and Codex has no new actionable finding:
   - download the NEW artifact from the `a4be5b6...` run,
   - independently verify ZIP digest in MarcoPolo,
   - remove Lean from consumer PATH,
   - verify artifact, rebuild disposable SQLite, rerun the Zeta graph/lexical/context checks above,
   - write final acceptance receipt to Issue #1.
3. Only then promote/merge bootstrap according to current branch/PR structure.
4. Do not treat the older `ec7c8fe` artifact as final acceptance for the newer producer-receipt code.

If Codex reports new defects, triage technically before accepting; do not auto-apply review comments.

## Generic Lean source — Issue #5 / PR #7

Approved design goal: make Zeta23 the first `LeanGitSource`, not the architecture. Same generic Git+Lean+Lake+LeanDepViz producer should handle a second corpus without publisher-specific code.

Canonical issue:
- `TeaShaman-cyber/theseus-repo-search-lab#5`

Approved design:
- merged PR #6
- `docs/superpowers/specs/2026-09-16-generic-lean-source-design.md`

Implementation plan PR:
- PR #7 `Plan: generic Lean source producer`
- branch: `plan/generic-lean-source`
- exact plan head: `cd41594bbf94659816f0c665a55e1dc794fff2c8`
- plan file: `docs/superpowers/plans/2026-09-16-generic-lean-source.md`

Second proving corpus chosen:
- `openai/LongGapsBetweenPrimes`
- pinned commit: `03a1190d0bc5502d9f54eeb60ad3e45e22b0df0b`

Hard gate:
- DO NOT start generic-source implementation until Issue #1 bootstrap has a final corrected artifact independently consumed Lean-free.

PR #7 already has three concrete Codex P2 findings that are OPEN unless later commits/reviews supersede them:

1. Resolve/validate canonical `source_root` BEFORE executing Lake, so a symlink cannot escape checkout before authority validation.
2. Replay must bind supplied SQLite projection to supplied artifact by comparing projection `artifact_identity` with `artifact_identity(manifest)`.
3. LongGaps replay must require the exact pinned commit `03a1190...` and expected empty subdir, not merely repo name/root module.

A fresh plan-review request was also submitted with a strict YAGNI/completeness rubric. Re-read latest PR #7 review before editing the plan.

Non-goals remain explicit:
- no registry service
- no combined corpus DB
- no embeddings/federated ranking
- no hosted service
- no ACL/security/product layer

## MarcoPolo RULES integration — NOT YET DONE

User explicitly wants Repository Search to become as easy to route to as Session Search via MarcoPolo `/workspace/RULES.md`.

Do NOT add the route before the second-corpus smoke proves the generic producer path.

Intended thin routing semantics after dual-source acceptance:

```text
past conversation/history -> Session Search
repository/formal proof/dependency evidence -> Repository Search
current repository state -> Git/GitHub authority
```

RULES integration belongs in `TeaShaman-cyber/marcopolo-cookbook` as a separate post-smoke issue/PR. Keep only a thin route/invocation contract there; repo-search owns schema, producer adapters/configs, tests, artifacts, replay, CLI.

## Research issue #37 — math is parked, not abandoned

Repository Search exists to reduce repeated manual traversal while working on:
- `TeaShaman-cyber/theseus-research#37`

Do not let infrastructure turn into a product detour. Once repo-search bootstrap + source abstraction + RULES route are usable, return to Issue #37 and use the tool on the actual mathematical questions.

Relevant parked RH/Zeta continuity:
- Connes route was localized around finite approximants and compact-uniform convergence:
  `theta_lambda  ?->  k_lambda  -> Xi`, then Hurwitz transfers zero location under the needed convergence conditions.
- A main unresolved bridge was whether the chosen finite approximants/control establish the required relation strongly enough; the repository lens was created to inspect formal source/dependency structure rather than repeatedly grep manually.
- Other issue #37 holes remain separate; re-read the issue before resuming and do not infer progress from this handoff alone.

## Operational warnings learned in this chat

- Green CI is not sufficient proof if acceptance metric scope is wrong. An earlier run was green while lexical scanning had accidentally indexed `.lake/packages`, producing ~239k chunks / 194 MB sources instead of 5277 / ~6.8 MB. Fixed by Git-tracked source enumeration.
- Relative/absolute source-root mismatch broke CI after local smoke used absolute paths; now regression-tested.
- Artifact publication and SQLite rebuild must preserve last known-good state on failure.
- FTS5 tokenizer/config belongs in projection fingerprint.
- Python-side query tokenization must not diverge from `unicode61`; Unicode like `xi`/Greek symbols matters in math corpora.
- Short declaration names are not unique identity; use source path/range/full Lean identity where available.
- Known external LeanDepViz endpoint may be filtered by root scope; an endpoint absent from raw node table is malformed exact evidence and must be blocked.
- Do not move multi-user/session security issues from Hermes into this single-operator repository tool unless operational assumptions actually change.

## Working style for the next chat

- Continue inline; user is skeptical of expensive subagent fan-out.
- Use Codex as an external reviewer with narrow rubric and primary-source links, not as authority or automatic fixer.
- TDD: RED -> minimal GREEN -> full verification -> small commit.
- Avoid intermediate pushes that restart expensive Lean builds; batch locally, push after fresh verification.
- Prefer one concrete next action and keep explanations compact.

## Exact handoff branch

This document lives on:

```text
handoff/2026-09-16-repo-search-chat
```

It is a continuity pointer only. Do not merge it as implementation. Re-read Issues #1/#5, PRs #4/#7, workflow runs, and exact branch SHAs before acting.
