# theseus-repo-search-lab

## Purpose and scope (SEARCH-ONLY)

Theseus Repository Search is a fast, dependable retrieval tool for formalized
mathematical corpora, initially Lean. Given a question or declaration, it locates
existing theorem statements, declarations, dependency neighborhoods, and bounded
source context across accepted corpora, with exact provenance and evidence grades.
Statements with `sorry` are searchable. **The tool does not prove theorems,
certify proofs, or determine mathematical truth.**

Accepted source revisions and immutable release artifacts are authoritative inputs.
SQLite/FTS indexes are disposable, reproducible search projections. A lexical
match or dependency path is retrieval evidence, not a proof verdict. Corpus-class
and historical axiom evidence may prevent false proof claims, but additional
`#print axioms` audits and proof-eligibility upgrades are **not prerequisites**
for indexing, onboarding, or querying a corpus.

The normal engineering objective is useful, fast, reliable search: one query
across accepted corpora, exact result provenance, honest missing/partial signals,
and automatic recovery of disposable indexes when runtimes change. Producer-side
Lean builds may be needed to **extract** an accepted corpus; query-time consumers
must not require Lean or fresh proof audits. Independent proof research or proof
certification belongs to an explicitly separate task and authority domain,
never an implicit Repository Search acceptance gate.

The [Repository Lens design](docs/superpowers/specs/2026-09-16-repository-lens-design.md)
defines the underlying retrieval architecture. The root [AGENTS.md](AGENTS.md)
carries this permanent scope into agent work; Issues and PRs document delivery
and historical decisions, not the enduring product definition.

## QA cadence

The canonical local gate is `./tools/dev/check`; run it for every implementation commit.

Hosted acceptance is phase-specific rather than one mandatory producer+consumer run for every change.

- **Producer-affecting changes** — source revision/descriptor, extractor/toolchain pins, normalization contract, or another producer-fingerprint input require the heavy `lean-source-producer-smoke.yml` path. The exact producer artifact is accepted on a hosted runner and may then be promoted to an immutable accepted-artifact release.
- **Consumer-only changes** — retrieval, projection, replay, context, receipt, or research-smoke changes may reuse an unchanged accepted producer artifact from an immutable release. The exact current consumer head must still verify the release/tag/assets and producer fingerprint, build a fresh disposable projection, and rerun the relevant consumer checks.

Any later head change invalidates a hosted claim about that head. Producer reuse is allowed only while the complete producer fingerprint still matches; drift returns `REBUILD_REQUIRED`. Missing, corrupt, or unverifiable release evidence fails closed.

## Hosted acceptance runtime routing

The heavy producer workflow remains the acceptance surface when source-owned computation is causally relevant. Its `consume-artifact` job runs on a fresh hosted runner and consumes the normalized Actions artifact without source checkout or Lean rebuild.

Accepted producer artifacts can additionally be packaged deterministically and promoted to **GitHub Immutable Releases**. Release assets are durable accepted inputs; GitHub Actions artifacts remain transient transport inside producer/acceptance runs. A release-backed consumer verifies the immutable release and exact assets, binds the tag to the accepted repository head, verifies the package and current producer fingerprint, then rebuilds fresh consumer state.

Runtime roles are explicit:

- **GitHub Actions** — default reproducible hosted acceptance surface, using either the heavy producer path or the release-backed consumer-only path according to the changed phase.
- **GitHub Immutable Releases** — durable store for already accepted producer artifacts and their canonical acceptance metadata; never a substitute for fresh consumer verification.
- **Codespaces** — on-demand interactive/differential debugging when an independent runtime comparison is useful.
- **MarcoPolo** — orchestration, Git/GitHub operations, MCP access, and bounded checks; do not retry heavy artifact replay there merely to close acceptance after a runtime-specific `137`.

Consumer execution must remain source-free: no upstream checkout, Elan/Lake setup, Lean build, or `--source-root`. A legitimate need for source-owned computation belongs in producer acceptance or forces `REBUILD_REQUIRED`; it must not be hidden inside a release-backed consumer fallback.

### Accepted-artifact release pilot

The first verified durable corpus is `leanprover-community/flt-regular` under immutable tag `accepted-artifact/flt-regular/41bfa1d236ee59a8`. The release-backed canary in `.github/workflows/accepted-artifact-consumer-canary.yml` verifies the exact release and package, creates a fresh SQLite projection, reruns FLT replay, and reruns the versioned research-smoke scenario without rebuilding Lean. `.github/workflows/accepted-artifact-consumer-negative-canary.yml` proves producer drift requires rebuild and invalid release evidence fails closed.

### Statement-corpus evidence in release receipts

