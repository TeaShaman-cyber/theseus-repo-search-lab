# Repository Lens Bootstrap v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first reproducible repository-lens pipeline that turns a pinned Lean repository snapshot plus LeanDepViz output into a normalized artifact, a disposable SQLite/FTS projection, and bounded JSON retrieval commands usable without Lean at query time.

**Architecture:** Heavy Lean elaboration stays in a producer runtime; the stable boundary is a versioned normalized artifact owned by this repository. A lightweight Python 3.11 consumer validates that artifact, builds a deterministic logical SQLite projection, and serves lexical and graph queries with explicit provenance, evidence grades, and scope boundaries.

**Tech Stack:** Python 3.11 standard library (`dataclasses`, `enum`, `hashlib`, `json`, `sqlite3`, `argparse`, `pathlib`, `unittest`); SQLite FTS5; GitHub Actions; pinned Lean/Elan/LeanDepViz only in the producer workflow.

**Spec:** `docs/superpowers/specs/2026-09-16-repository-lens-design.md`

## Global Constraints

- Git repository at an exact commit remains authority; every index/graph/database is a rebuildable projection.
- Runtime consumer must not require Lean to search or traverse an already-produced artifact.
- v1 dependency boundary is `internal_only`; omitted Mathlib/Lean/external dependencies are outside scope, never evidence of absence.
- Normalized Lean edge orientation is `dependent -> dependency`.
- Evidence grades are exactly `ELABORATED_VALUE_DEPENDENCY`, `ELABORATED_TYPE_DEPENDENCY`, `STATIC_REFERENCE`, and `LEXICAL_HIT`; weaker evidence is never promoted.
- No hosted service, mandatory embeddings, vector database, Prove2Me token, or generic multi-language abstraction in bootstrap v1.
- No runtime Python dependencies beyond the standard library; tests run with `python -m unittest`.
- Graph traversal is bounded; CLI default depth is `1`, and graph commands reject depth values greater than `5`.
- `context` uses a documented deterministic token estimate `ceil(len(text) / 4)`; it does not claim tokenizer-exact accounting.
- Public-repository baseline operation requires no hosted credentials.

---

## File Structure

```text
pyproject.toml
src/theseus_repo_search/
    __init__.py          package version and public exports
    __main__.py          `python -m theseus_repo_search` entry point
    model.py             artifact/query dataclasses and evidence enums
    errors.py            stable error codes and CLI exit mapping
    normalize.py         LeanDepViz -> normalized Node/Edge conversion
    sources.py           Lean source chunk extraction for lexical evidence
    artifact.py          deterministic JSONL/manifest write, load, hash, verify
    projection.py        disposable SQLite schema + FTS5 rebuild
    graph.py             bounded dependency/reverse/path traversal
    retrieval.py         exact-name, lexical search, and bounded context assembly
    cli.py               JSON-first CLI surface
scripts/
    replay_zeta23.py     pinned acceptance replay and baseline measurements
    consume_artifact_smoke.py
producer/
    zeta23.json          exact source/extractor/toolchain pins
.github/workflows/
    zeta23-producer-smoke.yml

tests/
    fixtures/
        raw_leandepviz.json
        lean_src/Zeta23/Tiny.lean
    test_model.py
    test_normalize.py
    test_sources.py
    test_artifact.py
    test_projection.py
    test_graph.py
    test_retrieval.py
    test_cli.py
```

Normalized artifact members:

```text
manifest.json
nodes.jsonl
edges.jsonl
sources.jsonl        # optional by contract, present in Zeta23 producer
```

`index.sqlite` is disposable and never part of artifact identity.

---

### Task 1: Package Skeleton, Evidence Types, and Stable Errors

**Files:**
- Create: `pyproject.toml`
- Create: `src/theseus_repo_search/__init__.py`
- Create: `src/theseus_repo_search/model.py`
- Create: `src/theseus_repo_search/errors.py`
- Create: `tests/test_model.py`

**Interfaces:**
- Produces `EvidenceGrade`, `Node`, `Edge`, `SourceChunk`, `ArtifactScope`, `ProducerPin`, `ArtifactManifest`, and `RepoSearchError`.

- [ ] **Step 1: Write the failing model tests**

