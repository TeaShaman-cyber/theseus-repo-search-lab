# theseus-repo-search-lab

## Purpose and scope (SEARCH-ONLY)

Theseus Repository Search is a fast, dependable retrieval tool for formalized
mathematical corpora, initially Lean. Given a question or declaration, it locates
existing theorem statements, declarations, dependency neighborhoods, and bounded
source context across accepted corpora, with exact provenance and evidence grades.
Statements with `sorry` are searchable. **The tool does not prove theorems,
certify proofs, or determine mathematical truth.**

### Mathematical research purpose

Repository Search exists to make the **formal structure of existing mathematics**
queryable for research. The useful object is the machine-readable mathematical
structure itself: theorem/declaration interfaces, proof dependencies, bounded
dependency cones, decompositions into subproblems, explicit reductions, preserved
invariants, bridge theorems, and mathematically justified structural
correspondences or isomorphisms. These structures can be compared across corpora
to generate and falsify candidate transfers to other mathematical problems. A
structural analogy is not a bridge unless its assumptions and evidence survive
that comparison.

Formalizations of difficult solved problems are therefore valuable **control and
reference corpora**. In particular, complete or substantial formalizations around
landmark results such as Fermat's Last Theorem and the Poincare theorem, plus
formalized Millennium Prize problem work where available, let the research layer
test whether decomposition, invariant, reduction, and bridge-retrieval methods
recover useful formal structure. Unsolved-problem corpora provide a separate test:
can the same lens expose bounded missing seams or candidate correspondences without
claiming a proof.

The historical human path by which a theorem was discovered is **not required**
for this research objective and is not a Repository Search acceptance input. It
may be retained as external historical context, but it must not replace or distort
the formal dependency/reduction structure extracted from the mathematical source.

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

### Durable Turso/libSQL projection

Accepted immutable releases remain corpus authority. A verified local SQLite/FTS5
projection can additionally be copied to Turso/libSQL so searchable state survives
loss of a workbench runtime without translating the query representation to
PostgreSQL.

Sync the full accepted catalog with one command:

```text
python3 -m theseus_repo_search sync-turso-catalog \
  --catalog producer/accepted-corpora.json \
  --cache-dir <path> \
  --organization <turso-organization> \
  --group <turso-group> \
  [--platform-base-url <url>] \
  [--platform-token-env <environment-variable>]
```

The command reuses the normal accepted-release verification and local projection
cache, creates missing Turso databases with `seed.type=database_upload`, uploads
the exact prepared SQLite file, verifies artifact identity, table/FTS counts,
SQLite integrity and page geometry, then enables delete protection. Existing
matching databases return `REUSED`; default database names are bound to the
accepted artifact identity so a newly accepted revision is published beside the
old immutable query copy rather than overwriting it. An identity or projection
mismatch fails closed. The JSON envelope reports every accepted corpus and explicit failures, so
a partial sync is `DEGRADED`, never silently complete.

`materialize-turso` provides the same path for one `--source-id`. Direct Turso API
use supplies a platform token through an environment variable, never a command
argument. A credential-injecting provider proxy may instead be selected with
`--platform-base-url` and no local platform token. Turso is a derived query copy:
it does not replace immutable accepted releases or the versioned accepted-corpus
catalog.

For Turso and MotherDuck resource ownership, retention and verified cleanup,
follow [cloud resource housekeeping](docs/cloud-resource-housekeeping.md).

### Read-only hosted search from the governed workbench

The same accepted catalog and retrieval logic can query the existing Turso
projections without downloading or rebuilding local SQLite files:

```text
python3 -m theseus_repo_search turso-search \
  --catalog producer/accepted-corpora.json \
  --organization <turso-organization> \
  --platform-base-url <governed-platform-proxy> \
  --query <text> --limit 20 --mode discovery
```

The workbench resolves identity-bound database names, obtains short-lived
read-only tokens and checks the accepted artifact identity before querying.
Both Git and archive projection schemas use the existing provenance reader.
Results use the same federated ranking, evidence/discovery semantics and explicit
partial-coverage envelope as local search. Query length is 1–512 characters and
result limit 1–50. No caller SQL, materialization, database writes or Lean build.
The platform proxy is a governed workbench credential, not an agent-facing
credential. This command is not yet the private MCP frontend in #123; that
frontend must have separately provisioned restricted credentials and verified
client/auth transport. Graph operations use `turso-graph` with the same catalog/organization/proxy,
`--source-id`, `--operation deps|rdeps|path|context`, `--name`, and `--depth`.
For a path, also supply `--target`. Traversal has an explicit examined-edge
budget (`--max-edges`, 1–500); context additionally caps that budget at 50 and
source text at `--token-budget` (1–4000 estimated tokens). An exhausted graph
budget returns `DEGRADED`, not a complete graph or a definitive no-path result.
Results bind the accepted source and artifact; no cross-corpus edge inference.

## Compiled-reference preview (not an accepted corpus)

