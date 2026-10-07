# theseus-repo-search-lab
Reproducible repository indexing, dependency graphs, and bounded LLM retrieval for Theseus research.

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

## Lexical search modes

`repo-search search` defaults to `--mode discovery`: multi-term lexical search may use broad OR matching and reports such hits as `CANDIDATE`. Use `--mode evidence` when all query terms must match the same source chunk; qualifying hits report `FOUND`. Exact declaration lookup remains `FOUND` in either mode.

## Live research smoke

After significant corpus acceptance, Repository Search can run a bounded versioned research-smoke panel on fresh hosted consumers, including release-backed consumers when the producer fingerprint is unchanged. Smoke scenarios ask for evidence classes and record grounded observations; they do not encode a desired mathematical conclusion.

Current v0 scenarios live under `qa/research-smoke/`. Search probes explicitly declare `query_mode`: broad `discovery` results remain candidate signals, while promotion-relevant target probes use `evidence` mode before they can contribute `FOUND_USEFUL_STRUCTURE`. Each run emits `theseus.repo-search-research-smoke-receipt.v1` with exact tool/artifact identity, probe evidence, observed states, regression flags, and `scientific_authority = NONE`.

Smoke dispositions are non-blocking evidence states, not mathematical verdicts. `regression_detected` records loss of a previously demonstrated capability without converting the smoke layer into scientific acceptance authority.