```python
import unittest
from theseus_repo_search.errors import RepoSearchError
from theseus_repo_search.model import Edge, EvidenceGrade, Node

class ModelTests(unittest.TestCase):
    def test_evidence_grades_are_stable_strings(self):
        self.assertEqual(EvidenceGrade.ELABORATED_VALUE_DEPENDENCY.value,
                         "ELABORATED_VALUE_DEPENDENCY")
        self.assertEqual(EvidenceGrade.LEXICAL_HIT.value, "LEXICAL_HIT")

    def test_node_lean_id_uses_canonical_full_name(self):
        node = Node.from_lean(full_name="Zeta23.Tiny.a", name="a", kind="thm",
                              module="Zeta23.Tiny", source_commit="abc123")
        self.assertEqual(node.id, "lean:Zeta23.Tiny.a")
        self.assertIsNone(node.source_start_line)

    def test_repo_search_error_keeps_machine_code(self):
        err = RepoSearchError("BLOCKED_ARTIFACT_INTEGRITY", "hash mismatch")
        self.assertEqual(err.code, "BLOCKED_ARTIFACT_INTEGRITY")
        self.assertEqual(str(err), "hash mismatch")

    def test_edge_serializes_grade_as_string(self):
        edge = Edge(source_id="lean:A", target_id="lean:B",
                    relation="value_dependency",
                    evidence_grade=EvidenceGrade.ELABORATED_VALUE_DEPENDENCY,
                    producer="LeanDepViz@deadbeef")
        self.assertEqual(edge.to_dict()["evidence_grade"],
                         "ELABORATED_VALUE_DEPENDENCY")
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
PYTHONPATH=src python -m unittest tests.test_model -v
```

Expected: `ModuleNotFoundError` for `theseus_repo_search`.

- [ ] **Step 3: Add package metadata and model types**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "theseus-repo-search"
version = "0.1.0"
description = "Reproducible repository evidence, graph traversal, and bounded LLM retrieval."
requires-python = ">=3.11"
dependencies = []

[project.scripts]
repo-search = "theseus_repo_search.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

Required Python signatures:

```python
class EvidenceGrade(str, Enum): ...

@dataclass(frozen=True)
class Node:
    id: str
    name: str
    kind: str
    module: str
    source_path: str | None
    source_start_line: int | None
    source_end_line: int | None
    source_commit: str
    @classmethod
    def from_lean(cls, *, full_name: str, name: str, kind: str,
                  module: str, source_commit: str) -> "Node": ...
    def to_dict(self) -> dict[str, object]: ...

@dataclass(frozen=True)
class Edge:
    source_id: str
    target_id: str
    relation: str
    evidence_grade: EvidenceGrade
    producer: str
    def to_dict(self) -> dict[str, object]: ...

@dataclass(frozen=True)
class SourceChunk:
    id: str
    source_commit: str
    source_path: str
    source_start_line: int
    source_end_line: int
    declaration_hint: str | None
    text: str
    content_sha256: str
    def to_dict(self) -> dict[str, object]: ...

@dataclass(frozen=True)
class ArtifactScope:
    root_modules: tuple[str, ...]
    dependency_boundary: str

@dataclass(frozen=True)
class ProducerPin:
    kind: str
    tool_repo: str
    tool_commit: str
    tool_hash: str

@dataclass(frozen=True)
class ArtifactManifest:
    schema: str
    source_repo: str
    source_commit: str
    source_subdir: str
    producer: ProducerPin
    scope: ArtifactScope
    nodes_sha256: str
    edges_sha256: str
    sources_sha256: str | None
    nodes_count: int
    edges_count: int
    created_from_authoritative_commit: bool
    def to_dict(self) -> dict[str, object]: ...
    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ArtifactManifest": ...
```

`errors.py`:

```python
class RepoSearchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
```

- [ ] **Step 4: Run the model tests**

```bash
PYTHONPATH=src python -m unittest tests.test_model -v
```

Expected: `4 tests ... OK`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/theseus_repo_search tests/test_model.py
git commit -m "feat: define repository artifact model"
```

---

### Task 2: Normalize Observed LeanDepViz JSON Without Overclaiming Scope

**Files:**
- Create: `src/theseus_repo_search/normalize.py`
- Create: `tests/fixtures/raw_leandepviz.json`
- Create: `tests/test_normalize.py`

**Interfaces:**

```python
def normalize_leandepviz(raw: dict[str, object], *, source_commit: str,
                         root_modules: tuple[str, ...], producer_ref: str
                         ) -> tuple[list[Node], list[Edge]]
