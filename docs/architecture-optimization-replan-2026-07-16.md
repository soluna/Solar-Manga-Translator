# Architecture Optimization Plan Amendment

Status: user-approved on 2026-07-17 after three plan-review iterations and an
explicit option-1 override of the corrected escalated plan.

This amendment keeps the accepted architecture direction in
`docs/architecture-optimization-execution-2026-07-15.md`. It replaces only the
execution decomposition for the escalated WU-A3, the compound WU-A4, and the
compound Phase-F ownership transfer. All other phases, global invariants,
compatibility rules, and final verification gates remain in force.

## Decision

A partial replan is necessary; a redesign is not.

The selected interface and ownership model remain correct:

- `WorkflowCoordinator.execute(ProjectCommand)` is the public Project Command
  seam.
- `ProjectWorkspace` owns Project Head, Pending Artifact Set, the Project
  Artifact Store, snapshots, and rebuildable storage projections.
- `InferenceBackend` and `TranslationProvider` are separate external adapters.
- Project Head is the only command commit boundary.

WU-A3 is too broad as one independently reviewed work unit. Its current diff
touches seven files and combines four different correctness surfaces:

1. Page Document input validity and Page Revision preflight.
2. Pending exact-match, corruption, checkpoint, and resume semantics.
3. Post-commit snapshot persistence and stored-metadata safety.
4. Snapshot-derived project-list projection and retention behavior.

Three adversarial reviews found blocking defects in different surfaces even
though all focused and full automated suites were green. That is evidence that
the work-unit test surface is wider than one reviewer can close reliably, not
evidence that Plan B should be discarded.

## New work-unit rules

Every remaining architecture work unit follows these rules:

1. One primary module interface or one invariant cluster per work unit.
2. Five changed files is the preferred maximum. Any exception must list why
   the additional file is an integration consumer rather than a second
   responsibility.
3. A work unit introduces at most one external seam. Production and
   deterministic test adapters must both exist before that seam is considered
   real.
4. The module interface is the test surface. Tests assert committed outcomes,
   classified errors, and observable durable state rather than private helper
   calls.
5. Every persistent side effect states whether it is pre-CAS or post-CAS.
   Pre-CAS failure preserves the previous Head; post-CAS failure is a warning
   and must be rebuildable.
6. Each work unit receives focused verification and a fresh adversarial review.
   Cross-module full suites run at explicit integration closeouts.
7. No compatibility projection is deleted in the same work unit that first
   introduces its replacement.

## Current implementation recovery

The current uncommitted WU-A3 implementation is retained as an integrated
prototype. It is not reset, stashed, or committed while the recovery gates are
open.

Already retained unless a recovery test disproves it:

- the four project-scoped actions route through `WorkflowCoordinator`;
- `translate-page` uses the Project Working Set path;
- one Project Head compare-and-swap exposes the final result;
- exact Pending matching includes action, normalized config and page scope,
  base Head generation, and base Head revision identifier;
- markerless, nonmatching, state-corrupt, blob-corrupt, traversal, root-only,
  and unknown-root Pending data fail closed;
- archives remain private until the Head CAS;
- glossary persistence is disabled inside command preparation;
- the legacy project transaction and hidden session keys are deleted.

The current seven-file diff receives one integrated WU-A3 commit only after all
five recovery gates pass. This is a one-time recovery constraint caused by the
already intertwined uncommitted prototype. Starting with WU-A4.1, each work
unit returns to one reviewed commit.

## Revised Phase A3 — close Project-scoped Pending execution

Dependency order:

`A3.1 -> A3.2 -> A3.3 -> A3.4 -> A3.5 -> A3.6 -> A3.7a -> A3.7b -> A3.7c -> A3-Closeout`

### WU-A3.1 — Page Document input integrity

Primary interface:

- `ProjectWorkspace.read_command_base(...)`
- `ProjectWorkspace.read_project_command_base(...)`

File scope:

- `backend/engine/project_workspace.py`
- `backend/workflow_coordinator.py`
- `backend/tests/test_project_workspace.py`
- `backend/tests/test_workflow_coordinator.py`

