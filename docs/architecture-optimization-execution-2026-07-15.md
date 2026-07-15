# Architecture Optimization Execution Plan

Status: approved for staged execution on 2026-07-15.

This plan closes the accepted W0-W8 architecture direction with independently
verifiable work units. W0-W2 foundations already implemented on `main` are
re-verified and their remaining exit conditions are carried into later phases;
they are not assumed complete merely because their primary code landed. Each
phase must pass its deletion test before the next phase begins. Compatibility
projections remain until their replacement paths have passed migration and
end-to-end tests.

## Outcome

The finished system has the following ownership model:

- `WorkflowCoordinator` is the only public seam for Project Commands.
- `TaskManager` owns project leases, task lifecycle, journal, cancellation, and
  interrupted-task recovery.
- `ProjectWorkspace` owns Project Head, Pending Artifact Set, snapshots, the
  Project Artifact Store, and compatibility projection after a successful
  commit.
- `InferenceBackend` and `TranslationProvider` are the only adapters for
  upstream inference and external translation providers.
- `ProjectView` and `PageView` are the only remote project facts consumed by
  the frontend.
- Page Document owns all Text Regions. Legacy session override fields are
  derived compatibility data until their versioned migration completes.
- `RuntimeEnvironment` owns runtime readiness. Task execution never patches an
  upstream checkout.
- `create_app(dependencies)` owns application assembly; routes only translate
  transport input/output and errors.

## Selected interface

`WorkflowCoordinator` uses one typed-command entry point:

```python
class WorkflowCoordinator:
    async def execute(
        self,
        command: WorkflowCommand,
        *,
        progress: ProgressSink = NOOP_PROGRESS,
    ) -> WorkflowOutcome: ...
```

`WorkflowCommand` is a discriminated union of Project Commands such as
`DetectProject`, `TranslateProject`, `ResumeTranslation`, `TranslatePage`,
`RenderProject`, and `RenderPage`. Callers never pass a mutable session,
filesystem path, Project Head, Pending Artifact Set, or transaction handle.

The rejected alternatives are:

- returning an opaque prepared task, because that duplicates TaskManager's
  lifecycle interface;
- one public method per action, because its interface grows with workflow
  steps and recreates action dispatch in callers.

## Global invariants

1. A Project Command binds to one base Project Head generation.
2. A page-scoped Project Command checks Page Revision before inference or file
   writes.
3. A single-page command advances Page Document, Page Artifacts, Project State,
   and project manifest in one Project Head pointer swap.
4. A project-scoped command keeps Project Head unchanged until every required
   page succeeds. Verified partial work is retained only in a compatible
   Pending Artifact Set.
5. Pending reuse requires canonical action, normalized configuration and page
   scope fingerprint, base Head generation, and base Head revision identifier.
6. Project Head success defines command success. Compatibility projection,
   Pending cleanup, snapshot retention, or garbage-collection failure after
   the pointer swap produces a warning and never reports the command as failed.
7. Workflow outcomes and frontend read models are rebuilt from the committed
   Project Head, not from a mutable working session.
8. Progress delivery is best effort and cannot change transaction outcome.
9. Cancellation propagates through the running coroutine. It does not add a
   second cancellation interface to WorkflowCoordinator.
10. Legacy projects remain readable and migrate lazily. Compatibility state is
    not deleted without a versioned migration and restoration tests.

## Error model

- Invalid/unsupported command: rejected before task creation or storage writes.
- Missing project/page: classified not-found result; Project Head unchanged.
- Page Revision conflict: includes expected and actual revision; inference has
  not started.
- Project Head conflict: no automatic rebase; verified work may remain Pending.
- Workflow precondition failure: the requested operation is not currently
  valid, such as rendering a page without editable data.
- Inference/provider failure: Project Head unchanged; compatible completed
  project pages may remain Pending.
- Corrupt Project Artifact/Pending data: fail closed and preserve diagnostic
  evidence; never expose a partial result.