```

Native LeanDepViz is observed as `source=dependency`, `target=dependent`; normalized v1 must invert it.

- [ ] **Step 1: Add the observed-shape fixture and failing tests**

```json
{
  "nodes": [
    {"name":"a","module":"Zeta23.Tiny","kind":"thm","fullName":"Zeta23.Tiny.a"},
    {"name":"b","module":"Zeta23.Tiny","kind":"thm","fullName":"Zeta23.Tiny.b"},
    {"name":"external","module":"Mathlib.Tiny","kind":"thm","fullName":"Mathlib.Tiny.external"}
  ],
  "edges": [
    {"source":"Zeta23.Tiny.a","target":"Zeta23.Tiny.b","kind":"value"},
    {"source":"Mathlib.Tiny.external","target":"Zeta23.Tiny.b","kind":"type"}
  ]
}
```

Assertions:

```python
nodes, edges = normalize_leandepviz(raw, source_commit="abc123",
                                    root_modules=("Zeta23",),
                                    producer_ref="LeanDepViz@7859d91")
self.assertEqual([n.id for n in nodes],
                 ["lean:Zeta23.Tiny.a", "lean:Zeta23.Tiny.b"])
self.assertEqual(len(edges), 1)
self.assertEqual(edges[0].source_id, "lean:Zeta23.Tiny.b")
self.assertEqual(edges[0].target_id, "lean:Zeta23.Tiny.a")
self.assertEqual(edges[0].relation, "value_dependency")
self.assertEqual(edges[0].evidence_grade,
                 EvidenceGrade.ELABORATED_VALUE_DEPENDENCY)
```

Also test deterministic ordering and duplicate-edge removal.

- [ ] **Step 2: Verify failure**

```bash
PYTHONPATH=src python -m unittest tests.test_normalize -v
```

- [ ] **Step 3: Implement the observed LeanDepViz shape only**

```python
RELATION_BY_KIND = {
    "type": ("type_dependency", EvidenceGrade.ELABORATED_TYPE_DEPENDENCY),
    "value": ("value_dependency", EvidenceGrade.ELABORATED_VALUE_DEPENDENCY),
}
```

Keep only nodes whose module equals a root module or starts with `<root>.`; keep edges only when both endpoints survive. Unknown edge kinds raise:

```python
RepoSearchError("BLOCKED_ARTIFACT_INTEGRITY",
                f"unknown LeanDepViz edge kind: {kind}")
```

Sort nodes by `id`; sort edges by `(source_id, target_id, relation, producer)`.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src python -m unittest tests.test_normalize -v
```

- [ ] **Step 5: Commit**

```bash
git add src/theseus_repo_search/normalize.py tests/fixtures/raw_leandepviz.json tests/test_normalize.py
git commit -m "feat: normalize LeanDepViz dependency edges"
```

---

### Task 3: Extract Lexical Source Chunks With Exact File/Line Provenance

**Files:**
- Create: `src/theseus_repo_search/sources.py`
- Create: `tests/fixtures/lean_src/Zeta23/Tiny.lean`
- Create: `tests/test_sources.py`

**Interfaces:**

```python
def scan_lean_sources(source_root: Path, *, source_commit: str) -> list[SourceChunk]
```

Source chunks are lexical evidence only.

- [ ] **Step 1: Write fixture and failing tests**

```lean
namespace Zeta23.Tiny

/-- Equality-family witness for rank trace tightness. -/
lemma lemmaR_tight_two : True := by
  trivial

/-- Moment lower bound used by the certificate. -/
theorem N0star_lower_moment : True := by
  trivial

end Zeta23.Tiny
```

Assertions:

```python
chunks = scan_lean_sources(FIXTURE_ROOT, source_commit="abc123")
self.assertEqual(len(chunks), 2)
self.assertEqual(chunks[0].declaration_hint, "lemmaR_tight_two")
self.assertEqual(chunks[0].source_path, "Zeta23/Tiny.lean")
self.assertIn("rank trace tightness", chunks[0].text)
self.assertEqual(chunks[0].content_sha256,
                 sha256(chunks[0].text.encode()).hexdigest())
```

