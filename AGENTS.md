# Repository Search agent contract

Read `README.md` -> **Purpose and scope (SEARCH-ONLY)** and the
[Repository Lens design](docs/superpowers/specs/2026-09-16-repository-lens-design.md)
before proposing or implementing a change. These version-controlled documents
define the enduring product scope. Issues and PRs are coordination/evidence,
not a replacement for that scope.

- **Do:** index accepted formal-mathematics sources; retrieve existing Lean
  declarations and statements (including admitted statements); search lexical
  text and bounded dependency graphs; return provenance and honest evidence
  boundaries. Optimize actual search usefulness, latency, multi-corpus access,
  and disposable-index recovery.
- **Research purpose:** expose formal mathematical structure for downstream
  research: decompositions, dependency cones, invariants, reductions, bridge
  theorems, and justified structural correspondences/isomorphisms. Landmark solved
  formalizations (for example FLT and Poincare) are control/reference corpora for
  testing those methods. Human discovery history is not required input and must
  not be substituted for formal source structure.
- **Do not:** attempt to prove theorems, introduce proof certification, or
  require theorem-level axiom audits as a condition for indexing, searching,
  onboarding, or accepting a search-runtime improvement. Historical audit
  artifacts may remain as provenance and should not be erased.
- **Preserve the runtime boundary:** Lean may run in producer acceptance to
  extract indexed evidence. An accepted-artifact consumer/query must not
  rebuild Lean sources or require a proof audit. Searches over `sorry`-backed
  declarations remain valid retrieval, never proof claims.
- **Before expanding scope:** ask whether the work improves locating or
  contextualizing existing formalized mathematics. If not, leave it out of
  Repository Search absent explicit, separately scoped authorization. Do not
  interpret old proof-audit issues or QA fixtures as a product roadmap.
- **Verify:** use the repository's `./tools/dev/check` first. For search-runtime
  changes, also exercise real cold/warm corpus queries and report absent or
  unavailable corpora explicitly; green unit tests alone do not prove usability.

Prefer existing narrow Issues for coordination, small reversible changes, and
exact-head readback. These instructions supplement rather than replace the
project contract and MarcoPolo workspace rules.