- Cancellation/interruption: Project Head unchanged and verified checkpoints
  retained according to the same Pending rules.

## Work-unit decomposition

### Phase 0 — Close W0-W2 foundation exit conditions

#### WU-0A — Shared command contract and stage compatibility matrix

File scope:

- `contracts/workflow-actions-v1.json`
- `backend/workflow_progress.py`
- `backend/tests/test_workflow_contract.py`
- `frontend/src/workflow-state.js`
- `frontend/scripts/test-workflow-state.mjs`

Definition of Done:

- Every canonical action, alias, scope, running/completed stage, and unknown
  action has a table-driven backend/frontend parity test sourced from the JSON
  contract fixture.
- Unknown actions fail before task creation and never fall through to full
  translation.
- The contract identifies `workflow_stage` as a compatibility projection; WU-C1
  proves ProjectView capabilities do not depend on it before its fallback use is
  deleted.
- Contract changes fail CI unless both runtimes agree.

Dependencies: current Plan B shared contract.

#### WU-0B — Page Artifact invalidation and recovery matrix

File scope:

- `backend/domain/project_artifacts.py`
- `backend/tests/test_project_artifact_state.py`
- `frontend/src/project-artifact-state.js`
- `frontend/scripts/test-project-artifact-state.mjs`
- `contracts/page-artifact-transitions-v1.json` (new)

Definition of Done:

- Table-driven tests cover recognize, translate, edit source, edit translation,
  edit layout/style, render, attach blank, delete/merge Text Regions, restore,
  and stale/final/export capability transitions.
- Unknown artifact schema/event fails closed.
- Backend and frontend derive the same Page Artifact readiness and invalidation
  result for every fixture.
- W0/W1 status is updated only after these tests pass; W2 remains open until
  WU-A2/A3 eliminate whole-directory commit ordering and WU-B2 covers startup
  cleanup.

Dependencies: current Page Artifact v2 and Project Head implementation.

#### WU-0C — Application-level foundation regressions

File scope:

- `backend/tests/test_translator_engine_state.py`
- `backend/tests/test_api_security.py`
- `frontend/scripts/test-v2-workspace-e2e.mjs`
- `frontend/scripts/create-v2-workspace-fixture.mjs`

Definition of Done:

- A page with zero Text Regions remains a valid recognized/blank page and does
  not block later project translation or export-capability derivation.
- A project command that completes only some pages leaves the previous Project
  Head visible and records only verified work in Pending.
- Retrying the same compatible failed command reuses verified pages; changing
  command scope/config/base Head does not.
- Backend application and V2 workspace regressions exercise these behaviors
  through public transport/workflow interfaces without private Engine mocks.

Dependencies: WU-0B.

### Phase A — WorkflowCoordinator and adapters

#### WU-A1 — Typed Project Command seam and task integration

File scope:

- `CONTEXT.md`
- `backend/workflow_coordinator.py` (new)
- `backend/tests/test_workflow_coordinator.py` (new)
- `backend/main.py`
- `backend/workflow_events.py`

Definition of Done:

- A typed Project Command rejects unknown actions and invalid page scope before
  starting a task.
- `main.py` no longer contains action-specific `if/elif` Engine dispatch.
- WorkflowCoordinator has one public `execute` interface.
- Task completion payload construction is owned behind the Coordinator seam.
- New tests use the Coordinator interface and a deterministic execution adapter;
  they do not replace TranslatorEngine private methods.
- Existing websocket request/event fields remain compatible.

Dependencies: Phase 0.

#### WU-A2 — Head/CAS Working Set and RenderPage tracer

File scope:

- `backend/workflow_coordinator.py`
- `backend/engine/project_workspace.py`
- `backend/engine/translator.py`
- `backend/tests/test_workflow_coordinator.py`
- `backend/tests/test_project_workspace.py`

Definition of Done:

- `RenderPage` validates Page Revision before invoking the render adapter.
- The private Working Set is materialized from Project Head/legacy state without
  exposing directories through the Coordinator interface.