- [ ] **Step 2: Verify failure**

```bash
PYTHONPATH=src python -m unittest tests.test_sources -v
```

- [ ] **Step 3: Implement a deliberately small declaration chunker**

```python
DECL_RE = re.compile(
    r"^\s*(?:protected\s+|private\s+|noncomputable\s+|unsafe\s+)*"
    r"(?:theorem|lemma|def|abbrev|structure|class|inductive|instance)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_'\.]*)"
)
```

Rules:
1. Walk `*.lean` in sorted relative-path order.
2. Include immediately preceding comments/blank lines in the declaration chunk.
3. End at the next declaration start or EOF.
4. Store 1-based inclusive ranges.
5. Ignore files with no recognized declaration.
6. ID: `src:<relative_path>:<start_line>:<end_line>`.
7. Sort `(source_path, source_start_line, id)`.
8. State in module docstring that this is a lexical chunker, not a Lean parser.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src python -m unittest tests.test_sources -v
```

- [ ] **Step 5: Commit**

```bash
git add src/theseus_repo_search/sources.py tests/fixtures/lean_src tests/test_sources.py
git commit -m "feat: extract Lean lexical source chunks"
```

---

### Task 4: Deterministic Artifact Writer, Identity, Loader, and Integrity Verification

**Files:**
- Create: `src/theseus_repo_search/artifact.py`
- Create: `tests/test_artifact.py`

**Interfaces:**

```python
def write_artifact(out_dir: Path, *, nodes: Sequence[Node], edges: Sequence[Edge],
                   sources: Sequence[SourceChunk] | None,
                   source_repo: str, source_commit: str, source_subdir: str,
                   producer: ProducerPin, scope: ArtifactScope,
                   created_from_authoritative_commit: bool) -> ArtifactManifest

def load_artifact(path: Path) -> tuple[ArtifactManifest, list[Node], list[Edge], list[SourceChunk]]

def artifact_identity(manifest: ArtifactManifest) -> str
```

- [ ] **Step 1: Write failing deterministic/integrity tests**

```python
m1 = write_artifact(tmp1, ...)
m2 = write_artifact(tmp2, ...)
self.assertEqual((tmp1 / "nodes.jsonl").read_bytes(),
                 (tmp2 / "nodes.jsonl").read_bytes())
self.assertEqual((tmp1 / "edges.jsonl").read_bytes(),
                 (tmp2 / "edges.jsonl").read_bytes())
self.assertEqual(artifact_identity(m1), artifact_identity(m2))
```

Tamper test:

```python
(tmp1 / "edges.jsonl").write_text("{}\n", encoding="utf-8")
with self.assertRaises(RepoSearchError) as ctx:
    load_artifact(tmp1)
self.assertEqual(ctx.exception.code, "BLOCKED_ARTIFACT_INTEGRITY")
```

- [ ] **Step 2: Verify failure**

```bash
PYTHONPATH=src python -m unittest tests.test_artifact -v
```

- [ ] **Step 3: Implement canonical serialization and verification**

```python
def _json_line(data: dict[str, object]) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")
```

Schema string: `theseus.repo-index.v1`.

`artifact_identity()` hashes canonical JSON of only:
- schema;
- source repo/commit/subdir;
- producer kind/tool_repo/tool_commit/tool_hash;
- scope root_modules/dependency_boundary;
- member nodes/edges/sources hashes.

Do not include timestamps.

`load_artifact()` verifies schema, member hashes, counts, node source commits, edge endpoints, and `internal_only`. Failure => `BLOCKED_ARTIFACT_INTEGRITY`.

- [ ] **Step 4: Run artifact and full tests**

```bash
PYTHONPATH=src python -m unittest tests.test_artifact -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

- [ ] **Step 5: Commit**

```bash
git add src/theseus_repo_search/artifact.py tests/test_artifact.py
git commit -m "feat: add deterministic repository artifact contract"
```

---

### Task 5: Build a Disposable SQLite Projection With FTS5

**Files:**
- Create: `src/theseus_repo_search/projection.py`
- Create: `tests/test_projection.py`

**Interfaces:**