Definition of Done:

- One internal validator accepts a Page Revision only when its stored value is
  an `int`, is not a `bool`, and is greater than zero. It does not coerce
  strings, floats, or booleans.
- Both single-page and project command-base reads validate Page Documents
  through that rule before Working Set materialization, inference, or writes.
- A page-scoped command with a valid document but stale expected revision still
  returns the existing classified revision conflict.
- Missing, boolean, string, zero, and negative stored revisions fail closed.
- Failure tests prove that the execution adapter is not called and Project
  Head, Pending, snapshots, and compatibility projection are byte-for-byte
  unchanged.

Focused verification:

- `backend.tests.test_project_workspace`
- Page Revision and `translate-page` cases in
  `backend.tests.test_workflow_coordinator`

### WU-A3.2 — Pending execution integrity

Primary interface:

- `ProjectWorkspace.materialize_project_working_set(...)`
- `ProjectWorkspace.commit_project_working_set(...)`

File scope:

- `backend/engine/project_workspace.py`
- `backend/engine/translator.py`
- `backend/workflow_coordinator.py`
- `backend/tests/test_workflow_coordinator.py`
- `backend/tests/test_translator_engine_state.py`

This is primarily a verification gate over behavior already present in the
prototype. Production changes are allowed only when a focused test disproves
one of the invariants below.

Definition of Done:

- Exact-match Pending reuse is tested independently for action, normalized
  config, page scope, base generation, and base revision identifier.
- Corrupt Pending validation occurs before match evaluation, including for
  nonmatching Pending data, so diagnostic evidence cannot be silently deleted.
- A partial project failure preserves the old Head and verified page
  checkpoints; an exact retry reuses only those pages.
- Successful completion advances Head exactly once. Pending cleanup and garbage
  collection occur after CAS and can only append warnings.
- Source, translated, cache, and archive files stay private until CAS; archive
  construction cannot leak the private Working Set path into durable state.
- Cancellation remains cancellation and does not become a warning or a second
  cancellation interface.

Focused verification:

- Pending corruption and routing cases in
  `backend.tests.test_workflow_coordinator`
- Partial-failure, exact-retry, archive, and cancellation cases in
  `backend.tests.test_translator_engine_state`
- Deletion searches for `_project_artifact_transaction`,
  `_artifact_transaction_*`, and `_pending_completed_page_ids`

### WU-A3.3 — Snapshot document safety

Primary interface:

- The snapshot request carried by `PreparedHeadUpdate` and persisted after a
  successful Project Head CAS.

File scope:

- `backend/engine/translator.py`
- `backend/tests/test_translator_engine_state.py`

Definition of Done:

- Snapshot configuration keeps reconstructible logical choices such as
  `font_key` and `style_font_keys`, but removes runtime-only `font_path`,
  `style_font_paths`, private Working Set paths, temporary paths, and workspace
  absolute roots.
- Provider and cleanup credentials remain redacted. The raw persisted snapshot
  JSON contains none of the injected secret literals.
- Snapshot creation occurs only after the committed Head exists and references
  that exact Head generation and revision identifier.
- A snapshot creation or retention failure does not perform a second Head CAS
  and returns command success with a warning.

Focused verification:

- Production-adapter snapshot persistence cases in
  `backend.tests.test_translator_engine_state`
- Raw-manifest assertions for secret literals, absolute runtime paths, and
  committed Head reference

### WU-A3.4 — Snapshot catalog and rebuildable project projection

Primary interface:

- `ProjectWorkspace.read_snapshot_manifests(...)`
- `ProjectWorkspace.rebuild_project_index(...)`

File scope:

- `backend/engine/project_workspace.py`
- `backend/tests/test_project_workspace.py`
- `backend/tests/test_workflow_coordinator.py` (existing corrupt-Head fixture only)
- `backend/tests/test_workflow_coordinator.py`

Definition of Done:

- Snapshot manifests are the durable source of truth for `snapshot_count` and
  `latest_snapshot_*`. Head-bound project-manifest copies of those fields are
  treated as compatibility data and are never trusted when rebuilding the
  project index.
- An empty snapshot directory produces count zero and empty latest fields.
  Every `*.json` snapshot file must decode to an object with a non-empty,
  storage-safe `snapshot_id` that matches its filename and a non-empty
  `created_at`; duplicate snapshot identifiers fail closed. Legacy manifests
  may omit the content-addressed artifact bundle, as required by ADR 0002.
- Invalid JSON, non-object, missing-identity, filename-mismatch, and duplicate
  manifests raise the classified corrupt-artifact error. A failed rebuild does
  not overwrite the last valid project index.
- The post-CAS order is explicit: attempt snapshot creation, attempt retention,
  derive the visible snapshot summary from the manifests that actually exist,
  refresh the rebuildable index, and then garbage-collect. Each failed step
  appends a warning and does not roll back Head.
- After a successful automatic snapshot, project listing and a deleted-then-
  rebuilt index report the same count/latest values as the snapshot manifests.
- Snapshot creation failure produces no dangling latest-snapshot reference.
  Retention failure reports the manifests that actually remain, even when that
  count temporarily exceeds the retention target.
- Every success or failure path advances Head by at most one generation.

Focused verification:

- `backend.tests.test_project_workspace`
- Snapshot/index and post-CAS warning cases in
  `backend.tests.test_workflow_coordinator`

### WU-A3.5 — explicit Pending page-finalization evidence

This recovery gate was authorized by the user's option-1 selection on
2026-07-18 after A3-Closeout exhausted three review retries. The replacement
plan passed the Feasibility, Completeness, and Scope & Alignment plan-review
gate on iteration 2.

Primary interface:

- `ProjectWorkspace.read_pending_artifact_set(...)`
- `ProjectWorkspace.write_pending_artifact_set(...)`
- `ProjectWorkspace.materialize_project_working_set(...)`

File scope:

- `backend/engine/project_workspace.py`
- `backend/engine/translator.py`
- `backend/tests/test_project_workspace.py`
- `backend/tests/test_translator_engine_state.py`
- `backend/tests/test_workflow_coordinator.py`

Definition of Done:

- New Pending writes use schema version 2 and one canonical per-page stage map:
  `page_checkpoints: {page_id: detected | rendered | finalized}`. Page IDs and
  stage values are strict, and an exact Pending identity cannot regress a page
  stage or remove prior page evidence.
- `detected` proves recognition and editable-cache work, `rendered` proves a
  translated output that may skip translation/render, and `finalized` proves
  the stable output after the optional repair decision. Archive construction
  remains project-level work and is never represented as a page checkpoint.
- Action, global workflow stage, page stage, Page State capabilities, cache,
  translated output, image, and blob evidence are validated before Pending
  match evaluation. Impossible combinations fail with
  `CorruptProjectArtifactError` without changing Head or diagnostic evidence.
- Markerless schema-v1 Pending remains readable and is normalized from both
  action and global workflow stage. `detect`, plus `translate` while
  `detecting` or `detected`, maps legacy `completed_page_ids` to `detected`.
  `translate`, `resume-translate`, or `translate-page` while `translating`
  maps it to `rendered`, never `finalized`. `rerender` while `translated` maps
  it to `finalized`; every other pair fails closed. The next checkpoint writes
  schema v2.
- Exact retry skips render for `rendered` or `finalized` pages, but skips
  optional page repair only for `finalized` pages. Repair candidates come from
  the original command page scope rather than the remaining render list.
- A page becomes `finalized` only after enhanced output, AI-clean output,
  explicit stable fallback, or an explicit no-repair decision is complete and
  its resulting translated/cache bytes are checkpointed. Page-level callbacks
  retain progress between pages; cancellation never finalizes the interrupted
  page.
- Archive construction begins only after every page in command scope is
  finalized. Archive failure retains post-repair bytes; exact retry skips all
  finalized page work, rebuilds the archive, and commits consistent page and
  archive bytes through one Head CAS.
