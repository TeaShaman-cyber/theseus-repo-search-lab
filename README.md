# theseus-repo-search-lab
Reproducible repository indexing, dependency graphs, and bounded LLM retrieval for Theseus research.

## QA cadence

The canonical local gate is `./tools/dev/check`; run it for every implementation commit.

The heavy GitHub Actions producer smoke is an acceptance-slice gate, not a per-push gate. Run it explicitly when a coherent remote-integration slice is ready, when hosted-runner/workflow/source-build behavior changed, or when remote-only evidence is required. Before closing acceptance work or merging, the exact current head must have a successful heavy remote run plus the required artifact/readback evidence. Any later head change invalidates that final-remote claim.