```python
def build_projection(artifact_dir: Path, db_path: Path) -> str

def projection_fingerprint(db_path: Path) -> str
```

- [ ] **Step 1: Write failing projection tests**

```python
fp1 = build_projection(artifact_dir, db1)
fp2 = build_projection(artifact_dir, db2)
self.assertEqual(fp1, fp2)
self.assertEqual(projection_fingerprint(db1), fp1)
```

Verify expected node/edge/source rows and a working FTS `MATCH` query.

- [ ] **Step 2: Verify failure**

```bash
PYTHONPATH=src python -m unittest tests.test_projection -v
```

- [ ] **Step 3: Implement projection schema**

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE nodes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  module TEXT NOT NULL,
  source_path TEXT,
  source_start_line INTEGER,
  source_end_line INTEGER,
  source_commit TEXT NOT NULL
);
CREATE TABLE edges (
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  evidence_grade TEXT NOT NULL,
  producer TEXT NOT NULL,
  PRIMARY KEY (source_id, target_id, relation, producer)
);
CREATE INDEX edges_target_idx ON edges(target_id);
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  source_commit TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_start_line INTEGER NOT NULL,
  source_end_line INTEGER NOT NULL,
  declaration_hint TEXT,
  text TEXT NOT NULL,
  content_sha256 TEXT NOT NULL
);
CREATE VIRTUAL TABLE sources_fts USING fts5(
  declaration_hint,
  text,
  content='sources',
  content_rowid='rowid',
  tokenize='unicode61'
);
```

Populate one transaction, then:

```sql
INSERT INTO sources_fts(sources_fts) VALUES('rebuild');
```

Store artifact identity, source commit, root modules JSON, and boundary in `meta`. Fingerprint canonical ordered logical rows, never SQLite page bytes. Missing FTS5 => `UNAVAILABLE_FTS5`.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src python -m unittest tests.test_projection -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

- [ ] **Step 5: Commit**

```bash
git add src/theseus_repo_search/projection.py tests/test_projection.py
git commit -m "feat: build deterministic SQLite repository projection"
```

---

### Task 6: Bounded Graph Traversal With Scope Metadata

**Files:**
- Create: `src/theseus_repo_search/graph.py`
- Create: `tests/test_graph.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class GraphResult:
    query: str
    edges: tuple[dict[str, object], ...]
    scope_root_modules: tuple[str, ...]
    dependency_boundary: str
    complete_within_scope: bool

def dependencies(db_path: Path, name: str, *, depth: int = 1) -> GraphResult

def reverse_dependencies(db_path: Path, name: str, *, depth: int = 1) -> GraphResult

def path(db_path: Path, source: str, target: str, *, max_depth: int = 5) -> GraphResult
```

- [ ] **Step 1: Write failing traversal tests**

Fixture graph:

```text
A -> B -> C
 \-> D -> C
C -> A
```

Assert depth bounds, cycle safety, reverse traversal, shortest path, `internal_only`, and `complete_within_scope=True`. `depth=6` must raise `RepoSearchError("UNKNOWN", "depth exceeds v1 maximum of 5")`.

- [ ] **Step 2: Verify failure**

```bash
PYTHONPATH=src python -m unittest tests.test_graph -v
```

- [ ] **Step 3: Implement deterministic BFS**

Expand frontier ordered by `(relation, target_id, source_id)`. `dependencies` follows outgoing normalized edges; `reverse_dependencies` uses `edges_target_idx`. Name resolution accepts exact `lean:<fullName>` or an unambiguous short name/full-name suffix. Ambiguous names => `UNKNOWN` with candidate IDs.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src python -m unittest tests.test_graph -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

- [ ] **Step 5: Commit**

```bash
git add src/theseus_repo_search/graph.py tests/test_graph.py
git commit -m "feat: add bounded dependency graph traversal"
```

---

### Task 7: Exact-Identifier and Lexical Retrieval, Then Bounded Context Assembly

**Files:**
- Create: `src/theseus_repo_search/retrieval.py`
- Create: `tests/test_retrieval.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class SearchHit:
    declaration_id: str | None
    declaration_hint: str | None
    source_commit: str
    source_path: str | None
    source_start_line: int | None
    source_end_line: int | None
    evidence_grade: EvidenceGrade
    score: float
    text: str | None