- Tests cover a post-repair archive failure, two-page render failure, two-page
  repair cancellation, no-repair/fallback finalization, conservative schema-v1
  detection and translation migration, corrupt/regressing stage maps, target
  translated/detected packaging behavior, CAS conflict, Pending cleanup,
  archive privacy, and final Head/archive byte identity.
- Detection and rerender behavior changes only to use the normalized stage
  representation. Production translation-resume control flow no longer uses
  `completed_page_ids`; that name may remain only in the isolated schema-v1
  compatibility decoder and historical documentation.

Focused verification:

- Pending schema/monotonicity cases in `backend.tests.test_project_workspace`
- Public Coordinator retry and artifact cases in
  `backend.tests.test_translator_engine_state`
- Pending corruption and routing cases in
  `backend.tests.test_workflow_coordinator`

### WU-A3.6 — atomic snapshot-root publication and collection

Primary interface:

- `ProjectWorkspace.create_project_head_snapshot(...)`
- `ProjectWorkspace.enforce_snapshot_retention(...)`
- `ProjectWorkspace.garbage_collect_snapshot_blobs(...)`

File scope:

- `backend/engine/project_workspace.py`
- `backend/engine/translator.py`
- `backend/tests/test_project_workspace.py`
- snapshot/GC assertions in the existing A3 Coordinator and Translator tests

Definition of Done:

- For one project, Pending publication, Head blob capture and pointer publish,
  snapshot-catalog publication/retention, and GC root discovery/deletion share
  the same project `RLock` for their complete root-changing windows.
- GC reads the authoritative snapshot catalog while holding that lock; it does
  not trust a caller-provided list captured before lock acquisition.
- A snapshot published after another caller observed an empty catalog remains
  restorable after GC, including when it is the only root for an older Head
  revision and blob.
- Translator compatibility paths delegate snapshot creation, retention, and GC
  to ProjectWorkspace rather than publishing snapshot manifests or supplying
  snapshot-root lists themselves.
- Deterministic event-driven regressions cover Pending publication, Head
  publication, and stale snapshot-catalog interleavings without timing sleeps.
- Lock-order review proves there is no project-lock cycle and different
  projects remain independently synchronized.

Focused verification:

- Blob-root and concurrent publication cases in
  `backend.tests.test_project_workspace`
- Existing snapshot/catalog integration cases in
  `backend.tests.test_workflow_coordinator` and
  `backend.tests.test_translator_engine_state`

### WU-A3.7a — fail-closed snapshot publication evidence

Primary interface:

- `ProjectWorkspace.create_project_head_snapshot(...)`

File scope:

- `backend/engine/project_workspace.py`
- `backend/tests/test_project_workspace.py`

Definition of Done:

- Snapshot publication validates its raw revision identifier and generated
  snapshot identifier as exact storage-safe filenames without trimming or path
  normalization.
- The supplied Head must match its stored revision document for schema,
  project, generation, revision, and file map before a snapshot manifest can be
  published.
- Every logical path and blob record is validated, and every referenced blob is
  present with the expected content digest.
- Unsafe revision or snapshot identifiers, unsafe logical paths, malformed blob
  records, missing or mismatched revisions, and missing or tampered blobs fail
  with the classified corrupt-artifact error and leave the snapshot catalog
  byte-for-byte unchanged.
- A valid retained older Head remains snapshot-able and restorable.

### WU-A3.7b — atomic Pending cleanup ownership

Primary interface:

- `ProjectWorkspace.clear_pending_artifact_set(...)`
- `ProjectWorkspace.clear_obsolete_pending_artifact_set(...)`

File scope:

- `backend/engine/project_workspace.py`
- `backend/engine/translator.py`
- `backend/tests/test_project_workspace.py`
- `backend/tests/test_translator_engine_state.py`
- existing Coordinator tests only when required by the public seam

Definition of Done:

