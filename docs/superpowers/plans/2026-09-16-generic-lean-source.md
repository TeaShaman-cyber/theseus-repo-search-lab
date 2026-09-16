# Generic Lean Git Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the existing Zeta23 producer into one descriptor-driven Lean Git source path, prove it against `openai/LongGapsBetweenPrimes`, and preserve the current normalized artifact/query contracts.

**Architecture:** A strict `LeanGitSource` descriptor identifies one exact Git snapshot plus Lake build/extraction scope. Producer bootstrap pins remain separate. Both Anthropic Zeta23 and OpenAI LongGaps flow through the same checkout/build/LeanDepViz/normalize/index path, while research-specific replay assertions remain separate scripts. One source snapshot still produces one artifact and one disposable SQLite projection.

**Tech Stack:** Python 3.11+, stdlib `dataclasses/json/pathlib/subprocess`, Git, Lake/Lean via source-owned `lean-toolchain`, pinned Elan bootstrap, pinned standalone LeanDepViz, SQLite FTS5, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-16-generic-lean-source-design.md`

## Global Constraints

- **Hard prerequisite:** do not start Task 1 until Issue #1 has a corrected Zeta23 GitHub artifact that is independently consumed in MarcoPolo with Lean absent from the query-time path.
- Git repository at exact commit remains source authority; descriptors are orchestration inputs only.
- Descriptor schema is exactly `theseus.lean-git-source.v1`.
- Lean toolchain is read from the exact checkout's `lean-toolchain`; do not duplicate Lean version in the source descriptor.
- Publisher is not an adapter boundary; no Anthropic/OpenAI branches in retrieval, graph, projection, or artifact code.
- Preserve the existing normalized artifact schema and evidence grades.
- One source snapshot -> one artifact -> one disposable SQLite projection.
- No registry service, combined corpus database, embeddings, federated ranking, hosted service, ACL/security layer, or background indexing.
- `RULES.md` integration is **not part of this plan**. After Tasks 1-5 pass, create a separate cookbook issue/PR for the thin `/workspace/tools/repo-search/` route.
- All code changes use TDD: observe RED before production changes, then GREEN, then full suite.

---

## File Structure

### New files

- `src/theseus_repo_search/producer_config.py` — strict descriptor and producer-runner config models/loaders.
- `tests/test_producer_config.py` — fail-closed descriptor/runner validation and source-root resolution tests.
- `producer/sources/zeta23.json` — Zeta23 `LeanGitSource` descriptor.
- `producer/sources/openai-long-gaps.json` — OpenAI LongGaps `LeanGitSource` descriptor.
- `producer/runner.json` — extractor + Elan bootstrap pins shared by sources.
- `scripts/load_producer_env.py` — converts one source descriptor plus runner pins to deterministic GitHub Actions environment values.
- `tests/test_load_producer_env.py` — deterministic env mapping tests.
- `scripts/replay_long_gaps.py` — source-specific acceptance replay for OpenAI LongGaps.
- `tests/test_replay_long_gaps.py` — synthetic replay contract test.
- `.github/workflows/lean-source-producer-smoke.yml` — generic two-source producer smoke.

### Modified/removed files

- Remove `producer/zeta23.json` after its fields are split into source and runner configs.
- Remove `.github/workflows/zeta23-producer-smoke.yml` after the generic workflow reproduces its Zeta path.
- Modify `scripts/replay_zeta23.py` to accept `--artifact`, load manifest provenance, and keep all existing Zeta graph/lexical assertions unchanged.
- Modify `tests/test_replay_zeta23.py` to exercise the new artifact argument and provenance receipt.
- Do **not** modify `retrieval.py`, `graph.py`, `projection.py`, `artifact.py`, or artifact schema unless a failing cross-source test proves a source-neutral defect.

---

### Task 1: Strict `LeanGitSource` and Runner Config Contracts

**Files:**
- Create: `src/theseus_repo_search/producer_config.py`
- Create: `tests/test_producer_config.py`

**Interfaces:**
- Produces: `LeanGitSource.from_dict(data) -> LeanGitSource`
- Produces: `load_lean_git_source(path: Path) -> LeanGitSource`
- Produces: `LeanGitSource.resolve_source_root(checkout_root: Path) -> Path`
- Produces: `RunnerPins.from_dict(data) -> RunnerPins`
- Produces: `load_runner_pins(path: Path) -> RunnerPins`
- Consumes: existing `RepoSearchError` for machine-readable fail-closed errors.

- [ ] **Step 1: Write descriptor happy-path and round-trip tests**

```python
# tests/test_producer_config.py
import json
import tempfile
import unittest
from pathlib import Path

from theseus_repo_search.producer_config import (
    LeanGitSource,
    RunnerPins,
    load_lean_git_source,
)


COMMIT = "a" * 40