def search(db_path: Path, query: str, *, limit: int = 10) -> list[SearchHit]

def context(db_path: Path, name: str, *, depth: int = 1,
            token_budget: int = 4000) -> dict[str, object]
```

- [ ] **Step 1: Write failing retrieval tests**

```python
hits = search(db, "lemmaR_tight_two")
self.assertEqual(hits[0].declaration_hint, "lemmaR_tight_two")

hits = search(db, "rank trace tightness")
self.assertEqual(hits[0].declaration_hint, "lemmaR_tight_two")
self.assertEqual(hits[0].evidence_grade, EvidenceGrade.LEXICAL_HIT)

result = context(db, "lemmaR_tight_two", depth=1, token_budget=80)
self.assertEqual(result["dependency_boundary"], "internal_only")
self.assertLessEqual(result["estimated_tokens"], 80)
self.assertEqual(result["token_estimate_method"], "ceil(chars/4)")
```

- [ ] **Step 2: Verify failure**

```bash
PYTHONPATH=src python -m unittest tests.test_retrieval -v
```

- [ ] **Step 3: Implement exact preference + safe FTS query**

Exact-name resolution order: `nodes.id`, full-name suffix, then unambiguous `nodes.name`.

Lexical terms:

```python
terms = re.findall(r"[A-Za-z0-9_]+", query)
fts_query = " OR ".join('"' + term + '"' for term in terms)
```

SQL:

```sql
SELECT s.*, bm25(sources_fts) AS rank
FROM sources_fts
JOIN sources s ON s.rowid = sources_fts.rowid
WHERE sources_fts MATCH ?
ORDER BY rank, s.source_path, s.source_start_line
LIMIT ?
```

No-hit returns `[]`; CLI maps it to `UNKNOWN` semantics.

`context()` resolves target, gathers target chunk, bounded graph neighbors, neighbor chunks, sorts by graph distance then path/line, and stops before `ceil(total_chars/4)` exceeds budget. Include scope/boundary and `complete_within_scope`.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src python -m unittest tests.test_retrieval -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

- [ ] **Step 5: Commit**

```bash
git add src/theseus_repo_search/retrieval.py tests/test_retrieval.py
git commit -m "feat: add lexical retrieval and bounded context assembly"
```

---

### Task 8: JSON-First CLI and End-to-End Artifact Build Command

**Files:**
- Create: `src/theseus_repo_search/cli.py`
- Create: `src/theseus_repo_search/__main__.py`
- Create: `tests/test_cli.py`

**Interfaces:**

```text
repo-search build-artifact
repo-search verify-artifact
repo-search build-index
repo-search search
repo-search deps
repo-search rdeps
repo-search path
repo-search context
```

Successful query commands emit one JSON object to stdout. Machine errors emit one JSON object to stderr:

```json
{"status":"ERROR","code":"BLOCKED_ARTIFACT_INTEGRITY","message":"..."}
```

- [ ] **Step 1: Write failing CLI tests**

Use `subprocess.run([sys.executable, "-m", "theseus_repo_search", ...], env={...}, capture_output=True, text=True)`.

Assert:
- `build-artifact` writes four members;
- `verify-artifact` returns `status=VERIFIED`;
- `build-index` returns a fingerprint;
- `deps` returns boundary metadata;
- search no-hit exits `0` with `status=UNKNOWN` and empty hits;
- tampered artifact exits non-zero with `BLOCKED_ARTIFACT_INTEGRITY`.

- [ ] **Step 2: Verify failure**

```bash
PYTHONPATH=src python -m unittest tests.test_cli -v
```

- [ ] **Step 3: Implement parser and orchestration**

`build-artifact` requires:

```text
--raw-depgraph PATH
--source-root PATH
--source-repo OWNER/REPO
--source-commit 40_HEX_SHA
--source-subdir PATH
--root-module MODULE        repeatable
--extractor-repo OWNER/REPO
--extractor-commit 40_HEX_SHA
--extractor-hash 64_HEX_SHA256
--authoritative-readback
--out PATH
```

It performs only `read raw graph -> normalize -> scan lexical sources -> write artifact`; it never invokes Lean, Git clone, or network fetch.

Graph depth defaults `1` and rejects `>5`.

`__main__.py`:

```python
from .cli import main
raise SystemExit(main())
```

- [ ] **Step 4: Install editable package and run all tests**

```bash
python -m pip install -e .
python -m unittest tests.test_cli -v
python -m unittest discover -s tests -v
repo-search --help
```

- [ ] **Step 5: Commit**

```bash
git add src/theseus_repo_search/cli.py src/theseus_repo_search/__main__.py tests/test_cli.py
git commit -m "feat: expose repository lens CLI"
```

---

### Task 9: Pin the Zeta23 Producer and Recreate the Verified Exact-Graph Boundary Here

**Files:**
- Create: `producer/zeta23.json`
- Create: `.github/workflows/zeta23-producer-smoke.yml`
- Create: `scripts/replay_zeta23.py`

**Interfaces:**
- Produces GitHub artifact `zeta23-repo-index-v1` containing normalized artifact members plus `replay.json`; raw graph and SQLite DB are not uploaded as authority.

- [ ] **Step 1: Add exact producer pins**

```json
{
  "source_repo": "anthropics/formal-math",
  "source_commit": "fbdc36bbf17d20af3fd0447c6d1a8a02773c9844",
  "source_subdir": "zeta23",
  "root_module": "Zeta23",
  "extractor_repo": "cameronfreer/LeanDepViz",
  "extractor_commit": "7859d91f89b37c9193d9a09709a58be6d98f94d6",
  "extractor_main_sha256": "7051af16c579dc425859442bbe7b647eaea78a581ddf685c5951c2b7bf28ceed",
  "elan_version": "v4.2.3",
  "elan_sha256": "df0b2b3a439961ffcbb3985214365ffe40f49bc871df04dff268c7d8e21ca8b2"
}
```

- [ ] **Step 2: Write replay script before workflow**

Registered graph assertions:

```python
assert "lean:Zeta23.ZeroSide.TightMult.lemmaR_tight" in deps_of_lemmaR_tight_two
assert "lean:Zeta23.ZeroSide.RankTraceMult.rank_trace_mult_k_le" in reverse_of_rank_trace_mult_k
assert "lean:Zeta23.Assembly.N0star_lower_moment" in ancestors_of_count_certificate
```

Lexical assertions:

```python
assert top10_contains("rank trace tightness", "lemmaR_tight_two")
assert top10_contains("Chebyshev Mertens", "ChebyshevMertens")
```

Measure a broad Python baseline using any case-insensitive token from `certificate`, `trace`, `Frobenius`, `moment` across `.lean` files. Record baseline unique-path count and top-10 FTS unique-path count in `replay.json`; require only:

```python
assert fts_unique_paths <= 10
assert baseline_unique_paths > fts_unique_paths
```

Do not hardcode historical `152` as correctness.

- [ ] **Step 3: Add producer workflow**

Triggers:

```yaml
on:
  workflow_dispatch:
  pull_request:
    paths:
      - 'src/**'
      - 'scripts/replay_zeta23.py'
      - 'producer/zeta23.json'
      - '.github/workflows/zeta23-producer-smoke.yml'
