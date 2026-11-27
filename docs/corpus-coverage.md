# Corpus coverage acceptance

Owner: [#46](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/46).
Regression: [Matrix #189](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/189)
and [local-lab #289 iteration 173](https://github.com/TeaShaman-cyber/local-lab/issues/289#issuecomment-7226690350).

A producer/consumer PASS proves integrity within the selected roots, not that
those roots cover the research question. Matrix originally extracted 27,474
declarations and 539,478 edges, but selected only 11 and 10. Turso and Kusto
correctly served that inadequate graph. A lexical hit was not a graph node.
The new gate applies to added or replaced accepted-catalog entries; it does
not invalidate historical releases or force all lexical material to be elaborated.

## Required lifecycle

1. Before expensive work, record the intended graph modules, useful exact
   declaration/dependency witnesses, and research purpose in the owning issue.
   Use the exact source/import inventory. Plan scope deliberately rather than
   copying a small successful graph after the fact.
2. Complete existing producer, independent source-free consumer, immutable
   release verification and Turso/Kusto projection parity. Explain every
   raw-module/declaration exclusion and lexical-only boundary. A statement
   corpus can intentionally contain only a small graph. Static references
   are not elaborated dependencies; keep their producer identity and existing
   evidence-class limits.
3. From the governed installed workbench, capture live `graph-search` and
   `context` against the candidate catalog. Each declared witness must resolve
   to the expected exact declaration, source path, artifact identity, revision
   and graph direction; a declared direct neighbor must actually be returned.
   Missing graph seeds, a Turso-only route, wrong artifact/counts or lost
   context fail the gate. Bounded traversal may report DEGRADED if the required
   neighbor is actually present; do not claim full traversal.
4. Commit the resulting receipt at `producer/coverage/<source-id>.json` with the
   catalog change. The existing workflow-lint job compares to the exact PR
   base and requires a receipt for each added/replaced entry. It independently
   downloads and attests the exact immutable release/assets, verifies the
   current producer fingerprint, checks raw-to-normalized declaration
   inventory, compares stored live witness evidence to verified graph counts,
   and verifies the required direct edges in that graph. An unchanged
   selected row cannot silently change its source/runner pins.
5. Review the intended scope and exclusions, not only the green CI label.
   Verify installed candidate parity again after merge as usual. A receipt is
   an observed workbench query record, not a new authority or an independent
   live cloud call by hosted CI. CI has no serving database credentials; the
   offline checks are independently recomputed, and the workbench performs
   the actual live probe before promotion.

## Plan and receipt

Create a JSON plan with these fields:

- `purpose`: research use and intended graph boundary.
- `producer_kind`: exact producer kind from the manifest.
- `required_modules`: nonempty list of exact module names that must actually
  have graph declarations. Include research-required imports even if absent
  from a prior extraction.
- `excluded_raw_modules`: exact raw module names mapped to nonempty reasons.
  Use an empty object for retaining all extracted modules.
- `excluded_raw_declarations`: exact `lean:<full-name>` identities mapped to
  reasons for intentional omissions within retained modules. For example, an
  archive source-binding policy may exclude generated declarations with no
  manifest-backed source evidence. Stale or unexplained exclusions fail.
- `lexical_boundary`: explanation of lexical-only files, which may provide
  broader context without claiming graph coverage.
- `witnesses`: nonempty array of objects with `declaration` (full Lean name
  without the lean prefix), `source_path`, `direction` (forward/reverse), and
  `neighbor` (exact full name of a direct graph neighbor without lean prefix).
  Null neighbor is allowed only for an isolated witness in that direction;
  if the graph contains edges, at least one witness must demonstrate an edge.
  Include the actual consuming-project witnesses when known.

Run from the candidate repository checkout with the installed, configured
workbench available. These commands do not create backend resources or
perform a release:

```sh
PYTHONPATH=.:src python3 scripts/check_corpus_selection.py record-live \
  --catalog producer/accepted-corpora.json --source-id "$SOURCE_ID" \
  --plan "$PLAN_FILE" --out "producer/coverage/$SOURCE_ID.json"
PYTHONPATH=.:src python3 scripts/check_corpus_selection.py check-pr --base "$BASE_SHA"
```

The second command rechecks the exact immutable package for changed entries.
For large packages run that CPU/memory-intensive verification on hosted CI
or an authorized Codespace, not the orchestration VM. Record-live only issues
bounded queries. The receipt is bound to the complete candidate catalog and
plan; re-capture after rebasing a catalog change.

## Limits

This is a selection gate, not a mathematical proof checker or a claim of
full upstream coverage. It rejects unexplained loss relative to raw evidence
and missing explicitly required modules/witnesses. It cannot discover every
declaration absent from the extractor, prove a chosen research scope is
sufficient, or prove all live rows independently from count/identity and
witness checks; existing exact projection parity and review remain required.
No universal ratio of lexical-to-graph files is imposed. Historical corpora
can be reviewed incrementally using the #46 audit, and replacements must pass
the new gate. Do not fabricate missing graph links or relabel source evidence.