- Unconditional Pending deletion is serialized by the project `RLock`.
- Obsolete cleanup reads the authoritative current Head and Pending manifest and
  makes the keep/delete decision inside the same lock. It returns false for no
  Pending or a current Pending, true only after deleting an obsolete Pending,
  and fails closed without changing bytes for corrupt Pending evidence.
- Page-working-set and Translator compatibility cleanup delegate to the atomic
  interface; project-working-set cleanup retains its exact owned-identity rule.
- Deterministic Event/Join regressions prove that an in-flight or newly
  published current-Head Pending cannot be deleted by an older cleanup, and GC
  continues to retain its blob roots.

### WU-A3.7c — application seam fixture alignment

File scope:

- `backend/tests/test_api_security.py`

Definition of Done:

- The zero-region WebSocket integration fake honors the Coordinator's
  `persist=False` preparation contract and private `archive_destination`.
- The fixture never advances Project Head during preparation, so the public
  Coordinator owns the single CAS and the clean-checkout integration test ends
  in `completed` rather than a Head conflict.

### WU-A3-Closeout — integrated architecture gate

File scope:

- the seven files already modified by the WU-A3 prototype plus the A3.7c
  application-fixture alignment; no new production responsibility may enter at
  this gate. This plan document is committed with that eight-file candidate.

Definition of Done:

- WU-A3.1 through WU-A3.7c focused suites pass independently.
- Original WU-A3 Definition of Done and global invariants 1 through 10 pass as
  an integrated matrix.
- The stage-available verification matrix below passes. Tests and package
  entries owned by later Phases C, E, and G are not forward dependencies of
  this closeout.
- A fresh adversarial review inspects the complete diff and returns no blocking
  issue.
- Only then is the integrated WU-A3 change committed. WU-A4 work cannot start
  earlier.

## Revised Phase A4 — adapters, one seam at a time

The original WU-A4 mixed two independent external seams, Coordinator cutover,
and deletion of legacy workflow methods. It is replaced by four serial work
units:

`A4.1 -> A4.2 -> A4.3 -> A4.4`

### WU-A4.1 — InferenceBackend seam

File scope:

- `backend/inference_backend.py` (new)
- `backend/engine/translator.py`
- `backend/tests/test_inference_backend.py` (new)
- `.github/workflows/ci.yml`

Definition of Done:

- Upstream import, subprocess, model-cache, and inference error knowledge sits
  behind one InferenceBackend interface.
- Production and deterministic fake adapters exercise the same interface.
- TranslatorEngine may temporarily forward compatibility calls, but its
  callers cannot reach upstream implementation details.
- Success, malformed output, upstream failure, cancellation, and cache reuse
  are tested through the InferenceBackend interface.

### WU-A4.2 — TranslationProvider seam

File scope:

- `backend/translation_provider.py` (new)
- `backend/engine/translator.py`
- `backend/tests/test_translation_provider.py` (new)
- `.github/workflows/ci.yml`

Definition of Done:

- Provider authentication, request normalization, response normalization, and
  error classification sit behind one TranslationProvider interface.
- Production and deterministic fake adapters exercise the same interface.
- Secrets never cross into Project State, Pending, snapshots, task events, or
  diagnostic payloads.
- Success, rate/context errors, authentication failure, retryable failure,
  malformed response, and cancellation are tested through the provider
  interface.

### WU-A4.3 — Coordinator adapter cutover

File scope:

- `backend/workflow_coordinator.py`
- `backend/engine/translator.py`
- `backend/main.py`
- `backend/tests/test_workflow_coordinator.py`

Definition of Done:

- Application assembly injects InferenceBackend and TranslationProvider
  adapters into the workflow path.
- Coordinator tests cover success, partial failure, exact retry, conflict, and
  cancellation with deterministic adapters.
- Production dispatch no longer calls `detect_session`, `translate_session`,
  `resume_translation_session`, or `rerender_session`. Those methods remain as
  temporary test/compatibility forwarding until WU-A4.4.
- Phase-A deletion tests prove that action dispatch, provider switching, and
  upstream implementation knowledge have not moved into another shallow
  module.

### WU-A4.4 — legacy workflow consumer migration and method deletion

File scope:

- `backend/workflow_coordinator.py`
- `backend/engine/translator.py`
- `backend/tests/test_workflow_coordinator.py`
- `backend/tests/test_translator_engine_state.py`
- `backend/tests/test_api_security.py`

Definition of Done:

- All direct or patched uses of `detect_session`, `translate_session`,
  `resume_translation_session`, and `rerender_session` are migrated to the
  Coordinator plus deterministic InferenceBackend/TranslationProvider
  adapters.
- API security, disconnect/reconnect, cancellation, and production-adapter
  tests inject at the owning adapter seams rather than patching
  `main.translator_engine` workflow methods.
- The four public workflow methods and the obsolete legacy execution branch in
  `TranslatorEngineWorkflowAdapter` are deleted. TranslatorEngine remains only
  for editing, snapshot/restore, glossary, export, and other Phase-F consumers.
- At work-unit end, repository search finds no caller or patch target for the
  four deleted method names outside historical documentation. The full
  stage-available integration matrix passes.

Human checkpoint: verify both adapter seams, the Coordinator interface, and the
Phase-A deletion test after WU-A4.4 and before Phase B starts.

## Revised Phase F — retire TranslatorEngine by ownership slice

The original WU-F1 combined editing, snapshot/restore, glossary extraction,
glossary application, and export. It is replaced by the following serial work
units after Phases A through E:

`F1 -> F2 -> F3 -> F4 -> F5 -> F6 -> F7`

### WU-F1 — PageEditor ownership

Primary module: `backend/page_editor.py` (new).

File scope:

- `backend/page_editor.py` (new)
- `backend/engine/translator.py`
- `backend/tests/test_page_region_commands.py`
- `backend/tests/test_project_operations.py` (new)

Definition of Done:

- PageEditor owns Page Command application, Page Document revision checks,
  editing operations, and `PageDocumentRevisionConflict`.
- TranslatorEngine temporarily imports and re-exports the conflict and forwards
  editing calls, so existing callers remain compatible until WU-F6.
- Invalid/stale revisions fail before writes; successful single-page edits
  advance Page Document, Page Artifacts, Project State, and manifest in one
  Head CAS.
- Tests cover every existing command kind, zero Text Regions, stale/invalid
  revision, missing editable data, pre-CAS failure, post-CAS projection warning,
  and legacy Page Document adaptation through the PageEditor interface.

### WU-F2 — snapshot/restore ownership

Primary module: `backend/engine/project_workspace.py`.

File scope:

- `backend/engine/project_workspace.py`
- `backend/engine/translator.py`
- `backend/tests/test_project_workspace.py`
- `backend/tests/test_project_operations.py`

Definition of Done:

- Snapshot document construction, redaction, catalog validation, retention,
  restore, and garbage collection sit behind the ProjectWorkspace interface.
- ADR 0002 legacy snapshots without artifact bundles remain readable; new
  snapshots and restores verify content-addressed files and logical paths.
- A3 post-CAS warning, one-Head-CAS, manifest corruption, empty catalog, shared
  artifact-store, and rebuildable-index tests pass through ProjectWorkspace.
- TranslatorEngine keeps only forwarding methods until WU-F6.

### WU-F3 — glossary extraction ownership

Primary modules: `backend/workflow_coordinator.py` and
`backend/translation_provider.py`.

File scope:

- `backend/workflow_coordinator.py`
- `backend/translation_provider.py`
- `backend/workflow_events.py`
- `backend/engine/translator.py`
- `backend/tests/test_project_operations.py`

Definition of Done:

- A typed glossary-extraction command uses the existing Coordinator seam and
  the TranslationProvider interface; pure candidate discovery remains in
  `backend/domain/glossary_candidates.py`.
- OCR context reading and provider requests are pre-CAS. Missing OCR skips the
  provider; authentication, provider failure, and cancellation leave Head and
  compatibility state unchanged.
- Only an explicit provider context-length failure may use the compact-context
  fallback. Empty or evidence-incomplete output follows ADR 0003 retries and
  remains retryable; successful extraction advances glossary state in one Head
  CAS.