```

Job:
1. checkout this repository;
2. read pin JSON;
3. checkout target exact commit into `_target/formal-math`;
4. require `git rev-parse HEAD == source_commit`;
5. install pinned Elan and verify SHA-256;
6. run `lake exe cache get` and `lake build` in `zeta23`;
7. fetch pinned `LeanDepViz/Main.lean`, verify SHA-256;
8. run extractor with root `Zeta23` to `_out/raw-depgraph.json`;
9. `python -m pip install -e .`;
10. `repo-search build-artifact ... --authoritative-readback --out _out/artifact`;
11. verify artifact;
12. build `_out/index.sqlite`;
13. run replay to `_out/replay.json`;
14. remove raw graph and SQLite before upload;
15. upload `zeta23-repo-index-v1`.

Permissions: `contents: read`. No secrets.

- [ ] **Step 4: Run local non-Lean checks**

```bash
python -m unittest discover -s tests -v
python -m json.tool producer/zeta23.json >/dev/null
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/zeta23-producer-smoke.yml"); puts "yaml_syntax_ok"'
```

GitHub Actions remains authoritative for workflow semantics.

- [ ] **Step 5: Commit**

```bash
git add producer/zeta23.json .github/workflows/zeta23-producer-smoke.yml scripts/replay_zeta23.py
git commit -m "feat: add pinned Zeta23 artifact producer"
```

- [ ] **Step 6: Require green producer smoke before merge**

Observed evidence must include exact source readback, exact extractor hash, `verify-artifact=VERIFIED`, replay PASS, and artifact upload. Green CI alone does not satisfy Task 10.

---

### Task 10: Independent Lean-Free Consumer Smoke and Bootstrap Acceptance Receipt

**Files:**
- Create: `scripts/consume_artifact_smoke.py`
- Modify: `README.md`
- Update: GitHub Issue #1 only after observed success.

**Interfaces:**
- `consume_artifact_smoke.py --artifact-dir PATH --require-lean-absent` rebuilds projection and answers registered graph/lexical queries without Lean.

- [ ] **Step 1: Write consumer smoke**

If `--require-lean-absent` and `shutil.which("lean")` is not `None`, exit non-zero.

Then:

```python
manifest, *_ = load_artifact(artifact_dir)
with TemporaryDirectory() as td:
    db = Path(td) / "index.sqlite"
    build_projection(artifact_dir, db)
    # run registered graph and lexical queries