- Render output, Page Document, Project State, and manifest become visible in
  one Head compare-and-swap.
- A failure before the pointer swap leaves the previous Head and compatibility
  projection unchanged.
- A failure after the pointer swap is reported only as a warning.
- A failing/disconnected progress sink cannot change the commit result; the
  command outcome remains readable from Project Head.
- The migrated single-page Engine transaction wrapper is deleted.

Dependencies: WU-A1.

Human checkpoint: review the first production use of the new deep module,
including its public interface, deletion test, and failure injection tests.

#### WU-A3 — Project-scoped Pending execution

File scope:

- `backend/workflow_coordinator.py`
- `backend/engine/project_workspace.py`
- `backend/engine/translator.py`
- `backend/tests/test_workflow_coordinator.py`
- `backend/tests/test_translator_engine_state.py`

Definition of Done:

- `RenderProject`, `DetectProject`, `TranslateProject`, and
  `ResumeTranslation` execute through the Coordinator.
- Verified completed pages are checkpointed in Pending and reused only for an
  exact command/base-Head match.
- Corrupt Pending data fails closed instead of being silently ignored.
- The old `_project_artifact_transaction` and its hidden session keys are
  deleted.
- The remaining main workflow tests use the Coordinator interface.

Dependencies: WU-A2.

#### WU-A4 — InferenceBackend and TranslationProvider adapters

File scope:

- `backend/inference_backend.py` (new)
- `backend/translation_provider.py` (new)
- `backend/workflow_coordinator.py`
- `backend/engine/translator.py`
- `backend/tests/test_workflow_coordinator.py`

Definition of Done:

- Upstream subprocess/import/cache knowledge is reachable only through the
  production InferenceBackend adapter.
- External provider authentication, request normalization, and error mapping
  are reachable only through TranslationProvider adapters.
- Deterministic fake adapters cover success, partial failure, retry, conflict,
  and cancellation through WorkflowCoordinator's interface.
- Migrated workflow public methods are removed from TranslatorEngine; it may
  remain temporarily for unmigrated editing/export algorithms only.

Dependencies: WU-A3.

### Phase B — Task ownership and durable recovery

#### WU-B1 — TaskManager as the only project lease owner

File scope:

- `backend/task_manager.py`
- `backend/main.py`
- `backend/engine/translator.py`
- `backend/tests/test_task_manager.py`
- `backend/tests/test_api_security.py`

Definition of Done:

- TaskManager is the only owner of project busy/lease state.
- Engine `active_sessions` and busy methods are deleted.
- Page editing and long-running Project Commands acquire the same lease model.
- Two concurrent commands for the same project fail deterministically; commands
  for different projects may run concurrently.

Dependencies: Phase A.

#### WU-B2 — Task journal and Pending reconciliation

File scope:

- `backend/task_manager.py`
- `backend/engine/project_workspace.py`
- `backend/main.py`
- `backend/tests/test_task_manager.py`
- `backend/tests/test_project_workspace.py`

Definition of Done:

- Task metadata, sequence, terminal result/error, and task identifier are
  persisted without translation text, secrets, or personal absolute paths.
- Startup converts previously running tasks to interrupted.
- Pending Artifact Sets reference their producing task identifier.
- Startup reconciliation retains compatible resumable Pending data and removes
  orphaned staging files without advancing Project Head.
- A project is immediately runnable after interrupted-state reconciliation.

Dependencies: WU-B1.

### Phase C — ProjectView/PageView and frontend ownership

#### WU-C1 — Backend read models

File scope:

- `backend/domain/project_artifacts.py`
- `backend/workflow_coordinator.py`
- `backend/engine/project_workspace.py`
- `backend/main.py`
- `backend/tests/test_project_artifact_state.py`

Definition of Done:

- ProjectView includes Project Head generation/revision and per-page PageView.
- PageView explicitly identifies source, blank, translation, and final Page
  Artifacts with revision, readiness, staleness, and URL.
