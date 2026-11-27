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