```

Emit JSON containing:

```text
status=VERIFIED
artifact_identity
source_repo
source_commit
producer.tool_repo
producer.tool_commit
scope.root_modules
scope.dependency_boundary
projection_fingerprint
graph_replay_passed=true
lexical_replay_passed=true
lean_available=false
```

- [ ] **Step 2: Add minimal README consumer flow**

```bash
python -m pip install -e .
repo-search verify-artifact ./artifact
repo-search build-index ./artifact ./index.sqlite
repo-search search ./index.sqlite 'rank trace tightness'
repo-search deps ./index.sqlite Zeta23.ZeroSide.TightMult.lemmaR_tight_two --depth 1
```

README must state:

```text
Git snapshot = authority.
Artifact/index = reproducible projection.
Search hit = candidate evidence, not proof of absence.
```

- [ ] **Step 3: Run all local tests**

```bash
python -m unittest discover -s tests -v
```

- [ ] **Step 4: Download successful GitHub artifact into MarcoPolo and verify independently**

1. read GitHub artifact digest;
2. download ZIP;
3. require local ZIP SHA-256 equals GitHub digest;
4. unzip cleanly;
5. remove `.elan/bin` from `PATH`;
6. run:

```bash
python scripts/consume_artifact_smoke.py \
  --artifact-dir /path/to/unpacked/artifact \
  --require-lean-absent
```

Expected: `status=VERIFIED`, `lean_available=false`, both replay booleans true.

- [ ] **Step 5: Record Issue #1 acceptance receipt with observed values only**

Record source commit, extractor commit/hash, GitHub run ID, artifact ID/digest, artifact identity, node/edge/source counts, projection fingerprint, graph results, lexical ranks/paths, and Lean-free result. Unknown values stay `UNKNOWN`.

- [ ] **Step 6: Commit smoke/documentation**

```bash
git add scripts/consume_artifact_smoke.py README.md
git commit -m "docs: add Lean-free repository lens smoke"
```

- [ ] **Step 7: Final verification before closing bootstrap**

```bash
python -m unittest discover -s tests -v
git status --short
```

Independently read back merged implementation commit, Issue #1 receipt, and artifact metadata/digest. Close Issue #1 only after all match; a merged PR alone is insufficient.

---

## Executor Self-Review Checklist

```text
[ ] Git commit remains authority; no DB/artifact is called canonical source.
[ ] Raw LeanDepViz orientation is inverted exactly once.
[ ] internal_only scope is visible in every graph response.
[ ] Missing external edges are never represented as absence.
[ ] Evidence grades survive artifact -> SQLite -> JSON unchanged.
[ ] Artifact member hashes and identity are deterministic.
[ ] SQLite file bytes are not used as deterministic identity.
[ ] Query-time path works without Lean.
[ ] No hosted credentials are required.
[ ] No embeddings/PageRank/multi-language framework slipped into v1.
[ ] All recursion is bounded at depth <= 5.
[ ] Lexical misses return UNKNOWN semantics.
[ ] Zeta23 graph and lexical replays pass from a downloaded artifact.
```
