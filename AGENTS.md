# Agent operating boundary — Repository Search

This file is a persistent work instruction for coding/research agents in this repository.
Read it at the start of a new task or a resumed conversation, **before** proposing work.
Related scope correction: [#118](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/118).
Current runtime usability blocker: [#117](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/117).

## Purpose: RETRIEVAL, not proof

Repository Search exists to provide **fast, convenient searches across Lean
declarations in accepted corpora**: exact names, lexical matches, navigable
dependencies, and source/corpus/revision provenance. The result is a
**candidate or retrieval hit**, not a mathematical discovery or proof verdict.

This repository is **not** a theorem prover, proof generator, or an independent
Lean kernel/axiom certification service. Preserve existing conservative source
and evidence-class labels as metadata; do not silently turn them into proof
verification or eligibility gates.

## Scope-drift gate (before each plan, issue, PR, or tool sequence)

1. **Name the search-user outcome.** Which real query, latency/reliability
   failure, corpus coverage gap, navigation result, or recoverability problem
   will improve? If none: stop and re-scope, rather than invent a proof task.
2. **Separate phases.** Source extraction/normalization and provenance
   checks belong to producer acceptance when genuinely needed. Retrieval,
   projection, replay, and source-free consumption belong to the consumer.
   Do not re-run Lean/Mathlib or require source checkout merely to search an
   unchanged accepted release.
3. **Do not introduce proof-work prerequisites.** A missing Lean
   `#print axioms` attestation, `sorry` audit, or independent mathematical
   correctness verdict is not by itself a blocker for searching declarations
   or onboarding clearly labeled statement/partial-proof corpora. Keep
   unknown proof status as UNKNOWN; distinguish result match from evidence.
   See [#34](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/34)
   (closed); do not reopen its proof-certification trajectory by default.
4. **Out-of-scope request:** if independent theorem proving, axiom auditing,
   or scientific correctness attestation is explicitly requested, handle it
   as a separately scoped decision/issue in the appropriate research track.
   Do not smuggle it into a Repository Search acceptance gate.
5. **Acceptance follows real usage.** For #117, prove one query searches
   the **eight existing accepted releases** and records searched/unavailable
   counts; exercise actual cold and warm runtime queries plus recovery after
   runtime loss. CI/unit green alone is not a verified working search.
   Keep immutable releases as authority; SQLite/FTS5 indexes are disposable
   derived caches. Report unavailable/untested behavior explicitly.

## Work discipline

- Use existing narrow issues and the current roadmap; avoid duplicate
  projects, corpora, proof pipelines, and speculative migrations.
- Every change must say how it improves the search-user outcome and how
  that improvement will be observed. Use the repository QA entrypoint
  `./tools/dev/check` and phase-appropriate hosted checks for code changes.
- Follow normal versioned PR review and exact-head readback. A chat
  statement or a newly created issue is not a merged policy or passing QA.
- On any chat/runtime reset, reload this file, the current relevant issue,
  and the observed repository head. Never promote old conversation context
  to current state without verification.