class LeanGitSourceTests(unittest.TestCase):
    def test_loads_valid_descriptor(self):
        source = LeanGitSource.from_dict({
            "schema": "theseus.lean-git-source.v1",
            "source_id": "openai-long-gaps",
            "source_repo": "openai/LongGapsBetweenPrimes",
            "source_commit": COMMIT,
            "source_subdir": "",
            "root_modules": ["LongGapsBetweenPrimes"],
            "build_target": "LongGapsBetweenPrimes",
        })
        self.assertEqual(source.root_modules, ("LongGapsBetweenPrimes",))
        self.assertEqual(source.source_commit, COMMIT)

    def test_load_file_uses_same_contract(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source.json"
            path.write_text(json.dumps({
                "schema": "theseus.lean-git-source.v1",
                "source_id": "zeta23",
                "source_repo": "anthropics/formal-math",
                "source_commit": COMMIT,
                "source_subdir": "zeta23",
                "root_modules": ["Zeta23"],
                "build_target": "Zeta23",
            }), encoding="utf-8")
            self.assertEqual(load_lean_git_source(path).source_id, "zeta23")
```

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONPATH=src python3 -m unittest -v tests.test_producer_config
```

Expected: import failure because `theseus_repo_search.producer_config` does not exist.

- [ ] **Step 3: Add fail-closed validation tests and observe RED before implementation**

Add tests that each assert `RepoSearchError.code == "BLOCKED_SOURCE_BINDING"` for:

```python
bad_cases = [
    {"schema": "wrong.schema"},
    {"source_commit": "main"},
    {"source_repo": "missing-owner"},
    {"source_subdir": "/absolute"},
    {"source_subdir": ".." + "/escape"},
    {"root_modules": []},
    {"root_modules": ["Zeta23", "Zeta23"]},
    {"build_target": ""},
]
```

Also test unknown keys fail closed:

```python
payload["publisher"] = "openai"
with self.assertRaises(RepoSearchError) as cm:
    LeanGitSource.from_dict(payload)
self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
```

Run the full new validation batch now and confirm it fails for the missing validation behavior before touching production code:

```bash
PYTHONPATH=src python3 -m unittest -v tests.test_producer_config
```

Expected: the newly added fail-closed cases are RED for the intended missing behavior, not due to syntax/import mistakes.

- [ ] **Step 4: Implement the minimal strict config model**

```python
# src/theseus_repo_search/producer_config.py
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import RepoSearchError


SOURCE_SCHEMA = "theseus.lean-git-source.v1"
RUNNER_SCHEMA = "theseus.lean-producer-runner.v1"
HEX40 = re.compile(r"[0-9a-f]{40}")
SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


def _blocked(message: str) -> RepoSearchError:
    return RepoSearchError("BLOCKED_SOURCE_BINDING", message)


def _require_exact_keys(data: dict[str, object], expected: set[str], label: str) -> None:
    if set(data) != expected:
        raise _blocked(f"{label} keys mismatch")


def _require_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise _blocked(f"{key} must be a string")
    return value


@dataclass(frozen=True)
class LeanGitSource:
    schema: str
    source_id: str
    source_repo: str
    source_commit: str
    source_subdir: str
    root_modules: tuple[str, ...]
    build_target: str

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "LeanGitSource":
        expected = {
            "schema", "source_id", "source_repo", "source_commit",
            "source_subdir", "root_modules", "build_target",
        }
        _require_exact_keys(data, expected, "source descriptor")
        schema = _require_str(data, "schema")
        source_id = _require_str(data, "source_id")
        source_repo = _require_str(data, "source_repo")
        source_commit = _require_str(data, "source_commit")
        source_subdir = _require_str(data, "source_subdir")
        build_target = _require_str(data, "build_target")
        roots = data.get("root_modules")

        if schema != SOURCE_SCHEMA:
            raise _blocked(f"unsupported source schema: {schema}")
        if SOURCE_ID.fullmatch(source_id) is None:
            raise _blocked("source_id must be lowercase letters, digits, and hyphens")
        if source_repo.count("/") != 1 or source_repo.startswith("/") or source_repo.endswith("/"):
            raise _blocked("source_repo must be owner/repository")
        if HEX40.fullmatch(source_commit) is None:
            raise _blocked("source_commit must be a 40-character lowercase Git SHA")
        subdir = PurePosixPath(source_subdir) if source_subdir else PurePosixPath(".")
        if subdir.is_absolute() or ".." in subdir.parts:
            raise _blocked("source_subdir must stay inside the Git checkout")
        if not isinstance(roots, list) or not roots or not all(isinstance(x, str) and x for x in roots):
            raise _blocked("root_modules must be a non-empty array of strings")
        if len(set(roots)) != len(roots):
            raise _blocked("root_modules must be unique")
        if not build_target.strip() or any(ch.isspace() for ch in build_target):
            raise _blocked("build_target must be one non-empty Lake target")

        return cls(
            schema=schema,
            source_id=source_id,
            source_repo=source_repo,
            source_commit=source_commit,
            source_subdir=source_subdir,
            root_modules=tuple(roots),
            build_target=build_target,
        )

    def resolve_source_root(self, checkout_root: Path) -> Path:
        checkout = checkout_root.resolve()
        candidate = (checkout / self.source_subdir).resolve()
        try:
            candidate.relative_to(checkout)
        except ValueError as exc:
            raise _blocked("resolved source root escapes Git checkout") from exc
        if not candidate.is_dir():
            raise _blocked(f"source root does not exist: {candidate}")
        return candidate


@dataclass(frozen=True)
class RunnerPins:
    schema: str
    extractor_repo: str
    extractor_commit: str
    extractor_main_sha256: str
    elan_version: str
    elan_sha256: str

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RunnerPins":
        expected = {
            "schema", "extractor_repo", "extractor_commit",
            "extractor_main_sha256", "elan_version", "elan_sha256",
        }
        _require_exact_keys(data, expected, "runner config")
        values = {key: _require_str(data, key) for key in expected}
        if values["schema"] != RUNNER_SCHEMA:
            raise _blocked("unsupported runner schema")
        if HEX40.fullmatch(values["extractor_commit"]) is None:
            raise _blocked("extractor_commit must be a 40-character lowercase Git SHA")
        if re.fullmatch(r"[0-9a-f]{64}", values["extractor_main_sha256"]) is None:
            raise _blocked("extractor_main_sha256 must be SHA-256 hex")
        if re.fullmatch(r"[0-9a-f]{64}", values["elan_sha256"]) is None:
            raise _blocked("elan_sha256 must be SHA-256 hex")
        return cls(**values)


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _blocked(f"cannot load producer config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise _blocked(f"producer config must be a JSON object: {path}")
    return value


def load_lean_git_source(path: Path) -> LeanGitSource:
    return LeanGitSource.from_dict(_load_json_object(path))


def load_runner_pins(path: Path) -> RunnerPins:
    return RunnerPins.from_dict(_load_json_object(path))
```

- [ ] **Step 5: Add source-root containment tests**

```python
def test_resolve_source_root_accepts_repository_root_and_nested_subdir(self):
    with tempfile.TemporaryDirectory() as d:
        checkout = Path(d) / "repo"
        nested = checkout / "zeta23"
        nested.mkdir(parents=True)
        root_source = LeanGitSource.from_dict({
            "schema": "theseus.lean-git-source.v1",
            "source_id": "root",
            "source_repo": "example/repo",
            "source_commit": COMMIT,
            "source_subdir": "",
            "root_modules": ["Example"],
            "build_target": "Example",
        })
        nested_source = LeanGitSource.from_dict({
            "schema": "theseus.lean-git-source.v1",
            "source_id": "nested",
            "source_repo": "example/repo",
            "source_commit": COMMIT,
            "source_subdir": "zeta23",
            "root_modules": ["Zeta23"],
            "build_target": "Zeta23",
        })
        self.assertEqual(root_source.resolve_source_root(checkout), checkout.resolve())
        self.assertEqual(nested_source.resolve_source_root(checkout), nested.resolve())


def test_resolve_source_root_rejects_missing_directory(self):
    with tempfile.TemporaryDirectory() as d:
        checkout = Path(d) / "repo"
        checkout.mkdir()
        source = LeanGitSource.from_dict({
            "schema": "theseus.lean-git-source.v1",
            "source_id": "missing",
            "source_repo": "example/repo",
            "source_commit": COMMIT,
            "source_subdir": "missing",
            "root_modules": ["Missing"],
            "build_target": "Missing",
        })
        with self.assertRaises(RepoSearchError) as cm:
            source.resolve_source_root(checkout)
        self.assertEqual(cm.exception.code, "BLOCKED_SOURCE_BINDING")
```

- [ ] **Step 6: Run GREEN and full suite**

```bash
PYTHONPATH=src python3 -m unittest -v tests.test_producer_config
PYTHONPATH=src python3 -m unittest discover -v
```

Expected: all tests pass; pre-existing 63 tests remain green.

- [ ] **Step 7: Commit**

```bash
git add src/theseus_repo_search/producer_config.py tests/test_producer_config.py
git commit -m "feat: define strict Lean Git source descriptors"
```

---

### Task 2: Split Zeta Source Facts From Producer Bootstrap Pins

**Files:**
- Create: `producer/sources/zeta23.json`
- Create: `producer/sources/openai-long-gaps.json`
- Create: `producer/runner.json`
- Create: `scripts/load_producer_env.py`
- Create: `tests/test_load_producer_env.py`
- Remove: `producer/zeta23.json`

**Interfaces:**
- Consumes: `load_lean_git_source()` and `load_runner_pins()` from Task 1.
- Produces: `environment_mapping(source, runner) -> dict[str, str]`.
- Produces GitHub Actions values: `SOURCE_ID`, `SOURCE_REPO`, `SOURCE_COMMIT`, `SOURCE_SUBDIR`, `ROOT_MODULES_CSV`, `BUILD_TARGET`, plus extractor/Elan pins.

- [ ] **Step 1: Add the two exact source descriptors and shared runner pins**

`producer/sources/zeta23.json`:

```json
{
  "schema": "theseus.lean-git-source.v1",
  "source_id": "zeta23",
  "source_repo": "anthropics/formal-math",
  "source_commit": "fbdc36bbf17d20af3fd0447c6d1a8a02773c9844",
  "source_subdir": "zeta23",
  "root_modules": ["Zeta23"],
  "build_target": "Zeta23"
}
```

`producer/sources/openai-long-gaps.json`:

```json
{
  "schema": "theseus.lean-git-source.v1",
  "source_id": "openai-long-gaps",
  "source_repo": "openai/LongGapsBetweenPrimes",
  "source_commit": "03a1190d0bc5502d9f54eeb60ad3e45e22b0df0b",
  "source_subdir": "",
  "root_modules": ["LongGapsBetweenPrimes"],
  "build_target": "LongGapsBetweenPrimes"
}
```

`producer/runner.json`:

```json
{
  "schema": "theseus.lean-producer-runner.v1",
  "extractor_repo": "cameronfreer/LeanDepViz",
  "extractor_commit": "7859d91f89b37c9193d9a09709a58be6d98f94d6",
  "extractor_main_sha256": "7051af16c579dc425859442bbe7b647eaea78a581ddf685c5951c2b7bf28ceed",
  "elan_version": "v4.2.3",
  "elan_sha256": "df0b2b3a439961ffcbb3985214365ffe40f49bc871df04dff268c7d8e21ca8b2"
}
```

- [ ] **Step 2: Write RED env-mapping test**

```python
# tests/test_load_producer_env.py
import unittest

from scripts.load_producer_env import environment_mapping
from theseus_repo_search.producer_config import LeanGitSource, RunnerPins


class ProducerEnvTests(unittest.TestCase):
    def test_mapping_joins_multiple_roots_for_leandepviz(self):
        source = LeanGitSource.from_dict({
            "schema": "theseus.lean-git-source.v1",
            "source_id": "fixture",
            "source_repo": "example/repo",
            "source_commit": "a" * 40,
            "source_subdir": "formal",
            "root_modules": ["Fixture.A", "Fixture.B"],
            "build_target": "Fixture",
        })
        runner = RunnerPins.from_dict({
            "schema": "theseus.lean-producer-runner.v1",
            "extractor_repo": "cameronfreer/LeanDepViz",
            "extractor_commit": "b" * 40,
            "extractor_main_sha256": "c" * 64,
            "elan_version": "v4.2.3",
            "elan_sha256": "d" * 64,
        })
        env = environment_mapping(source, runner)
        self.assertEqual(env["ROOT_MODULES_CSV"], "Fixture.A,Fixture.B")
        self.assertEqual(env["SOURCE_SUBDIR"], "formal")
        self.assertEqual(env["BUILD_TARGET"], "Fixture")
```

- [ ] **Step 3: Run RED**

```bash
PYTHONPATH=src:. python3 -m unittest -v tests.test_load_producer_env
```

Expected: import failure because `scripts.load_producer_env` does not exist.

- [ ] **Step 4: Implement deterministic env mapping only**

```python
# scripts/load_producer_env.py
from __future__ import annotations

from theseus_repo_search.producer_config import LeanGitSource, RunnerPins


def environment_mapping(source: LeanGitSource, runner: RunnerPins) -> dict[str, str]:
    return {
        "SOURCE_ID": source.source_id,
        "SOURCE_REPO": source.source_repo,
        "SOURCE_COMMIT": source.source_commit,
        "SOURCE_SUBDIR": source.source_subdir,
        "ROOT_MODULES_CSV": ",".join(source.root_modules),
        "BUILD_TARGET": source.build_target,
        "EXTRACTOR_REPO": runner.extractor_repo,
        "EXTRACTOR_COMMIT": runner.extractor_commit,
        "EXTRACTOR_MAIN_SHA256": runner.extractor_main_sha256,
        "ELAN_VERSION": runner.elan_version,
        "ELAN_SHA256": runner.elan_sha256,
    }
```

- [ ] **Step 5: Add a CLI test that writes a temporary GitHub env file**

```python
import json
import tempfile
from pathlib import Path
from scripts.load_producer_env import main


def test_cli_writes_sorted_environment_once(self):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        source_path = root / "source.json"
        runner_path = root / "runner.json"
        env_path = root / "github-env"
        source_path.write_text(json.dumps({
            "schema": "theseus.lean-git-source.v1",
            "source_id": "fixture",
            "source_repo": "example/repo",
            "source_commit": "a" * 40,
            "source_subdir": "formal",
            "root_modules": ["Fixture.A", "Fixture.B"],
            "build_target": "Fixture",
        }), encoding="utf-8")
        runner_path.write_text(json.dumps({
            "schema": "theseus.lean-producer-runner.v1",
            "extractor_repo": "cameronfreer/LeanDepViz",
            "extractor_commit": "b" * 40,
            "extractor_main_sha256": "c" * 64,
            "elan_version": "v4.2.3",
            "elan_sha256": "d" * 64,
        }), encoding="utf-8")
        rc = main([
            "--source", str(source_path),
            "--runner", str(runner_path),
            "--github-env", str(env_path),
        ])
        self.assertEqual(rc, 0)
        lines = env_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, sorted(lines))
        keys = [line.split("=", 1)[0] for line in lines]
        self.assertEqual(len(keys), len(set(keys)))
```

Run the new CLI test before implementing any CLI-specific production behavior and confirm RED:

```bash
PYTHONPATH=src:. python3 -m unittest -v tests.test_load_producer_env
```

If `environment_mapping` is already GREEN from the prior slice, the CLI-specific case itself must still be observed failing before the CLI implementation is added.

- [ ] **Step 6: Implement the minimal CLI after its RED test**

```python
# append to scripts/load_producer_env.py only after the CLI test is observed RED
import argparse
from pathlib import Path

from theseus_repo_search.producer_config import load_lean_git_source, load_runner_pins


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--github-env", type=Path, required=True)
    args = parser.parse_args(argv)
    source = load_lean_git_source(args.source)
    runner = load_runner_pins(args.runner)
    mapping = environment_mapping(source, runner)
    with args.github_env.open("a", encoding="utf-8") as fh:
        for key in sorted(mapping):
            fh.write(f"{key}={mapping[key]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run `tests.test_load_producer_env` again and require GREEN before continuing.

- [ ] **Step 7: Validate both committed descriptors through the production loader**

```bash
PYTHONPATH=src:. python3 - <<'PY'
from pathlib import Path
from theseus_repo_search.producer_config import load_lean_git_source, load_runner_pins
for name in ("zeta23", "openai-long-gaps"):
    source = load_lean_git_source(Path("producer/sources") / f"{name}.json")
    print(source.source_id, source.source_repo, source.source_commit)
print(load_runner_pins(Path("producer/runner.json")).extractor_commit)
PY
```

Expected exact source IDs/repos/commits and the pinned LeanDepViz commit.

- [ ] **Step 8: Remove the mixed legacy producer file and run full suite**

```bash
rm producer/zeta23.json
PYTHONPATH=src:. python3 -m unittest discover -v
```

- [ ] **Step 9: Commit**

```bash
git add producer src/theseus_repo_search/producer_config.py scripts/load_producer_env.py tests/test_load_producer_env.py
git commit -m "refactor: split Lean source descriptors from runner pins"
```

---

### Task 3: Generic Two-Source Producer Workflow

**Files:**
- Create: `.github/workflows/lean-source-producer-smoke.yml`
- Remove: `.github/workflows/zeta23-producer-smoke.yml`
- Modify: `scripts/replay_zeta23.py`
- Modify: `tests/test_replay_zeta23.py`

**Interfaces:**
- Consumes descriptor path, replay script, and artifact name from a two-row Actions matrix.
- Uses the exact same checkout/build/extract/normalize/index sequence for both sources.
- Reads `lean-toolchain` from the selected exact source root after checkout and logs it; it does not inject a Lean version from descriptor data.

- [ ] **Step 1: Replace source-specific workflow setup with a two-row matrix**

Use this matrix shape:

```yaml
strategy:
  fail-fast: false
  matrix:
    include:
      - source_descriptor: producer/sources/zeta23.json
        replay_script: scripts/replay_zeta23.py
        artifact_name: zeta23-repo-index-v1
      - source_descriptor: producer/sources/openai-long-gaps.json
        replay_script: scripts/replay_long_gaps.py
        artifact_name: openai-long-gaps-repo-index-v1
```

Keep the current pinned `actions/checkout` and `actions/upload-artifact` SHAs unchanged.

- [ ] **Step 2: Load descriptor and runner pins through the tested Python loader**

```yaml
- name: Load producer configuration
  shell: bash
  run: |
    set -euo pipefail
    python3 scripts/load_producer_env.py \
      --source "${{ matrix.source_descriptor }}" \
      --runner producer/runner.json \
      --github-env "$GITHUB_ENV"
```

- [ ] **Step 3: Make checkout destination source-neutral**

```yaml
- name: Checkout pinned source
  shell: bash
  run: |
    set -euo pipefail
    python3 scripts/producer_guard.py checkout \
      --repo-url "https://github.com/${SOURCE_REPO}.git" \
      --commit "$SOURCE_COMMIT" \
      --dest _target/source
```

Then verify exact readback with:

```bash
python3 scripts/producer_guard.py verify --repo-dir _target/source --expected "$SOURCE_COMMIT"
```

- [ ] **Step 4: Resolve and validate the canonical source root before any source-owned code executes**

Do **not** concatenate `SOURCE_SUBDIR` in shell. Extend the tested loader with a small CLI mode that loads the selected descriptor and calls `LeanGitSource.resolve_source_root(Path("_target/source"))`; write only that canonical, containment-checked path to `$GITHUB_ENV`. Add a RED CLI test using a tracked symlink whose resolved target escapes the checkout and verify it is rejected before any Lake command can run.

Example workflow shape after that test is GREEN:

```bash
python3 scripts/load_producer_env.py \
  --source "${{ matrix.source_descriptor }}" \
  --runner producer/runner.json \
  --checkout-root "$GITHUB_WORKSPACE/_target/source" \
  --github-env "$GITHUB_ENV"

test -n "$SOURCE_ROOT"
test -f "$SOURCE_ROOT/lean-toolchain"
printf 'observed_lean_toolchain=%s\n' "$(cat "$SOURCE_ROOT/lean-toolchain")"
```

`SOURCE_ROOT` must be the exact value returned by `resolve_source_root`; Lake/Lean/any source-owned executable is forbidden before this containment check succeeds. Do not compare the observed toolchain to a descriptor copy; the exact Git commit is the authority binding the file.

- [ ] **Step 5: Install the pinned generic runner prerequisites, then keep one generic Lake build path**

Carry forward the exact bootstrap operations from the corrected Zeta producer before invoking Lake or LeanDepViz:

```bash
curl --fail --location --proto '=https' --tlsv1.2 \
  --output "$RUNNER_TEMP/elan.tar.gz" \
  "https://github.com/leanprover/elan/releases/download/${ELAN_VERSION}/elan-x86_64-unknown-linux-gnu.tar.gz"
echo "${ELAN_SHA256}  $RUNNER_TEMP/elan.tar.gz" | sha256sum --check
mkdir -p "$RUNNER_TEMP/elan"
tar -xzf "$RUNNER_TEMP/elan.tar.gz" -C "$RUNNER_TEMP/elan"
"$RUNNER_TEMP/elan/elan-init" -y --no-modify-path --default-toolchain none
echo "$HOME/.elan/bin" >> "$GITHUB_PATH"

url="https://raw.githubusercontent.com/${EXTRACTOR_REPO}/${EXTRACTOR_COMMIT}/LeanDepViz/Main.lean"
curl --fail --location --proto '=https' --tlsv1.2 \
  --output "$RUNNER_TEMP/LeanDepVizMain.lean" "$url"
echo "${EXTRACTOR_MAIN_SHA256}  $RUNNER_TEMP/LeanDepVizMain.lean" | sha256sum --check
```

These are generic runner prerequisites, not source-specific branches. Do not replace the hash checks with floating installs.

Then run the source-owned Lake commands:

```bash
python3 scripts/producer_guard.py run --cwd "$SOURCE_ROOT" -- lake exe cache get
python3 scripts/producer_guard.py run --cwd "$SOURCE_ROOT" -- lake build "$BUILD_TARGET"
```

- [ ] **Step 6: Keep one generic bound LeanDepViz extraction path after verified bootstrap**

The generic workflow MUST use the corrected bootstrap's `producer_guard.py extract` path, not a plain `run`. This binds the raw graph hash to exact source commit, source scope, producer pins, and tracked-source cleanliness before/after extraction.

```bash
mkdir -p _out
root_args=()
IFS=',' read -r -a roots <<< "$ROOT_MODULES_CSV"
for root in "${roots[@]}"; do
  root_args+=(--root-module "$root")
done

python3 scripts/producer_guard.py extract \
  --cwd "$SOURCE_ROOT" \
  --repo-dir "$GITHUB_WORKSPACE/_target/source" \
  --expected "$SOURCE_COMMIT" \
  --raw-depgraph "$GITHUB_WORKSPACE/_out/raw-depgraph.json" \
  --receipt "$GITHUB_WORKSPACE/_out/raw-depgraph-receipt.json" \
  --source-repo "$SOURCE_REPO" \
  --source-subdir "$SOURCE_SUBDIR" \
  "${root_args[@]}" \
  --producer-kind lean-dep-viz \
  --producer-tool-repo "$EXTRACTOR_REPO" \
  --producer-tool-commit "$EXTRACTOR_COMMIT" \
  --producer-tool-hash "$EXTRACTOR_MAIN_SHA256" -- \
  lake env lean --run "$RUNNER_TEMP/LeanDepVizMain.lean" \
  --roots "$ROOT_MODULES_CSV" \
  --json-out "$GITHUB_WORKSPACE/_out/raw-depgraph.json"
```

The receipt is a required intermediate authority-binding record and is deleted only after normalized artifact verification/replay succeeds.

- [ ] **Step 7: Build the existing artifact without source-specific query code**

Invoke the existing CLI with matrix-loaded source values:

```bash
root_args=()
IFS=',' read -r -a roots <<< "$ROOT_MODULES_CSV"
for root in "${roots[@]}"; do
  root_args+=(--root-module "$root")
done
python3 -m theseus_repo_search build-artifact \
  --source-root "$SOURCE_ROOT" \
  --source-repo "$SOURCE_REPO" \
  --source-commit "$SOURCE_COMMIT" \
  --source-subdir "$SOURCE_SUBDIR" \
  "${root_args[@]}" \
  --producer-kind lean-dep-viz \
  --producer-tool-repo "$EXTRACTOR_REPO" \
  --producer-tool-commit "$EXTRACTOR_COMMIT" \
  --producer-tool-hash "$EXTRACTOR_MAIN_SHA256" \
  --authoritative-readback \
  --raw-depgraph _out/raw-depgraph.json \
  --raw-depgraph-receipt _out/raw-depgraph-receipt.json \
  --out "_out/${SOURCE_ID}-artifact"
```

The current CLI already defines `--root-module` with `action="append"`; the Bash array above preserves every descriptor root without changing CLI semantics. Add a workflow-review assertion that no root is collapsed into a single comma-containing CLI value.

- [ ] **Step 8: Verify, project, replay, and upload by source ID**

```bash
python3 -m theseus_repo_search verify-artifact --artifact "_out/${SOURCE_ID}-artifact"
python3 -m theseus_repo_search build-index \
  --artifact "_out/${SOURCE_ID}-artifact" \
  --db "_out/${SOURCE_ID}.sqlite"
python3 "${{ matrix.replay_script }}" \
  --db "_out/${SOURCE_ID}.sqlite" \
  --source-root "$SOURCE_ROOT" \
  --artifact "_out/${SOURCE_ID}-artifact" \
  --descriptor "${{ matrix.source_descriptor }}" \
  --out "_out/${SOURCE_ID}-replay.json"
```

Before any replay assertion, bind the disposable projection back to the supplied artifact: read `meta.artifact_identity` from SQLite and require exact equality with `artifact_identity(load_artifact(...).manifest)`. A mismatched DB/artifact pair is a replay failure, never a PASS receipt. Add the mismatch as a RED regression first.

The replay also loads the selected `LeanGitSource` descriptor and requires exact equality of manifest `source_repo`, `source_commit`, and `source_subdir` to the descriptor. This makes the OpenAI smoke prove the pinned `openai/LongGapsBetweenPrimes@03a1190d0bc5502d9f54eeb60ad3e45e22b0df0b` repository-root snapshot rather than any artifact from that repository.

Before changing `replay_zeta23.py`, add this RED change to `tests/test_replay_zeta23.py`:

```python
# Make the synthetic artifact identify the real Zeta source family.
source_repo="anthropics/formal-math",

result = run_replay(db, artifact, descriptor, source_root)
self.assertEqual(result["provenance"], {
    "repo": "anthropics/formal-math",
    "commit": COMMIT,
    "subdir": "zeta23",
})
```

Make four exact incremental edits to `scripts/replay_zeta23.py` without replacing its existing replay body:

1. Add `import sqlite3`, `artifact_identity`/`load_artifact`, and `load_lean_git_source` imports.
2. Change the signature to `run_replay(db_path: Path, artifact_path: Path, descriptor_path: Path, source_root: Path | None = None)`. Core graph/lexical assertions are artifact-only; repository-wide grep comparison runs only when `source_root` is provided by producer CI.
3. Immediately inside the function load the descriptor, validate DB↔artifact identity, then validate manifest provenance against that descriptor:

```python
manifest, _, _, _ = load_artifact(artifact_path)
source = load_lean_git_source(descriptor_path)
with sqlite3.connect(db_path) as conn:
    projected_identity = dict(conn.execute("SELECT key, value FROM meta"))["artifact_identity"]
if projected_identity != artifact_identity(manifest):
    raise AssertionError("projection/artifact identity mismatch")
if (manifest.source_repo, manifest.source_commit, manifest.source_subdir) != (
    source.source_repo, source.source_commit, source.source_subdir,
):
    raise AssertionError("artifact provenance does not match selected source descriptor")
```

Then insert this key into the existing return dictionary immediately after `"status": "PASS",`:

```python
"provenance": {
    "repo": manifest.source_repo,
    "commit": manifest.source_commit,
    "subdir": manifest.source_subdir,
},
```

Replace the parser/main tail with the explicit additional artifact argument:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_replay(args.db, args.artifact, args.descriptor, args.source_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0
```

Before upload:

```bash
rm -f _out/raw-depgraph.json "_out/${SOURCE_ID}.sqlite"
test ! -e _out/raw-depgraph.json
test ! -e "_out/${SOURCE_ID}.sqlite"
```

Upload only the normalized artifact directory plus source-specific replay receipt.

- [ ] **Step 9: Delete the old Zeta-only workflow and run local non-Lean verification**

```bash
rm .github/workflows/zeta23-producer-smoke.yml
PYTHONPATH=src:. python3 -m unittest discover -v
python3 -m py_compile src/theseus_repo_search/*.py scripts/*.py
```

Also inspect the workflow diff manually to confirm no `anthropics` or `openai` publisher branch exists in generic steps; publisher-specific values may appear only in descriptor files and matrix artifact/replay labels.

- [ ] **Step 10: Commit**

```bash
git add .github/workflows scripts tests src producer
git commit -m "ci: drive Lean producer smokes from source descriptors"
```

---

### Task 4: OpenAI LongGaps Acceptance Replay

**Observed pinned-source anchors (verified at `openai/LongGapsBetweenPrimes@03a1190d0bc5502d9f54eeb60ad3e45e22b0df0b`):**

```text
lean-toolchain = leanprover/lean4:v4.33.0
default target = LongGapsBetweenPrimes
LongGapsBetweenPrimes.lean:25   def iteratedLog
LongGapsBetweenPrimes.lean:41   def ShortTranslates
LongGapsBetweenPrimes.lean:3672 lemma short_translates : ShortTranslates
LongGapsBetweenPrimes.lean:4468 theorem long_gap_theorem : LongGapTheorem
LongGapsBetweenPrimes.lean:4502 theorem long_prime_gaps
```

The source comment on `long_gap_theorem` is `Theorem 1.1: the unconditional long-gap bound in the paper.` These are observed fixtures for the real smoke, not invented placeholder declarations. `Challenge.lean` is a separate comparator library and is not part of the v1 `LongGapsBetweenPrimes` root-module smoke.

**Files:**
- Create: `scripts/replay_long_gaps.py`
- Create: `tests/test_replay_long_gaps.py`

**Interfaces:**
- Consumes: one built SQLite projection, one normalized artifact, and the selected `LeanGitSource` descriptor; producer CI may also pass source root for a common workflow signature.
- Uses existing `search()`, `dependencies()`, and `context()` APIs unchanged.
- Produces one JSON receipt with exact source provenance plus graph/lexical/context assertions.

- [ ] **Step 1: Write the synthetic RED replay test**

Build a tiny artifact with these declaration IDs:

```python
names = [
    "LongGapsBetweenPrimes.long_gap_theorem",
    "LongGapsBetweenPrimes.short_translates",
    "LongGapsBetweenPrimes.iteratedLog",
]
```

Use source chunks containing grounded source phrases:

```python
chunk(
    "long_gap_theorem",
    "LongGapsBetweenPrimes.lean",
    "/-- Theorem 1.1: the unconditional long-gap bound in the paper. -/\n"
    "theorem long_gap_theorem : True := by trivial\n",
    100,
)
```

Add at least one elaborated value dependency from `long_gap_theorem` to `short_translates` in the synthetic fixture. The fixture tests replay mechanics only; the real corpus smoke must discover its actual graph edges independently.

Assertions:

```python
result = run_replay(db, artifact, descriptor)
self.assertEqual(result["status"], "PASS")
self.assertEqual(result["provenance"]["repo"], "openai/LongGapsBetweenPrimes")
self.assertEqual(result["provenance"]["commit"], COMMIT)
self.assertEqual(result["exact"]["target"], "LongGapsBetweenPrimes.long_gap_theorem")
self.assertGreater(result["graph"]["edge_count"], 0)
self.assertGreater(len(result["context"]["chunks"]), 0)
```

Also add two fail-closed RED cases before implementation:

- projection DB built from artifact A paired with artifact B -> replay rejects `projection/artifact identity mismatch`;
- descriptor commit/subdir differs from the artifact manifest -> replay rejects provenance mismatch.

The committed OpenAI descriptor is the source of the expected exact pin; do not duplicate a second hard-coded commit constant inside replay code.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=src:. python3 -m unittest -v tests.test_replay_long_gaps
```

Expected: import failure because `scripts.replay_long_gaps` does not exist.

- [ ] **Step 3: Implement the source-grounded replay**

Core checks:

```python
import sqlite3

from theseus_repo_search.artifact import artifact_identity, load_artifact
from theseus_repo_search.graph import dependencies
from theseus_repo_search.producer_config import load_lean_git_source
from theseus_repo_search.retrieval import context, search

TARGET = "LongGapsBetweenPrimes.long_gap_theorem"
LEXICAL_QUERY = "unconditional long gap bound"


def run_replay(db_path: Path, artifact_path: Path, descriptor_path: Path) -> dict[str, object]:
    manifest, _, _, _ = load_artifact(artifact_path)
    source = load_lean_git_source(descriptor_path)
    with sqlite3.connect(db_path) as conn:
        projected_identity = dict(conn.execute("SELECT key, value FROM meta"))["artifact_identity"]
    if projected_identity != artifact_identity(manifest):
        raise AssertionError("projection/artifact identity mismatch")
    if (manifest.source_repo, manifest.source_commit, manifest.source_subdir) != (
        source.source_repo, source.source_commit, source.source_subdir,
    ):
        raise AssertionError("artifact provenance does not match selected source descriptor")

    exact_hits = search(db_path, TARGET, limit=1)
    if len(exact_hits) != 1 or exact_hits[0].source_path != "LongGapsBetweenPrimes.lean":
        raise AssertionError("main theorem did not resolve to exact source provenance")

    graph = dependencies(db_path, TARGET, depth=1)
    if not graph.edges:
        raise AssertionError("main theorem has no in-scope elaborated dependency edges")
    if not all(str(edge["evidence_grade"]).startswith("ELABORATED_") for edge in graph.edges):
        raise AssertionError("graph replay returned non-elaborated dependency evidence")

    lexical = search(db_path, LEXICAL_QUERY, limit=10)
    if not any(hit.declaration_hint == "long_gap_theorem" for hit in lexical):
        raise AssertionError("grounded long-gap lexical query missed long_gap_theorem")

    ctx = context(db_path, TARGET, depth=1, token_budget=4000)
    if not any(chunk["declaration_hint"] == "long_gap_theorem" for chunk in ctx["chunks"]):
        raise AssertionError("bounded context omitted target theorem source")

    return {
        "status": "PASS",
        "provenance": {
            "repo": manifest.source_repo,
            "commit": manifest.source_commit,
            "subdir": manifest.source_subdir,
        },
        "exact": {
            "target": TARGET,
            "source_path": exact_hits[0].source_path,
            "source_start_line": exact_hits[0].source_start_line,
            "source_end_line": exact_hits[0].source_end_line,
        },
        "graph": {"edge_count": len(graph.edges), "edges": list(graph.edges)},
        "lexical": {
            "query": LEXICAL_QUERY,
            "hits": [hit.declaration_hint for hit in lexical],
        },
        "context": {
            "estimated_tokens": ctx["estimated_tokens"],
            "chunks": ctx["chunks"],
        },
    }
```

Add this exact argparse/output tail. `--source-root` is intentionally accepted for a common workflow signature even though this replay does not run a grep baseline:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_replay(args.db, args.artifact, args.descriptor)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0
```

- [ ] **Step 4: Run GREEN and full suite**

```bash
PYTHONPATH=src:. python3 -m unittest -v tests.test_replay_long_gaps
PYTHONPATH=src:. python3 -m unittest discover -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/replay_long_gaps.py tests/test_replay_long_gaps.py
git commit -m "test: add OpenAI LongGaps repository replay"
```

---

Before Task 5 commands are considered complete, add an explicit **artifact-only Zeta consumer mode**: the independent consumer receives the uploaded artifact and repository-search package/code only, builds a fresh SQLite projection, then runs the Zeta core replay with `source_root=None`. The old repository-wide grep baseline remains producer-CI evidence and is not required in the Lean-free consumer runtime.

### Task 5: Real Dual-Source Acceptance and Lean-Free Readback

**Files:**
- No production code unless a real failure produces a minimal reproducible defect.
- Update: Issue #5 with observed acceptance receipts.
- Do not modify Issue #1 in this task; the Global Constraints require its corrected bootstrap acceptance to be complete before Task 1 starts.

**Interfaces:**
- GitHub Actions produces separate `zeta23-repo-index-v1` and `openai-long-gaps-repo-index-v1` artifacts.
- MarcoPolo consumes each normalized artifact with Lean absent from `PATH` and rebuilds disposable SQLite locally.

- [ ] **Step 1: Push implementation branch and open/update its PR**

Before push:

```bash
git diff --check
PYTHONPATH=src:. python3 -m unittest discover -v
python3 -m py_compile src/theseus_repo_search/*.py scripts/*.py
```

Record exact branch head SHA before triggering CI.

- [ ] **Step 2: Require both matrix jobs to complete successfully**

For each source, verify GitHub job steps show success for:

```text
checkout exact source
verify exact source readback
observe source-owned lean-toolchain
lake cache/build
pinned LeanDepViz extraction
normalized artifact build
artifact verify
SQLite projection build
source replay
remove raw graph/SQLite
artifact upload
```

Do not treat one green matrix row as proof for the other.

- [ ] **Step 3: Inspect each uploaded artifact metadata**

Verify artifact names are distinct and record GitHub artifact IDs, ZIP digests, sizes, run ID, job IDs, and exact implementation commit.

Reject an OpenAI artifact if its manifest provenance does not exactly report:

```text
repo   = openai/LongGapsBetweenPrimes
commit = 03a1190d0bc5502d9f54eeb60ad3e45e22b0df0b
subdir = ""
```

Reject Zeta if its existing exact source provenance changes unexpectedly.

- [ ] **Step 4: Download each artifact independently into MarcoPolo**

For each ZIP:

```bash
sha256sum <downloaded-zip>
unzip -q <downloaded-zip> -d <consume-dir>
```

Require the local ZIP SHA-256 to match GitHub's artifact digest when GitHub exposes one.

- [ ] **Step 5: Prove Lean-free query-time consumption for each source**

Construct a query environment that removes Elan from `PATH` and verify:

```bash
command -v lean >/dev/null 2>&1 && exit 1 || true
PYTHONPATH=src python3 -m theseus_repo_search verify-artifact --artifact <artifact-dir>
PYTHONPATH=src python3 -m theseus_repo_search build-index --artifact <artifact-dir> --db <fresh-db>
```

Then run the corresponding replay against the fresh SQLite projection **without `--source-root`**, using the committed descriptor only to state the expected exact source identity:

```bash
python3 scripts/replay_zeta23.py \
  --db <zeta-db> --artifact <zeta-artifact-dir> \
  --descriptor producer/sources/zeta23.json --out <zeta-receipt>
python3 scripts/replay_long_gaps.py \
  --db <long-gaps-db> --artifact <long-gaps-artifact-dir> \
  --descriptor producer/sources/openai-long-gaps.json --out <long-gaps-receipt>
```

No source checkout, source build, grep baseline, or Lean invocation is allowed in this consumer step. The producer CI keeps the source-root grep benchmark separately.

- [ ] **Step 6: Run practical research queries on both projections**

Zeta examples:

```bash
python3 -m theseus_repo_search search --db <zeta-db> --query lemmaR_tight_two
python3 -m theseus_repo_search deps --db <zeta-db> --name count_certificate --depth 2
python3 -m theseus_repo_search context --db <zeta-db> --name count_certificate --depth 2 --token-budget 4000
```

OpenAI examples:

```bash
python3 -m theseus_repo_search search --db <long-gaps-db> --query LongGapsBetweenPrimes.long_gap_theorem
python3 -m theseus_repo_search deps --db <long-gaps-db> --name LongGapsBetweenPrimes.long_gap_theorem --depth 1
python3 -m theseus_repo_search context --db <long-gaps-db> --name LongGapsBetweenPrimes.long_gap_theorem --depth 1 --token-budget 4000
```

Confirm results carry exact source repo/commit provenance and graph edges carry elaborated evidence grades.

- [ ] **Step 7: Check the publisher-neutrality invariant in code**

Run:

```bash
rg -n "anthropics|openai|Zeta23|LongGapsBetweenPrimes" \
  src/theseus_repo_search/retrieval.py \
  src/theseus_repo_search/graph.py \
  src/theseus_repo_search/projection.py \
  src/theseus_repo_search/artifact.py
```

Expected: no source/publisher-specific branches or names in query/runtime code.

- [ ] **Step 8: Record the acceptance receipt in Issue #5**

Receipt must include:

```text
implementation commit
both exact source commits
both observed lean-toolchain strings
workflow run + job IDs
artifact IDs + digests
manifest source provenance
node/edge/source counts
replay PASS summaries
Lean-free consumer proof
publisher-neutrality grep result
```

Keep Issue #5 open if any source is degraded or one replay is incomplete.

- [ ] **Step 9: Only after Issue #5 acceptance, start a separate cookbook change**

Do **not** edit cookbook in this branch. Create one narrow cookbook issue for:

```text
/workspace/tools/repo-search/ stable wrapper
+
canonical rules/workspace.RULES.md routing section
+
projection/readback verification
```

That follow-up must preserve:

```text
prior chat continuity -> Session Search
repository evidence   -> Repository Search
current Git state     -> Git/GitHub authority
```

- [ ] **Step 10: Final implementation commit/PR verification**

Before requesting merge:

```bash
git diff --check
PYTHONPATH=src:. python3 -m unittest discover -v
python3 -m py_compile src/theseus_repo_search/*.py scripts/*.py
git status --short --branch
```

Require a clean tree and exact remote readback of the tested commit.

---

## Self-Review Checklist

Before implementation begins, verify this plan against the approved spec:

- [ ] Corrected Zeta bootstrap Lean-free acceptance is a hard prerequisite.
- [ ] Descriptor contains source identity/build scope, not duplicated Lean version.
- [ ] Runner pins are separate from source descriptor.
- [ ] Zeta and OpenAI use the same producer path.
- [ ] OpenAI replay is source-specific but retrieval/query code remains source-neutral.
- [ ] Multiple `root_modules` are preserved through comma-separated LeanDepViz roots.
- [ ] One snapshot produces one artifact and one SQLite projection.
- [ ] Both real artifacts are independently consumed without Lean.
- [ ] `RULES.md` remains a separate cookbook change after dual-source acceptance.
- [ ] No registry, combined database, embeddings, federation, hosted service, security/product layer, or background indexing is introduced.
