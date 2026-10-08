## Search-user outcome

<!-- State the real user query, coverage/reliability/latency failure, or
retrieval/navigation improvement. If none, explain why this PR belongs here. -->

## Scope gate

- [ ] This change serves fast retrieval/navigation of Lean declarations;
      it does **not** turn Repository Search into a theorem prover.
- [ ] Matching/provenance/evidence labels are not presented as a proof verdict.
- [ ] No new Lean axiom audit, `sorry` audit, mathematical certification,
      source build, or Mathlib run is made an implicit prerequisite for
      searching an already accepted release.
- [ ] If the change truly requires scientific proof/axiom verification, that
      separate scope and authorization are explicitly linked; it is not
      smuggled into corpus admission or search acceptance.

## Observable acceptance

<!-- State exact tests / search receipts, not just intended behavior. -->

- [ ] `./tools/dev/check` on the exact head (or explicitly NOT RUN for
      documentation-only changes).
- [ ] Phase-appropriate hosted checks/results are linked, or marked NOT RUN
      with reason.
- [ ] For retrieval-runtime changes: actual cold and warm query receipts,
      searched/unavailable corpus counts, and runtime-recovery status are
      linked or explicitly UNVERIFIED.
- [ ] The exact branch head and relevant files are read back before merge.

Related boundaries: [#118](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/118)
(agent/process scope), [#117](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/117)
(multi-corpus retrieval), [#34](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/34)
(closed proof-evidence expansion).