- Capabilities derive from Page Artifacts, never from a global workflow stage.
- All Project Command terminal results return a ProjectView rebuilt from Head.

Dependencies: Phase B.

#### WU-C2 — Frontend project-workflow state and connection module

File scope:

- `frontend/src/project-workflow.js` (new)
- `frontend/src/composables/useTranslationTaskConnection.js`
- `frontend/src/composables/usePageCommandState.js`
- `frontend/scripts/test-project-workflow.mjs` (new)
- `frontend/package.json`

Definition of Done:

- One frontend module owns ProjectView, task subscription, refresh, and command
  submission.
- Existing task-connection and Page Command composables become internal seams
  of project-workflow instead of independent remote-state owners.
- Page URL/status selection uses PageView artifacts without original,
  inspection, and translated fallback merging.
- A completed task triggers one ProjectView refresh.

Dependencies: WU-C1.

#### WU-C3 — Integrate ProjectView into the application shell

File scope:

- `frontend/src/AppV2.vue`
- `frontend/src/project-workflow.js`
- `frontend/src/project-artifact-state.js`
- `frontend/src/workflow-state.js`
- `frontend/scripts/test-project-workflow.mjs`

Definition of Done:

- AppV2 consumes ProjectView/PageView as read-only derived state.
- Migrated original, inspection, translated arrays and URL/status fallbacks are
  deleted from AppV2.
- The existing project workflow and task reconnection behaviors remain covered.

Dependencies: WU-C2.

#### WU-C4 — Frontend review-document module

File scope:

- `frontend/src/review-document.js` (new)
- `frontend/src/AppV2.vue`
- `frontend/src/review-workspace-state.js`
- `frontend/scripts/test-review-document.mjs` (new)
- `frontend/package.json`

Definition of Done:

- One module owns the current Page Document, local draft, Page Revision,
  undo/redo, save state, and conflict refresh.
- Canvas geometry consumes read-only review-document data.
- AppV2 no longer owns remote Page Document or revision maps.

Dependencies: WU-C3.

#### WU-C5 — Frontend review-canvas ownership

File scope:

- `frontend/src/review-canvas.js` (new)
- `frontend/src/AppV2.vue`
- `frontend/src/review-workspace-state.js`
- `frontend/scripts/test-canvas-preview.mjs`
- `frontend/scripts/test-review-workspace-state.mjs`

Definition of Done:

- Canvas coordinate transforms, selection, drag, zoom, and preview rendering
  live behind one review-canvas interface.
- review-canvas consumes read-only review-document state and cannot write remote
  Page Document state directly.
- AppV2 no longer owns canvas geometry or pointer interaction state.

Dependencies: WU-C4.

#### WU-C6 — Frontend app-settings ownership

File scope:

- `frontend/src/app-settings.js` (new)
- `frontend/src/AppV2.vue`
- `frontend/scripts/test-config-persistence.mjs`
- `frontend/package.json`

Definition of Done:

- app-settings owns Provider Profile, runtime preferences, and UI Preferences.
- AppV2 no longer owns settings persistence or provider/runtime preference
  merging.

Dependencies: WU-C5. WU-E1 later replaces the persistence implementation
behind app-settings without changing its interface.

#### WU-C7 — Frontend project-library ownership and shell completion

File scope:

- `frontend/src/project-library.js` (new)
- `frontend/src/AppV2.vue`
- `frontend/scripts/test-project-library.mjs` (new)
- `frontend/package.json`

Definition of Done:

- project-library owns import, project history, snapshot entry points, archive,
  and restore actions.
- AppV2 only composes project-workflow, review-document, review-canvas,
  app-settings, and project-library modules.
- Existing import/history/snapshot/restore empty and error states remain
  user-visible and covered by tests.

Dependencies: WU-C6.

### Phase D — Page Document ownership and compatibility retirement

#### WU-D1 — Versioned Project State migration

File scope:

- `backend/domain/project_state.py`
- `backend/engine/project_workspace.py`
- `backend/workflow_coordinator.py`
- `backend/tests/test_project_state.py`
- `backend/tests/test_workflow_coordinator.py`