- Tests migrated from `backend/tests/test_translator_engine_state.py` cover
  large context, context fallback, auth/provider failure, cancellation, empty
  retry, evidence retry, merge, and successful committed outcome through the
  Coordinator/TranslationProvider interfaces. Provider response normalization
  remains covered by `backend/tests/test_translation_provider.py` from A4.2.

### WU-F4 — glossary edit and application ownership

Primary interface: typed glossary save/preview/apply operations at the
Coordinator seam.

File scope:

- `backend/workflow_coordinator.py`
- `backend/workflow_events.py`
- `backend/engine/project_workspace.py`
- `backend/engine/translator.py`
- `backend/tests/test_project_operations.py`

Definition of Done:

- Preview is read-only. Save is a single-Head-CAS state update. Apply builds all
  glossary overrides, affected Page Documents, rerendered artifacts, and the
  archive in a private Project Working Set before one Head CAS.
- Partial rerender failure or cancellation leaves the previous Head and
  compatibility projection visible. Verified pages may remain only in an exact
  Pending match and an exact retry may reuse them.
- Automatic snapshot, project-index refresh, Pending cleanup, and garbage
  collection are post-CAS and warning-only.
- Tests cover no-op preview/apply, prior-translation replacement, untranslated
  source replacement, one-page and multi-page success, mid-project failure,
  cancellation, exact retry, stale Head conflict, archive privacy, and post-CAS
  warning behavior. Every failure asserts Head, Pending, Page Documents,
  overrides, archive, and compatibility projection explicitly.

### WU-F5 — ProjectExporter ownership

Primary module: `backend/project_exporter.py` (new).

File scope:

- `backend/project_exporter.py` (new)
- `backend/engine/translator.py`
- `backend/tests/test_project_operations.py`

Definition of Done:

- Result and blank archive construction sit behind one ProjectExporter
  interface and consume only committed ProjectWorkspace facts.
- Tests cover path traversal, missing/corrupt artifacts, deterministic archive
  contents, zero-page/empty exports, and legacy-project compatibility through
  the ProjectExporter interface.

### WU-F6 — consumer migration

Primary goal: remove every first-party import from the TranslatorEngine module,
not only imports of the `TranslatorEngine` class.

File scope:

- all files returned at work-unit start by
  `rg -l 'engine\.translator|translator_engine|TranslatorEngine' backend scripts frontend desktop --glob '!**/node_modules/**' --glob '!**/.venv*/**' --glob '!backend/manga-image-translator/**'`
- expected current consumers include `backend/main.py`,
  `backend/workflow_coordinator.py`, `backend/tests/test_workflow_coordinator.py`,
  `backend/tests/test_api_security.py`,
  `backend/tests/test_project_artifact_state.py`,
  `backend/tests/test_page_region_commands.py`, and
  `backend/tests/test_translator_engine_state.py`, plus indirect route-contract
  consumers such as `backend/tests/test_workflow_contract.py` and the V2 fixture
  generator `scripts/create_canvas_test_fixture.py`

This is an explicit file-count exception: every changed file is an integration
consumer of already introduced interfaces; this work unit introduces no new
implementation responsibility.

Definition of Done:

- Production and tests import PageEditor, ProjectWorkspace,
  WorkflowCoordinator, TranslationProvider, InferenceBackend, ProjectExporter,
  or their owning error modules directly.
- `PageDocumentRevisionConflict`, constants, and helper types are included in
  the inventory; no non-class symbol remains accidentally tied to
  `backend/engine/translator.py`.
- Application routes and dependency assembly use owning interfaces. Full
  backend and application compatibility suites pass while the now-unused
  TranslatorEngine file still exists.
- The V2 fixture generator constructs projects through the owning modules and
  no longer imports or instantiates TranslatorEngine.
- The import-inventory command returns no first-party consumer outside the
  compatibility file itself, and the broader module-attribute inventory finds
  no indirect `main.translator_engine` consumer.

### WU-F7 — delete TranslatorEngine