New deterministic release packages include an `evidence` section derived from their
already-digest-bound replay, with `declared_corpus_class` and
`proof_eligibility`. The release consumer independently derives the same fields
from the packaged replay and rejects a mismatched claim. All four declared corpus
classes are supported; a `PROOF_CORPUS` declaration without a separate trusted
Lean axiom audit remains `UNKNOWN_NOT_PROOF_EVIDENCE`. A declared
`STATEMENT_CORPUS`, `PARTIAL_PROOF_CORPUS`, or `BENCHMARK_CORPUS` can only yield
`NOT_PROOF_EVIDENCE` in this release channel. Existing v1 packages with no
`evidence` metadata remain valid but return **UNKNOWN**, never a proof verdict.
This is replay-origin classification, not independent proof/statement attestation
or permission to publish an immutable release. Existing artifact identities do
not change. Issue #34 preserves the historical evidence-class and axiom-audit
disposition; it does not impose an audit gate on search or onboarding.

### Mathlib archives cache (best effort)

The heavy producer stores only `$HOME/.cache/mathlib` download archives in GitHub Actions Cache, keyed by byte-level hashes of the source-owned `lean-toolchain` and `lake-manifest.json` and the runner OS. Persistence is skipped if pins are missing, the archive directory is empty, or its uncompressed size exceeds 750,000 KiB. The multi-GiB `.lake/packages` and `.lake/build` directories are **not** cached. `lake exe cache get` still runs and may restore or fetch dependencies; actual speedup requires a hosted cache-hit measurement and is not inferred from the static QA tests. Reuse of an already accepted source artifact through a source-free consumer is preferable to running the producer again when the producer fingerprint is unchanged. These download archives are operational acceleration, never source or proof authority.

## Lexical search modes

`repo-search search` defaults to `--mode discovery`: multi-term lexical search may use broad OR matching and reports such hits as `CANDIDATE`. Use `--mode evidence` when all query terms must match the same source chunk; qualifying hits report `FOUND`. Exact declaration lookup remains `FOUND` in either mode.

### Recoverable multi-corpus search

The repository-owned accepted-corpus catalog can be queried through one recoverable runtime entrypoint:

```text
python3 -m theseus_repo_search multicorpus-search \
  --catalog producer/accepted-corpora.json \
  --cache-dir <path> \
  --query <text> \
  --limit 20 \
  --mode discovery
```

The command verifies accepted release evidence, rebuilds disposable SQLite/FTS5 projections when the identity-bound cache is absent or stale, and federates results without comparing raw BM25 scores across corpora. Exact matches sort before lexical candidates; other ordering uses corpus-local rank, accepted `source_id`, and stable candidate identity. The JSON result always reports the catalog digest, searched-corpus count, explicit unavailable-corpus reasons, and provenance-rich hits. A partial corpus failure is reported as `DEGRADED`; it is never silently omitted.

## Live research smoke

After significant corpus acceptance, Repository Search can run a bounded versioned research-smoke panel on fresh hosted consumers, including release-backed consumers when the producer fingerprint is unchanged. Smoke scenarios ask for evidence classes and record grounded observations; they do not encode a desired mathematical conclusion.

Current v0 scenarios live under `qa/research-smoke/`. Search probes explicitly declare `query_mode`: broad `discovery` results remain candidate signals, while promotion-relevant target probes use `evidence` mode before they can contribute `FOUND_USEFUL_STRUCTURE`. Each run emits `theseus.repo-search-research-smoke-receipt.v1` with exact tool/artifact identity, probe evidence, observed states, regression flags, and `scientific_authority = NONE`.

After accepting a new corpus, select only relevant scenarios from `qa/research-smoke/` and run them on that corpus's fresh projection with `scripts/run_research_smoke.py`; do not run the full corpus producer matrix just for the smoke. The versioned `annals-marton-seam-v0.json` links to [Math #56](https://github.com/TeaShaman-cyber/theseus-math-research-lab/issues/56). Its first live source-specific [experimental hosted run #37768026926](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/actions/runs/37768026926) passed producer, source-free fresh consumer and research smoke on exact head `54cdadd8c336ecb2d359cf01d0872751b634a63d`, yielding `CORPUS_BOUNDARY`, `scientific_authority=NONE`. The Actions artifact is **transient**, not an immutable accepted-artifact release. Onboarding it to `main` does not itself publish a durable release or prove Marton. For unchanged producer fingerprints, reuse an existing accepted artifact on a source-free consumer rather than rerun the full producer matrix. An absent mathematical bridge is not a smoke regression.

Smoke dispositions are non-blocking evidence states, not mathematical verdicts. `regression_detected` records loss of a previously demonstrated capability without converting the smoke layer into scientific acceptance authority.
