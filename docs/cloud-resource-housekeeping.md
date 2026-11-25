# Cloud resource lifecycle — Turso and MotherDuck

Owner: Repository Search [#181](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/181).
Precedents: [Session Search Neon #235](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/235)
and [Cloud Shell artifact housekeeping](https://github.com/TeaShaman-cyber/theseus-cloud-shell/blob/main/docs/artifact-housekeeping.md).
This is an operational contract for a research lab, not an additional release
or production-approval process.

## Prevent archaeology

A cloud resource must have an owner and a retirement condition **before creation**.
Record the intended exact target in the owning Issue (or a linked versioned
manifest) before a remote write. Use one record per experiment or generation,
not per row or query:

- provider/account scope, group/database/schema and intended object name;
- owner Issue, purpose, source revision/artifact identity and projection version;
- whether it is a selected projection, temporary experiment, or recovery copy;
- known consumers and parent/child/clone dependencies (or explicitly unknown);
- retirement trigger and cleanup scope/postcondition;
- after creation, actual stable provider ID where exposed, observed state and
  evidence pointer; where IDs are unavailable, record fully qualified identity
  and scope, plus creation/readback evidence.

Use the existing accepted catalog to derive selected Turso names. Do not
maintain another serving catalog. A candidate not selected by it is not
automatically disposable. Keep credentials, account identifiers, private data
and connection URLs out of public receipts.

An interrupted run is not an unrecorded run: a planned target is durable before
the mutation. If the outcome is uncertain, read back that exact target before
retrying; record PENDING_CLEANUP or UNKNOWN_NEED_EVIDENCE with the missing check.
Do not make a new name just to bypass an ambiguous failure.

## Provider-specific practice

**Turso:** reuse the existing deterministic artifact-bound name and verify
the full artifact identity, not just its short name suffix. Record group and
database ID, parent/child and schema dependencies, protection and retirement
condition. Accepted projections retain existing delete protection. New
temporary resources must have an owning Issue and a bounded retirement trigger.
When publishing a new generation, explicitly disposition the previous one;
retention requires a reason, not indefinite default. A failed upload may leave
a database: inspect it and record its actual ID/state, rather than blindly
retrying or deleting. Review inventory at catalog change and experiment closure.

**MotherDuck:** use the governed native DuckDB route in Cloud Shell #32/#33
for ingest; use the official MCP or native SQL for readback. Keep analytical
projections separate from accepted source authority. Use an explicitly
owned database/schema namespace for persistent research and issue/run-bound
names for disposable fixtures. Do not put anonymous experiment tables into
shared or unrelated schemas. Record object kind, qualified name, source,
consumer dependencies (including views, shares, flights or dives when used),
and retirement trigger. Prefer temporary tables for session-only results.
If persistent test data is needed, record the exact target before creation and
remove it on completion when its authorized scope and evidence are satisfied.
No indefinite token, external share, or paid resource is implied by this guide.

## Disposition and closure

| State | Meaning |
| --- | --- |
| KEEP_ACTIVE | Selected or required by a current workflow; need not be production. |
| KEEP_RECOVERY_OR_SOURCE | Required independent recovery, source or rollback evidence. |
| TEMP | Owned reproducible fixture with explicit cleanup trigger. |
| REVIEW_DELETE | Candidate only; retirement and dependencies need verification. |
| UNKNOWN_NEED_EVIDENCE | Identity, owner or dependency evidence incomplete. |
| PENDING_CLEANUP | Intended cleanup not yet independently verified. |
| ALREADY_REMOVED | Exact target absent on independent readback. |

At experiment completion, reconcile planned/actual objects against current
provider inventory. Record a reason to retain, or remove only the exact
authorized disposable targets and check absence. If cleanup cannot finish,
retain the owner Issue or link an explicit follow-up with exact target and
missing prerequisite before closing it. A closed experiment, old name,
matching size, or same short hash does not establish safe deletion.
Before retiring any retained projection, verify full source identity and
recoverability, current consumers, dependencies and scope, then independently
verify absence and that necessary selected projections remain available.
Do not cascade deletion or disable protection by inference. Use provider
reported usage to measure actual change, not logical size as billed savings.

No new scheduler, storage service, or automatic deletion daemon is required.
The first line of defense is the pre-create record and closure reconciliation.
A read-only audit can derive expected names from the existing catalog and
compare them to live provider inventory; unmatched objects must be classified,
not deleted. A separate follow-up may automate audit/receipt generation
if recurring manual work warrants it.

## Initial read-only inventory — 2026-11-25

Historical snapshot; not current authority and not deletion authorization.
Turso inventory via the attached platform proxy; one visible organization,
group `repo-search`, primary Frankfurt. All 20 listed DBs were unarchived,
with empty parent_db_id. 19 protected, one unprotected. Current main
`1a39e5a044fff40626eeea23c788f3018fc33603`: nine accepted corpora; all nine
names generated by the actual materializer match live protected DBs.
[#132 final receipt](https://github.com/TeaShaman-cyber/theseus-repo-search-lab/issues/132#issuecomment-7084194926)
records 9/9 verified at the earlier sync; current inventory establishes
presence and protection, not a fresh full data/fingerprint check.

| Turso database | Provider DB ID | Usage bytes | Protection | Owner / disposition |
| --- | --- | ---: | --- | --- |
| `repo-search-annals-challenge-marton` | `01a20465-8101-7304-9071-a97680d11cb9` | 454,656 | True | UNKNOWN_NEED_EVIDENCE; #181; extra alias, creation/consumer evidence incomplete |
| `repo-search-annals-challenge-marton-1d0d8ce2` | `01a20470-7801-7399-be59-b640605efcd8` | 454,656 | True | KEEP_ACTIVE; #132; selected annals-challenge-marton |
| `repo-search-annals-marton` | `01a20452-5701-7d36-a7b1-c9de28e8c429` | 454,656 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-decreasing-diagrams-complete` | `01a20453-3501-7efe-92a5-ec922b8ba85f` | 1,392,640 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-decreasing-diagrams-complete-abd7c1b4` | `01a20470-8301-7555-ac94-490be2245db8` | 1,392,640 | True | KEEP_ACTIVE; #132; selected decreasing-diagrams-complete |
| `repo-search-leanprover-community-con-nf` | `01a20453-4501-72ac-bfb4-5a3c66d1d4b0` | 24,465,408 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-leanprover-community-con-nf-e6bf117b` | `01a20470-9201-7f4c-bf94-1e4aa6692dfb` | 24,465,408 | True | KEEP_ACTIVE; #132; selected leanprover-community-con-nf |
| `repo-search-leanprover-community-flt-692479-41bfa1d2` | `01a20472-9301-7824-a206-dd25bb006a05` | 1,056,768 | True | KEEP_ACTIVE; #132; selected leanprover-community-flt-regular |
| `repo-search-leanprover-community-flt-regular` | `01a20453-5401-724b-abca-70b2e1275663` | 1,056,768 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-openai-cdc-lean` | `01a20453-5e01-7b1b-a772-cf343ee1a21e` | 2,854,912 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-openai-cdc-lean-154e254c` | `01a20470-c001-76cd-83c9-24552f027365` | 2,854,912 | True | KEEP_ACTIVE; #132; selected openai-cdc-lean |
| `repo-search-openai-long-gaps` | `01a20453-6801-78f0-ab1e-a44623ea2ed3` | 1,466,368 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-openai-long-gaps-9c062327` | `01a20470-cd01-7bec-8cba-11e034907ad7` | 1,466,368 | True | KEEP_ACTIVE; #132; selected openai-long-gaps |
| `repo-search-openai-navier-stokes` | `01a20455-4b01-7b86-aad4-d726701af8f3` | 459,137,024 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-openai-navier-stokes-83408cf0` | `01a2049c-9401-7ed9-b60c-f63156f86f41` | 459,137,024 | True | KEEP_ACTIVE; #132; selected openai-navier-stokes |
| `repo-search-openai-ten-proofs-multicolor` | `01a20453-a201-769f-95c8-9c8c2ab054cc` | 41,205,760 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-openai-ten-proofs-multicolor-3f2d8edd` | `01a20471-0801-7250-bb63-2653db88d912` | 41,205,760 | True | KEEP_ACTIVE; #132; selected openai-ten-proofs-multicolor |
| `repo-search-zeta23` | `01a20453-ca01-7658-914e-90753ed0cc0b` | 30,195,712 | True | REVIEW_DELETE; #132 initial migration; verify replacement and consumers |
| `repo-search-zeta23-786b5ab3` | `01a20471-5e01-7048-94ee-c6a9036d1367` | 30,195,712 | True | KEEP_ACTIVE; #132; selected zeta23 |
| `theseus-repo-search` | `01a2044d-2e01-7f18-a3e8-43663f450eb5` | 454,656 | False | REVIEW_DELETE; #132 row-import canary; verify current scope |

Provider total storage usage: 1,125,367,808 bytes. Selected nine: 562,229,248 bytes; ten review candidates: 562,683,904 bytes; one unknown: 454,656 bytes. These are reported usage measurements, not verified savings. No database was deleted or protection changed.

**MotherDuck:** official MCP list returned two databases (2/2): owned
`my_db` and attached provider sample share `sample_data`. Read-only
information_schema query on my_db returned zero tables/views. Stable provider
DB ID was not exposed by this listing; scoped alias is the available identity.
Keep my_db as the current research connection target, and sample_data as
provider-managed sample data, outside project cleanup. Cloud Shell #32's
100k-edge temporary fixture was removed and readback verified; no persistent
RH analytical corpus has been loaded. This inventory does not establish
account-wide absence of shares, flights, tokens or other non-table objects.

**Next retirement review:** #181 owns this baseline. The nine legacy migration
copies and row-import canary are candidates, not approved deletions. Verify
their exact identities and dependencies against replacements and current
workflows before retirement. Investigate the additional Annals alias separately.
Do not repeat Neon #235's pattern of reconstructing new experiment ownership
from chat history.
