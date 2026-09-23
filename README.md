# theseus-repo-search-lab
Reproducible repository indexing, dependency graphs, and bounded LLM retrieval for Theseus research.

## QA cadence

The canonical local gate is `./tools/dev/check`; run it for every implementation commit.

The heavy GitHub Actions producer+consumer workflow is an acceptance-slice gate, not a per-push gate. Run it explicitly when a coherent remote-integration slice is ready, when hosted-runner/workflow/source-build behavior changed, or when remote-only evidence is required. Before closing acceptance work or merging, the exact current head must have a successful heavy remote run plus the required artifact/readback evidence. Any later head change invalidates that final-remote claim.

## Heavy acceptance runtime routing

Heavy artifact acceptance uses the manually dispatched GitHub Actions workflow. The producer job may build Lean/source state; the separate `consume-artifact` job runs on a fresh hosted runner, downloads only the normalized Actions artifact, rebuilds a disposable SQLite projection, performs registered replay/search/graph/context checks, and emits a machine-readable consumer receipt.

Runtime roles are explicit:

- **GitHub Actions** — default reproducible heavy artifact-consumer acceptance surface.
- **Codespaces** — on-demand interactive/differential debugging when an independent runtime comparison is useful.
- **MarcoPolo** — orchestration, Git/GitHub operations, MCP access, and bounded checks; do not retry heavy artifact replay there merely to close acceptance after a runtime-specific `137`.

The consumer job must remain source-free: no upstream checkout, Elan/Lake setup, Lean build, or `--source-root`. A legitimate need for source-owned computation belongs in the producer job or a separately justified workflow slice.

## Live research smoke

After significant corpus acceptance, Repository Search can run a bounded versioned research-smoke panel on fresh Actions consumers. Smoke scenarios ask for evidence classes and record grounded observations; they do not encode a desired mathematical conclusion.

Current v0 panel preserves one historical Riemann/Zeta23 capability and one non-RH FLT bridge scenario under `qa/research-smoke/`. Each run emits `theseus.repo-search-research-smoke-receipt.v1` with exact tool/artifact identity, probe evidence, observed states, regression flags, and an explicit `scientific_authority = NONE`.

Initial smoke dispositions are non-blocking: `FOUND_USEFUL_STRUCTURE`, `CORPUS_BOUNDARY`, `NO_SIGNAL`, `DEGRADED`, and `UNKNOWN_WITHIN_CURRENT_CONTRACT` are evidence states, not mathematical verdicts. A `regression_detected` flag records loss of a previously demonstrated retrieval capability without silently converting the smoke layer into scientific acceptance authority.
