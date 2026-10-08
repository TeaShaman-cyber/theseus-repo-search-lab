# Marton source-bound axiom witness (v0)

This is **an exact, bounded historical execution record**, not a mathematical
proof or a GitHub accepted-artifact release. It preserves the raw provider ZIP
from hosted [run #37784601371](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/actions/runs/37784601371),
its genuine Lean output, and the independently checked source-free sidecar
against previous consumer artifact identity `1d0d8ce2fbeb279fc9024856a94f788a2ed015dca5aeaee94786040b89d8f17c`.

The ZIP digest in `provenance.json` came from the **GitHub Actions artifact API**
and was confirmed by independently downloading ZIP bytes through the provider
archive endpoint (not inferred from a file inside the archive). The provider
archive can expire; these exact bytes are retained here for reproducibility.
The source is the pinned public upstream
`ImperialCollegeLondon/AnnalsChallenge@e32eb1411db0d700ca874dd695aea92f78699db8`,
file `AnnalsChallenge/AnnalsOfMathematics/2025-201-2-ConjectureOfMarton.lean`.

The real Lean 4.33.0-rc1 report for `ConjectureOfMarton.theorem_1_2`
contains `sorryAx`. `STATEMENT_CORPUS / NOT_PROOF_EVIDENCE` is the resulting
proper classification. No claim is made that all axioms are invalid: this
report is declaration-specific and subject to its exact Lean/source imports.

`sidecar.json` is the existing detached sidecar schema. `verification.json`
records the source-free consumer check from a fresh checkout pinned to
experiment head `b41bd07d876f42e818d121c8b466bc77024e8903`; the
provider-registered workflow ID was reused on an isolated branch after GitHub
failed to register the newly merged manual-only workflow. No project main
workflow was overwritten. The historical hosted run therefore reflects that
**experiment** head, not the current main head; its run id and attempt are
part of evidence identity.

Tests on these checked-in bytes are regression checks, not a replacement for
outside-provider attestation or a new Lean execution. See existing #34 and #35
for remaining acceptance and release authorization boundaries.