For a verified `olean-export deps` pilot, `scripts/build_olean_preview.py`
consumes the original ZIP with an independently obtained SHA-256, validates its
member hashes and source/producer pins, reads the exact Git commit's source
blobs, and builds the ordinary Repo Search artifact and SQLite projection.
Run the script with `PYTHONPATH=src` and `--help` for the explicit inputs.
Use ordinary `search`, `deps`, `rdeps`, `path` and `context` on its index;
no Lean build or service deployment is needed for querying.

`COMPILED_REFERENCE` / `compiled_reference` denotes the upstream combined
compiled-reference relation, not an elaborated type or proof-value dependency.
The current one-node-per-name representation excludes all ambiguous name
occurrences from the graph; it never arbitrarily chooses a module. Raw
occurrences remain in the original verified pilot ZIP; the separate preview
receipt records the omissions and coverage. Source chunks remain searchable. Preview source associations are reciprocal
unique lexical candidates within the module, including competition from
graph-omitted names; they are not elaborated declaration ranges. Ambiguous
candidates remain unbound, and exact/context retrieval does not guess a source
location for an unbound node.
Unresolved references include references outside the emitted graph and must not
be interpreted as missing mathematical declarations. The preview producer
cannot claim authoritative source acceptance. Graph results always have
`complete_within_scope=false` and CLI status `DEGRADED`, even when a path is
found. It is not registered in the accepted catalog or published as a release.

Large CLI graph scopes are summarized to 32 displayed entries, with explicit
truncation, the original count, and SHA-256 of the canonical full module list.
The exact scope remains in the artifact/projection; the graph's actual
completeness signal is independent of display truncation.

## Live research smoke

After significant corpus acceptance, Repository Search can run a bounded versioned research-smoke panel on fresh hosted consumers, including release-backed consumers when the producer fingerprint is unchanged. Smoke scenarios ask for evidence classes and record grounded observations; they do not encode a desired mathematical conclusion.

Current v0 scenarios live under `qa/research-smoke/`. Search probes explicitly declare `query_mode`: broad `discovery` results remain candidate signals, while promotion-relevant target probes use `evidence` mode before they can contribute `FOUND_USEFUL_STRUCTURE`. Each run emits `theseus.repo-search-research-smoke-receipt.v1` with exact tool/artifact identity, probe evidence, observed states, regression flags, and `scientific_authority = NONE`.

After accepting a new corpus, select only relevant scenarios from `qa/research-smoke/` and run them on that corpus's fresh projection with `scripts/run_research_smoke.py`; do not run the full corpus producer matrix just for the smoke. The versioned `annals-marton-seam-v0.json` links to [Math #56](https://github.com/TeaShaman-cyber/theseus-math-research-lab/issues/56). Its first live source-specific [experimental hosted run #37768026926](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/actions/runs/37768026926) passed producer, source-free fresh consumer and research smoke on exact head `54cdadd8c336ecb2d359cf01d0872751b634a63d`, yielding `CORPUS_BOUNDARY`, `scientific_authority=NONE`. The Actions artifact is **transient**, not an immutable accepted-artifact release. Onboarding it to `main` does not itself publish a durable release or prove Marton. For unchanged producer fingerprints, reuse an existing accepted artifact on a source-free consumer rather than rerun the full producer matrix. An absent mathematical bridge is not a smoke regression.

Smoke dispositions are non-blocking evidence states, not mathematical verdicts. `regression_detected` records loss of a previously demonstrated capability without converting the smoke layer into scientific acceptance authority.

### Static workflow and shell checks

The normal `tools/dev/check` parses every POSIX endpoint in `tools/ci/` and
`tools/dev/` using `sh -n`. The hosted Workflow and shell analysis job runs
digest-pinned jactionlint, zizmor and ShellCheck. With the versions pinned in
`.github/workflows/workflow-lint.yml` installed on PATH, the same analysis can
run locally:

```sh
jactionlint --no-online
zizmor --offline --no-config --strict-collection .github/workflows
shellcheck tools/ci/* tools/dev/*
```

These tools replace generic syntax checks and add workflow correctness and
security diagnostics. Concurrency findings remain warnings: cancellation
policy for existing acceptance runs is not changed by linting. Keep
project-specific tests for exact workflow pins, accepted corpus matrices,
provenance, fail-closed behavior and producer/consumer phase boundaries.
The lint job does not change branch protection or accepted corpus policy.

### JSON lint

