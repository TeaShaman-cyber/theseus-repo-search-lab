# Producer efficiency for future corpora

Applies to new corpus intake and producer rebuilds. See [Issue #8](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/8) and the [producer boundary](superpowers/specs/2026-09-16-repository-lens-design.md#producer-boundary). This runbook changes preparation and recovery decisions, not source authority, corpus scope, acceptance, or release permission.

## Default intake sequence

1. Check for an existing accepted artifact with the complete current producer fingerprint. Reuse it through a fresh source-free consumer if valid. Do not rebuild unaffected corpora.
2. Pin the source revision, subdirectory, toolchain, dependency lock, extractor and intended roots. Inspect actual imports and tracked source counts. Where lexical scope is excessive, evaluate descriptor-supported exclusions; retain every required graph module and explicitly record coverage. Do not silently shrink research scope.
3. Measure dependency preparation, compilation, extraction, normalization and replay separately. Record wall time, peak RSS where available, disk space, artifact sizes, and the last completed stage. Use a small representative cold/warm probe before a long or unfamiliar run.
4. Use GitHub Actions for reproducible producer and independent consumer acceptance. For interactive heavy diagnosis, reuse an existing authorized Codespace; exe.dev and MarcoPolo are orchestration and bounded-check surfaces, not heavy build hosts. Inspect actual available resources first. Codespaces temporary storage can disappear on stop: persist compact evidence/checkpoints outside it and verify readback before stopping. Do not assume a fixed disk size or create a larger paid machine without authorization.
5. When measured compilation approaches runner limits, split at completed, dependency-valid module targets. Preserve the actual completed output; do not rely on interrupting Lake at a timer boundary. Save exact raw extraction before normalization, and normalized output before replay. On failure resume from the latest compatible verified checkpoint, not from zero. Keep owner, source identity, hashes, retention and cleanup disposition in the covering issue.
6. Finish the exact endpoint build and pinned extraction; run canonical QA and independent fresh source-free acceptance. A checkpoint, cache hit, or pilot is not an accepted corpus. Release and catalog selection retain their existing gates.

## Optimization decisions and evidence

| Technique | Current status | Conditions for use |
| --- | --- | --- |
| Exact-lock compressed Mathlib download cache | Existing default | Preserve exact pins, bounded persistence and Mathlib's own restore/validation. Measure hit benefits. |
| Completed module-group checkpoints | Verified at Poincare scale | Bound source, lock, toolchain, workflow head/run/stage, archive digest and retention. Verify restore, then require full endpoint build. Keep the generic workflow unchanged until a separately reviewed reusable route is ready. |
| Native Lake output cache | Candidate, small pilot verified | Probe exact Lake version and supported cache commands. Measure larger sample and restore on a fresh runner before relying on it for long builds. |
| Early extractor filtering | Candidate, small parity verified | Preserve graph semantics and compare against pinned baseline on representative corpora before changing any extractor pin. |

### Native Lake cache pilot

On Lean 4.33.1 with Poincare source at `62db01af9388dc1d9b56b66d6674f4c09b2318a2`, the interactive pilot used `LAKE_ARTIFACT_CACHE=true`, `lake build <targets> -o outputs.jsonl`, and `lake cache stage outputs.jsonl <dir>`. Restore used a new local cache and build directory, `lake cache unstage <dir>`, `LAKE_RESTORE_ARTIFACTS=true`, and `lake --no-build build <targets>`. Five .olean SHA-256 values matched, including in a relocated checkout with shared pinned dependencies. The staged payload was 35,512 bytes versus 866,769 bytes for the comparison compressed build directory; warm restore took 9.7–10.1 seconds. The local cache itself had 6.5 GiB apparent size due to dependencies.

Before wider deployment: verify cache payload and mapping integrity with cryptographic member hashes and exact source, dependency lock, toolchain, platform, build configuration and producer-run provenance. Reject mismatches and untrusted cache inputs. Demonstrate fresh-runner restore with independently prepared pinned dependencies, no recompilation of restored targets, required on-disk artifacts for extraction, and equal graph output. Measure total staging/transfer/restore cost and disk usage on a representative larger group. Lake's internal cache identifiers alone are not cryptographic authority. Do not enable remote publication or broad shared caches by default.

### Early extractor filtering pilot

Pinned LeanDepViz built an entire imported environment graph before discarding out-of-scope nodes. A separate prototype applied the existing module predicate before collecting nodes and edges while retaining the final edge filter and keep-all behavior. On the small Poincare Statement sample, the original and relocated baseline and prototype produced byte-identical JSON (3 nodes, 2 edges; SHA-256 `c4118b5d757f23d6c566eab498474e69bc730ff189d702740209b7db212a98e9`). Prototype elapsed 107.05 s, peak RSS 8,240,520 KiB; baseline was still running at 201 s with observed RSS 9,790,060 KiB. Baseline total elapsed/peak were not measured.

Before changing the pinned extractor: establish differential parity on larger real graphs and fixtures covering explicit include prefixes, keep-all, internal declarations, edge endpoints, metadata/axioms, and deterministic ordering. Compare raw and normalized graph content and replay outcomes, with resource measurements. Any changed extractor identity requires normal producer-affecting acceptance and a distinct fingerprint; never relabel an old accepted artifact. No theorem audit or proof verdict is implied.

## Evidence and decision record

- [Matrix #189](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/189): bounded lexical scope and independent consumer acceptance.
- [Poincare interactive results #102](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/102#issuecomment-7220306221): sample cache and extractor comparison, retained evidence.
- [Poincare stage run 46010142690](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/actions/runs/46010142690): two completed groups and verified restores, third group in progress as of 2026-11-27. First two checkpoint ZIP sizes 305,154,391 and 650,131,307 bytes. Final corpus acceptance is not implied.
- [Issue #8](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/8) tracks general performance follow-up. Record the chosen route, measurements, any exception, and final disposition in the relevant intake issue.
