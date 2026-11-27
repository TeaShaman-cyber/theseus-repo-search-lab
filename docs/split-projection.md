# Split search / graph projection contract

Owner: [#215](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/215).
The immutable accepted artifact and existing full projection fingerprint remain
the recovery authority. No selected catalog entry changes in this step.

A candidate directory contains a search-only SQLite file (nodes, source chunks,
FTS and metadata; no edge table), a deterministic integer-to-declaration-ID
dictionary, typed edges using that dictionary, and a receipt. The profile is
`repo-search-split-v1`. The full fingerprint API rejects this profile. The
search-only fingerprint is a distinct logical digest including the profile
marker and no edges. It is not the accepted full fingerprint.

From a previously verified full projection and independently known logical
fingerprint:

```sh
PYTHONPATH=src python3 scripts/split_projection.py build --full index.sqlite --out split --full-fingerprint "$EXPECTED_FULL"
PYTHONPATH=src python3 scripts/split_projection.py verify --out split --full-fingerprint "$EXPECTED_FULL"
```

Verification checks physical file digests, exact ordered dictionary parity
with SQLite node IDs, typed edge order and uniqueness, endpoint closure, and
recombines all logical rows (including FTS postings) to match the trusted full
fingerprint. Changing both an edge and its file hash is insufficient to pass.
The output is staged and published only after verification; inputs are not
modified. CSV is a portable staging representation, not a serving database.
Convert it to Parquet at the existing governed native DuckDB boundary for
MotherDuck ingestion, and independently verify the loaded rows.

Next: explicit catalog profile routing, separate remote identity/fingerprint
verification for the two projections, bounded native graph query sessions,
and live parity of search, dependencies, paths, context and graph-search.
Kusto remains the analytical projection. Existing full Turso targets stay
selected until the replacement has passed those checks and can be recovered
from the immutable release. Actual quota savings require provider usage
readback after retirement; do not equate file bytes with billed storage.

## Automated local preparation

The existing accepted-release consumer is the only download/verification path.
Prepare the two projections and the native local DuckDB graph by corpus ID:

```sh
PYTHONPATH=.:src python3 scripts/split_projection.py prepare-corpus --catalog producer/accepted-corpora.json --cache-dir "$CACHE" --source-id openai-matrix-exponent
```

Or use `prepare` with an already verified full projection and its trusted
fingerprint (the same arguments as `build`). A second invocation reuses an
existing graph only after checking its actual serving rows against the
verified split; it does not rebuild or upload it. The preparation step
still verifies the full split and stages a compact local graph atomically.
This is an intake/recovery operation, not an operation to repeat per query.
The local graph is disposable, no MotherDuck credentials are inherited,
and no cloud query is made by the native graph builder. Do not select a
split catalog row until the remote projection and query routing gates pass.

### Candidate cloud intake

`prepare-corpus` also returns the logical split pin, independent of physical
SQLite page layout. Once the candidate catalog entry is pinned and the exact
cloud targets and lifecycle are recorded in its owner issue, invoke:

```sh
PYTHONPATH=.:src python3 scripts/split_projection.py materialize-corpus \
  --catalog producer/accepted-corpora.json --source-id openai-matrix-exponent \
  --cache-dir "$CACHE" --organization "$ORG" --group repo-search \
  --platform-base-url "$TURSO_PLATFORM_URL" \
  --motherduck-launcher "$GOVERNED_MD_LAUNCHER" --motherduck-db "$MD_DATABASE"
```

The command resolves the accepted release, verifies local preparation, creates
or reuses the pinned Turso search candidate, and exports the native graph to
temporary compressed Parquet for the configured MotherDuck SQL-file launcher.
The graph tables are created transactionally if absent, with exact typed row
and identity checks before returning success. A partial or mismatched cloud
target is blocked for reconciliation, not overwritten. Successful retry reuses
verified resources; selection and retirement are deliberately not performed by
this command. Kusto remains unchanged. The command is an intake operation,
not a warm graph query. The existing accepted release is still authoritative.