Definition of Done:

- A new Project State schema derives compatibility `manual_regions` and
  translation/style/layout values from committed Page Documents.
- Legacy v2 projects migrate lazily and idempotently.
- Unknown future versions fail closed.
- Migration/restore tests cover automatic, user, derived, and explicitly empty
  Text Region collections.

Dependencies: Phase C.

#### WU-D2 — Delete validated compatibility ownership

File scope:

- `backend/engine/translator.py`
- `backend/engine/project_workspace.py`
- `frontend/src/AppV2.vue`
- `backend/tests/test_translator_engine_state.py`
- `frontend/scripts/test-review-document.mjs`

Definition of Done:

- Runtime reads no longer overlay session override maps onto Page Document.
- New writes do not double-write Page Document ownership into legacy maps.
- Compatibility projections remain read-only only where required for old
  versions, then are deleted when fixture migration proves they are unused.
- Private implementation-coupled tests replaced by public interface tests are
  deleted.

Dependencies: WU-D1 and retained legacy fixtures.

### Phase E — Configuration, runtime, and application assembly

#### WU-E1 — Configuration scopes

File scope:

- `backend/domain/project_state.py`
- `backend/configuration.py` (new)
- `backend/main.py`
- `frontend/src/app-settings.js`
- `backend/tests/test_project_state.py`

Definition of Done:

- Provider Profile, App Settings, Project Workflow Config, and UI Preferences
  have separate versioned schemas and persistence locations.
- Project data stores provider references but no secrets.
- Restoring a project cannot overwrite current provider credentials or UI
  preferences.
- Existing mixed `last_config` and browser settings migrate idempotently into
  the four scopes. Explicit empty values retain their documented meaning.
- Unknown future versions and corrupt configuration fail closed with a
  recoverable user-facing error; fixtures cover old, empty, corrupt, and future
  versions.

Dependencies: Phase D.

#### WU-E2 — RuntimeEnvironment

File scope:

- `backend/runtime_environment.py` (new)
- `backend/runtime_bootstrap.py`
- `backend/model_manager.py`
- `backend/tests/test_runtime_bootstrap.py`
- `backend/tests/test_runtime_environment.py` (new)

Definition of Done:

- A verified runtime contract contains pinned upstream revision, patch version,
  target file hashes, model readiness, device, and model paths.
- Project Commands consume the verified contract and never patch the upstream
  checkout during task execution.
- Runtime unavailable/corrupt states are typed and covered by tests.

Dependencies: Phase A and WU-E1.

#### WU-E3 — Wire the verified runtime into inference

File scope:

- `backend/inference_backend.py`
- `backend/runtime_environment.py`
- `backend/workflow_coordinator.py`
- `backend/tests/test_inference_backend.py` (new)
- `backend/tests/test_workflow_coordinator.py`

Definition of Done:

- Production inference cannot start without a verified runtime contract.
- Fake inference remains independent of local models and external credentials.
- Missing/corrupt runtime fails before Page Artifact writes and leaves Project
  Head unchanged.

Dependencies: WU-E2.

#### WU-E4 — Application factory and route modules

File scope:

- `backend/main.py`
- `backend/application.py` (new)
- `backend/routes/` (new package)
- `backend/tests/test_api_security.py`
- `backend/tests/test_application_factory.py` (new)

Definition of Done:

- `create_app(dependencies)` can create two isolated application instances in
  one process.
- Project, page, task, and settings/runtime routes depend on a Project
  Application interface rather than module globals.
- Global mutable `SESSIONS` is deleted.
- Transport tests use a fake Project Application and do not patch module-global
  Engine state.

Dependencies: WU-E3 and Phase C.

### Phase F — Retire the TranslatorEngine facade

#### WU-F1 — Move editing, glossary, snapshot, and export ownership

File scope:

- `backend/page_editor.py` (new)
- `backend/project_exporter.py` (new)
- `backend/engine/translator.py`
- `backend/application.py`
- `backend/tests/test_project_operations.py` (new)

