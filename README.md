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

## Lexical search modes

`repo-search search` defaults to `--mode discovery`: multi-term lexical search may use broad OR matching and reports such hits as `CANDIDATE`. Use `--mode evidence` when all query terms must match the same source chunk; qualifying hits report `FOUND`. Exact declaration lookup remains `FOUND` in either mode.

## Live research smoke

After significant corpus acceptance, Repository Search can run a bounded versioned research-smoke panel on fresh Actions consumers. Smoke scenarios ask for evidence classes and record grounded observations; they do not encode a desired mathematical conclusion.

Current v0 scenarios live under `qa/research-smoke/`. Search probes explicitly declare `query_mode`: broad `discovery` results remain candidate signals, while promotion-relevant target probes use `evidence` mode before they can contribute `FOUND_USEFUL_STRUCTURE`. Each run emits `theseus.repo-search-research-smoke-receipt.v1` with exact tool/artifact identity, probe evidence, observed states, regression flags, and `scientific_authority = NONE`.

Smoke dispositions are non-blocking evidence states, not mathematical verdicts. `regression_detected` records loss of a previously demonstrated capability without converting the smoke layer into scientific acceptance authority.