The hosted static analysis job also uses standalone Biome 2.6.6 with the
repository-specific `biome.json` profile based on Session Search
(`89a261a1092fd693b3ae95d05e69d1ed6fef037b`). No Node.js dependencies are
required. The JSON-only profile explicitly disallows comments and trailing
commas, empty keys, scalar roots and duplicate keys. Three individually
enabled nursery rules reject unsafe interoperation values (including integers
outside the IEEE-754 safe range), non-NFC object keys and a leading UTF-8 BOM.
These are policies for checked-in configuration/evidence JSON, not constraints
on arbitrary mathematical corpus data. Formatter and assist are disabled;
no blanket nursery preset or automatic rewriting is enabled. Review these
experimental rules when upgrading the pinned Biome version. Run `tools/ci/json-lint` with that version of Biome on PATH. It
checks all tracked JSON, treats warnings (including files skipped for size)
as failures, and does not reformat or modify files. The local dev check uses
this endpoint when Biome is installed; otherwise its original basic
fixture-syntax check remains available without additional dependencies.
The hosted job always executes the full Biome check.

Static lint is not runtime validation: Biome 2.6.6 misses equivalent
escaped object keys (for example `"a"` and `"\u0061"`). Keep strict runtime
parsing, provenance and domain-invariant tests. JSONL and downloaded corpus
artifacts are not part of this tracked-JSON lint scope.

## Unified agent query CLI (read-only)

Use the same command for corpus discovery, lexical search, dependency traversal,
paths and bounded source context. From a checked-out repository, set the
authorized connection profile once:

```sh
export REPO_SEARCH_TURSO_ORGANIZATION='<your organization>'
export REPO_SEARCH_TURSO_PLATFORM_URL=https://turso.int.exe.xyz
```

The proxy example is specific to an authorized exe.dev workbench with the
Turso integration attached. The launcher defaults to the checked-out accepted
catalog; override with `REPO_SEARCH_CATALOG`. Other environments can configure
the official API URL and `REPO_SEARCH_TURSO_TOKEN_ENV` (the **name** of an
environment variable containing a platform token). Do not paste tokens into
arguments, logs or source. This workbench route is not an agent-facing
credential boundary for untrusted remote clients.

```sh
./tools/repo-search query corpora
./tools/repo-search query search 'spectral measure' --limit 5
./tools/repo-search query search 'coeffMoments_atV' --source zeta23
./tools/repo-search query deps zeta23 Zeta23.XiPrime.coeffMoments_atV
./tools/repo-search query rdeps zeta23 Zeta23.XiPrime.GzMoments
./tools/repo-search query path zeta23 Zeta23.XiPrime.coeffMoments_atV Zeta23.XiPrime.CoeffMoments
./tools/repo-search query context zeta23 Zeta23.XiPrime.coeffMoments_atV --token-budget 2000
```

All results are JSON. Search hits carry accepted artifact identities alongside
source IDs and provenance. `corpora` lists the catalog, not live availability;
search reports unavailable corpora and distinguishes partial coverage from
zero hits. `--source` restricts search to one accepted corpus without changing
the catalog identity. Graph operations always name a corpus; they preserve
the existing bounded graph semantics and do not infer cross-corpus edges.
Use the existing `discovery` (OR) or `evidence` (AND) search mode.
A successful process exit does not imply a complete result: inspect `status`
and coverage fields. Profile flags may precede the operation, for example
`query --catalog /path/catalog.json corpora`.

This interface uses existing Turso read-only data-plane queries. Local SQLite
commands remain available. Azure SQL and Kusto pilots are not accepted serving
projections; no live Kusto or SQL dependency is added here. MCP hosting,
declaration grouping and feedback remain separate work (#123, #159, #162).

### Kusto graph analytics in the same CLI

On the authorized exe.dev workbench configure the existing ADX cluster and
database in the same environment profile:

```sh
export REPO_SEARCH_KUSTO_CLUSTER='https://<cluster>.<region>.kusto.windows.net'
export REPO_SEARCH_KUSTO_DATABASE=repository_search
./tools/repo-search query reach zeta23 Zeta23.XiPrime.coeffMoments_atV --depth 3 --limit 100
./tools/repo-search query reach zeta23 Zeta23.XiPrime.coeffMoments_atV --reverse
```

Use the exact declaration ID from search. Reach returns unique reachable
declarations and their shortest distance (excluding the root), with depth
1–3 and limit 1–500. It uses ADX graph-match, not a client-side walk.
The result carries accepted artifact identity, revision and dependency
boundary; result completeness is limited to that indexed graph and depth.
It does not certify a proof. Existing deps/rdeps/path/context still return
typed evidence via Turso. ADX uses the governed az-owner workbench wrapper;
this is not a least-privilege external serving credential.

The first selected ADX projection is Zeta23 (6046 nodes, 46563 typed edges).
Other corpora without a verified selected ADX projection fail explicitly;
there is no silent backend switch. An identity-specific ready table is
written only after exact row parity with the accepted local projection.
The read-only query checks the ready record, artifact identity and current
row counts (not a full content audit on every request). For materialization,
verify the immutable release, rebuild and verify its projection with the
existing consumer, record resource ownership in an Issue, then use
`scripts/materialize_kusto_graph.py` with the independently established
projection fingerprint. Do not overwrite existing tables; reconcile an
interrupted materialization before retry. The ready record is not corpus
authority. No new cluster or database is required.