Definition of Done:

- PageEditor owns Page Command application, Page Document revision checks, and
  editing operations not already owned by WorkflowCoordinator.
- ProjectWorkspace owns snapshot/restore persistence; glossary orchestration is
  owned by WorkflowCoordinator/TranslationProvider; ProjectExporter owns result
  and blank archive construction.
- Public behavior tests use PageEditor, WorkflowCoordinator, ProjectWorkspace,
  and ProjectExporter interfaces instead of TranslatorEngine private methods.
- Corresponding public methods and one-line ProjectWorkspace forwarding methods
  are deleted from TranslatorEngine.

Dependencies: Phases A-E.

#### WU-F2 — Rehome Page Command consumers before facade deletion

File scope:

- `backend/page_editor.py`
- `backend/engine/translator.py`
- `backend/tests/test_page_region_commands.py`
- `backend/tests/test_project_operations.py`

Definition of Done:

- Page Command integration tests instantiate PageEditor/ProjectWorkspace rather
  than TranslatorEngine.
- Every first-party import consumer of TranslatorEngine is enumerated and either
  migrated or explicitly assigned to WU-F3.
- Full backend tests pass while the compatibility facade still exists.

Dependencies: WU-F1.

#### WU-F3 — Delete TranslatorEngine and private implementation tests

File scope:

- `backend/engine/translator.py` (delete)
- `backend/inference_backend.py`
- `backend/tests/test_translator_engine_state.py` (split/delete)
- `backend/tests/test_inference_backend.py`
- `.github/workflows/ci.yml`

Definition of Done:

- Remaining rendering/inference/provider/runtime algorithms live behind their
  owning module interfaces and production adapters.
- No production import references TranslatorEngine.
- No test mocks or calls a first-party private workflow implementation.
- Behavior and algorithm tests moved from the deleted monolithic test file run
  through owning public interfaces or focused pure algorithm modules.
- Deleting TranslatorEngine does not move its former public interface to another
  facade; callers use the smaller owning module interfaces.
- The same commit removes TranslatorEngine from CI's explicit compile list and
  adds every replacement backend module, so the branch never has a forward
  dependency on a later CI fix.

TranslationProvider behavior tests already moved behind WorkflowCoordinator in
WU-A4; configuration/provider parsing tests remain in their owning modules.

Dependencies: WU-F2.

### Phase G — Distribution and continuous verification

#### WU-G1 — Package and CI wiring for the new architecture

File scope:

- `.github/workflows/ci.yml`
- `desktop/scripts/stage-runtime.mjs`
- `desktop/scripts/test-runtime-paths.mjs`
- `desktop/package.json`
- `SERVICE-INVENTORY.md` (new)

Definition of Done:

- Desktop runtime staging includes every new backend module/package required by
  the assembled application and rejects a missing module.
- CI compile covers every first-party backend Python module introduced by this
  plan.
- CI runs every new frontend state test and the existing full test matrix.
- Desktop staged-runtime tests import the assembled backend application from the
  staged tree, not only the source checkout.
- SERVICE-INVENTORY records each deep module, its interface, production/test
  adapters, owner, and deletion status of the replaced path.

Dependencies: Phases 0-F. This work unit is updated incrementally when a phase
adds a new runtime file and receives its final deletion/coverage check here.

## Transport compatibility

No endpoint is removed during the migration. The existing translation WebSocket
continues to accept:

```json
{
  "action": "detect | translate | resume-translate | translate-page | rerender",
  "config": {},
  "target_stored_name": "optional-page-id",
  "expected_revision": "optional-page-revision",
  "task_id": "optional-existing-task-id",
  "after_sequence": 0
}
```

Page-scoped commands require `expected_revision` once the corresponding
frontend module is deployed. During the compatibility window, missing revision
is populated from the Head-read PageView inside the application module, never
from client-local counters. Existing event fields and reconnect behavior remain
compatible. Unknown actions remain rejected.

