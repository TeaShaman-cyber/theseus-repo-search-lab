# Retired Neon / PostgreSQL query-plane experiment

Historical only. Not a supported Repository Search backend, deployment route,
or active CI gate. The PostgreSQL 17 runner, SQL migrations, probes, Python
tests and October design documents are preserved here for research provenance.
The workflow is deliberately outside .github/workflows and the tests outside
the active tests directory. Do not run the archived shell files in place:
their original paths and repository-root assumptions are historical.

Exact runnable historical layout: commit
[dc7a580e3a2d726eee674ed62513a19be85be171](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/tree/dc7a580e3a2d726eee674ed62513a19be85be171).
Use a detached disposable worktree at that revision for any explicitly scoped
reproduction, not an existing cloud database. This archive does not grant
authority to redeploy the retired backend.

Originally implemented in #117/#121/#122. Durable serving moved to Turso
in #132/#133. Current split architecture is tracked in #215; private MCP
serving remains a separate open question in #123. Retirement: #228.
No current runtime files or accepted corpus artifacts are changed.