File scope:

- `backend/engine/translator.py` (delete)
- `backend/tests/test_translator_engine_state.py` (split/delete)
- `.github/workflows/ci.yml`
- `desktop/scripts/stage-runtime.mjs`

Definition of Done:

- TranslatorEngine and its private implementation tests are deleted; surviving
  behavior tests live at owning module interfaces.
- CI compile entries and desktop staging contain every replacement module and
  no deleted path.
- Repository-wide import/deletion searches, backend tests, application tests,
  desktop staged-runtime import checks, and V2 end-to-end pass.
- The deletion test proves behavior concentrated in the owning deep modules and
  was not recreated under a new facade name.

Human checkpoint: review PageEditor, ProjectWorkspace, glossary command, and
ProjectExporter interfaces before WU-F7 deletion.

## Dependency amendments

- WU-B1 depends on WU-A4.4.
- WU-B2 consumes the exact Pending contract closed by WU-A3.2.
- WU-C1 derives snapshot facts through the Snapshot Catalog behavior closed by
  WU-A3.4, never from stale Head-manifest summary fields.
- WU-F2 must preserve ADR 0002 and ADR 0004 and cannot change the one-CAS rule.
- WU-F3 depends on WU-A4.2.
- The original WU-F2 and WU-F3 are replaced by WU-F6 and WU-F7 above.
- Phase G and every original final verification gate remain unchanged.

## Verification cadence

For each work unit:

1. RED: add or identify a failing test through the owning module interface.
2. GREEN: implement only the named invariant cluster.
3. REFACTOR: run the deletion test and remove replaced behavior after its
   compatibility window permits deletion.
4. Run focused tests and static checks.
5. Run a fresh adversarial review limited to that work unit.
6. Commit only after the review passes.

### Stage-available integration matrix

At A3-Closeout run only checks that exist at that dependency point:

1. CI's current backend `py_compile` list and
   `python -m unittest discover -s backend/tests -t . -v`.
2. The current CI frontend commands: `test:config-persistence`,
   `test:local-port`, `test:project-artifact-state`,
   `test:review-workspace-state`, `test:task-event-state`,
   `test:task-connection`, `test:workflow-state`, and `build`.
3. Current desktop JavaScript syntax checks and `test:runtime-paths`.
4. The existing `test:v2-workspace` flow, diff/deletion checks, and CI's
   forbidden tracked-content/personal-path check.

At WU-A4.4 run that matrix plus `test_inference_backend`,
`test_translation_provider`, compilation of both new modules, and Coordinator
fake-adapter integration tests. It does not require ProjectView/frontend files
from Phase C, configuration fixtures from Phase E, or final package allowlists
from Phase G.

At the end of each later phase, add the tests and package entries created by
that phase. Only the pre-merge gate requires the complete final verification
matrix from the accepted 2026-07-15 plan.

## Plan-review gate record

The review gate reached its three-iteration maximum:

| Iteration | Feasibility | Completeness | Scope & Alignment |
| --- | --- | --- | --- |
| 1 | FAIL | FAIL | PASS |
| 2 | PASS | FAIL | PASS |
| 3 | FAIL | FAIL | PASS |

The final Feasibility and Completeness failures were the same issue: WU-F6's
consumer inventory was limited to `backend/` and therefore omitted
`scripts/create_canvas_test_fixture.py`, which imports TranslatorEngine and is
called by the mandatory V2 end-to-end flow. The current revision expands the
inventory to first-party backend, scripts, frontend, and desktop paths and
explicitly assigns that fixture generator to WU-F6.

No other blocking issue remained in the final reviews. Because the gate allows
at most three iterations, this correction did not receive a fourth independent
review and the plan is not represented as gate-approved. On 2026-07-17 the user
explicitly selected option 1 to adopt this corrected plan and resume execution.

## Immediate next action after approval

Resume from WU-A3.1. Fix and test Page Revision input integrity without changing
Pending or snapshot behavior. Then close A3.2 as a regression gate before
touching snapshot safety and snapshot-derived projections.