## Security considerations

- Bearer-token/CORS behavior remains unchanged.
- Project/page identifiers are validated before filesystem access.
- Project Command configuration is normalized before fingerprinting and must
  not persist provider secrets in Project State, Pending, snapshots, task
  journal, task events, or diagnostic payloads.
- Uploaded/archive file limits, traversal checks, and image validation remain
  mandatory.
- Expensive work remains limited to one active task per project. Existing local
  deployment behavior does not add an internet-facing rate limiter.
- Task journal errors use the existing redaction rules for secrets and absolute
  paths.

## User flows

### Normal flow

1. User imports images and receives a ProjectView built from Project Head.
2. User starts recognition; UI subscribes to task events.
3. Project Head remains unchanged until recognition completes.
4. UI refreshes ProjectView once and shows recognized Page Documents/blank
   artifacts.
5. User edits Text Regions with expected Page Revision.
6. User translates or rerenders a page/project.
7. On success UI refreshes one ProjectView and export capability derives from
   committed final Page Artifacts.

### Failure/resume flow

1. A project command fails after some pages complete.
2. User continues seeing the previous Project Head; no mixed result appears.
3. Task failure explains whether retry is possible.
4. Compatible retry reuses verified Pending pages and completes remaining pages.
5. Only the final successful command advances Project Head.

### Revision conflict flow

1. User submits an edit/render based on an older Page Revision.
2. Command fails before inference or writes.
3. UI receives current PageView, refreshes the Page Document, and offers retry.

### Restart flow

1. Backend stops while a task is running.
2. Startup journal reconciliation marks the task interrupted.
3. Project Head remains the last committed result.
4. Compatible Pending data is retained for retry; the project lease is free.

## External dependencies

- No new network service, credential, database, or paid provider is introduced.
- Existing upstream inference and configured translation providers remain in
  use behind adapters.
- Tests use deterministic fake adapters and synthetic images; full CI and V2
  end-to-end tests do not require paid calls or private manga content.

## Verification strategy

Every work unit follows RED -> GREEN -> REFACTOR through its public interface.
The orchestrator independently validates focused tests and then runs the
relevant complete suite before adversarial review.

Blocking final gates:

1. Python compile for every backend module listed by CI, including new modules.
2. `backend/.venv-mac/bin/python -m unittest discover -s backend/tests -t . -v`.
3. Every frontend test entry in `frontend/package.json`: canvas preview,
   configuration persistence, font preview, local port, project artifact state,
   region typography, review workspace state, task event state, task
   connection, workflow state, project-workflow, review-document,
   project-library, the existing `test:review-typography` entry, and V2 workspace
   end-to-end.
4. Frontend production build.
5. Desktop syntax, runtime-path, and staged-runtime import checks.
6. V2 Playwright workspace end-to-end flow, including upload, fake-adapter
   recognition, translation, edit, rerender, reconnect, and export.
7. Workflow contract parity test.
8. Failure-injection tests for Head pointer, compatibility projection, corrupt
   Pending, Page Revision conflict, cancellation, restart recovery, and a
   failing/disconnected progress sink that must not change commit outcome.
9. Configuration migration fixtures for old, explicit-empty, corrupt, and
   unknown-future versions.
10. Repository forbidden-content/personal-path check equivalent to CI.
11. Desktop package allowlist check proving every required new backend module is
    staged.
12. Application-level zero-Text-Region, partial-completion, and failed-retry
    regressions through public interfaces.

No work unit commits until focused tests pass, independent validation passes,
and a fresh adversarial review reports no blocking issue.

## Checkpoints

- After WU-A2: first new deep module in production; verify interface and
  transaction semantics.
- After Phase A: verify old workflow dispatch/transaction paths are deleted.
- After Phase B: verify restart behavior and project lease ownership.
- After Phase C: visual and behavior review of the workspace.
- Before WU-D2: verify legacy migration fixtures and compatibility removal.
- Before final merge: full cross-phase review and complete test matrix.
