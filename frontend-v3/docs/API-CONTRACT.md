# InkStage API contract

The wire contract is owned by `backend/main.py`, the canonical Page Document in
`backend/engine/translator.py`, and the domain models described in `CONTEXT.md`.
The old frontend's flattened editor objects are not HTTP document schemas.

## Transport and identity

- Development uses the documented Vite `/api`, `/output`, and `/ws` proxies.
- Electron supplies `window.mangaDesktop.runtime.apiBaseUrl` and `apiToken`.
- The documented Windows `start-v3.bat` launcher injects the chosen backend URL
  and token into Vite; it does not depend on port 8000 being free. Manual dev must
  provide matching `VITE_API_BASE_URL`/`VITE_API_TOKEN` when the backend uses auth.
  The desktop dev/package scripts currently select the legacy frontend by
  default; the v3 client accepts the same preload contract, but this does not
  constitute a v3 packaged-desktop release or Windows execution verification.
- HTTP requests use Bearer auth. WebSocket auth uses the subprotocols
  `manga-translator` and `auth.<token>`.
- `session_id` identifies the active project. `stored_name` / `page_id` identifies
  a page inside it. Encode both when building paths.
- Errors contain `detail`, either a string or an object with `code` and `message`.
- Image previews support `max_side`. Key image URLs by the corresponding source,
  blank or final artifact revision. Do not put `Date.now()` in render-time URLs.

## Import

`POST /api/upload` accepts multipart form data:

- One image or ZIP/CBZ: `file`.
- Multiple images / folder: repeated `files`, paired repeated `relative_paths`,
  and `folder_name`. Keep each image and its relative path in the same order.
- `review_mode` is optional; InkStage sends `auto`.

The response is a full project view. Directory drops must enumerate nested
entries and every directory-reader batch before constructing this form.

## Projects and artifact state

- `GET /api/projects` returns `{projects: [summary]}`.
- `POST /api/projects/{id}/restore` returns the full project view for that ID.
- `PATCH /api/projects/{id}` updates title/note and may return only a summary.
- `DELETE /api/projects/{id}` deletes the project.
- `GET /api/projects/{id}/snapshots` returns `{snapshots: [...]}`.
- `POST /api/projects/{id}/snapshots/{snapshot}/restore` creates an independent
  project. Navigate to the response's **new `session_id`**.
- `POST /api/projects/{id}/snapshots/{snapshot}/pin` accepts `{pinned: boolean}`.
- `POST /api/projects/{id}/base-images` takes a multipart `file` (image or ZIP/CBZ).
  Its `base_image_upload` result reports matched, unmatched and invalid inputs.

Full views include `session_id`, `project`, `images`, `translated_images`,
`config`, `workflow_stage`, `page_artifacts`, `download_url`,
`project_head_generation`, and `project_head_revision_id`.

Artifact schema version is 2. Each page artifact has `page_id`, `artifacts`
(source/recognition/blank/translation/final), and
`capabilities`. Both `page_artifacts[pageId]` and `images[].artifact_state` carry
page state. Project-level `workflow_stage` must not override per-page validity.
A typeset image is not evidence of human review.

Merge compact command responses into the existing project view. Do not replace
its image list with a page result. Reject another project's response and a head
older than the one already adopted. Editing can invalidate `download_url`.

## Canonical Page Document

`GET /api/pages/{project}/{page}/document` returns `{document: {...}}`.
A reproducible synthetic response is in `test-fixtures/page-document.json`.
Regenerate it with the documented backend Python environment and
`scripts/export_review_contract_fixture.py`; the script seeds recognition state,
substitutes the upstream TextBlock class, and uses a temporary APP_DATA_DIR.
It does not exercise OCR, a GPU, or an online translation provider.

A document contains:

- `page_id`, `dimensions: {width, height}`, `regions`, `erase_regions`.
- `source_image`, `base_image`, `preview_image`, `translated_image`: URL strings.
- `metadata: {document_version, revision, updated_at, review}`. `review.status`
  is `reviewed` or `unreviewed`; a current human mark may also include
  `reviewed_at`, `based_on_revision`, and an optional `reviewer`.

Each region contains:

- `region_id`, `page_id`, `kind`, `origin` (`automatic`, `user`, `derived`),
  `source_ids`, `bbox: [x1,y1,x2,y2]`, `polygon`, `direction`, `source_text`.
- `translation: {machine, edited, resolved}`.
- `recognition: {status, error, translation_status, translation_error}`.
- `style`: font style buckets/overrides, font key/family/path, effective and
  detected font sizes, size override, spacing, alignment, RGB colors, stroke
  width and rotation.
- `flags: {disabled, keep_original, translation_enabled, preserve_background}`.
- `audit` and `ocr_confidence`.

Direction may be `h`/`v`; the editor projects these as horizontal/vertical.
`region_id` must remain unique. Empty recognition strings do not by themselves
indicate an OCR failure. Local projections are derived data; retain the canonical
saved document separately from optimistic geometry and text/style drafts.

## Page commands and saving

`POST /api/pages/{project}/{page}/commands` accepts:

```json
{
  "config": {},
  "expected_page_revision": 4,
  "commands": [{"type": "update_translation", "region_id": "region-a", "text": "译文"}]
}
```

The expected revision comes from **`document.metadata.revision`**. Serialize
project edits and read the latest revision when each queued command starts.
Capture project/page identity when enqueueing, not after awaiting its response.

Supported command types:

- `update_source_text`, `update_translation`, `set_keep_original`,
  `disable_region`, `restore_region`.
- `update_region_bbox`, `update_font_size` (null clears an override),
  `update_text_direction`, `update_region_font`, `update_region_style`,
  `update_font_style`.
- `create_region`, `duplicate_region`, `merge_regions`, `delete_manual_region`,
  `recognize_manual_region`, `restore_manual_region`.
- `retry_region_ocr`, `restore_region_ocr_snapshot`, `set_review_status`.

`update_font_style.style` selects a font bucket: `gothic`, `mincho`, `rounded`,
`cartoon`, `handwritten`, `sfx`, or an empty automatic override. It does not accept
`bold italic`. Automatic regions are disabled/restored; `delete_manual_region`
is reserved for user/derived regions. `create_region` saves the region first;
OCR is the separate `recognize_manual_region` action.

`update_source_text` accepts `{region_id, text}`, including an explicit empty
string. `retry_region_ocr` accepts `{region_id}` for automatic and user-authored
regions. It updates source text and recognition status while retaining translation,
geometry and typography. OCR failure retains the existing content and returns
failed recognition metadata; HTTP success alone is not OCR success. The legacy
`recognize_manual_region` behavior remains separate.

Source corrections, OCR retries and OCR restores return
`ocr_snapshots: {[region_id]: {before, after}}`; single-region responses also
include `previous_ocr_snapshot` and `ocr_snapshot`. Undo/redo submits
`{type: "restore_region_ocr_snapshot", region_id, snapshot}` using the exact
returned snapshot. Snapshots carry version, page/region identity, and explicit
presence/value entries for the source override, recognition override and authored
region payload. They cannot change region origin or be applied to another page.
Redo restores the accepted result rather than invoking OCR again.

`set_review_status` accepts `{status: "reviewed" | "unreviewed", reviewer?}`.
It persists an explicit human mark without creating a rendered artifact. Content
edits and changes to recognition, blank, translation or final artifact revisions
invalidate the mark. Restoring a project or snapshot preserves the mark only for
the matching artifact state. This status does not bypass export readiness.

Responses are **compact**: session/head identity, `page_id`, `page_artifact`,
`document`, `revision`, `updated_region_ids`, optional `created_region_id`,
`created_region_payload`, `deleted_region_payload`, `recognized_region_payload`,
and compatibility projections. They need not contain `images` or `config`.

HTTP 409 may return `detail.code = page_revision_conflict` with
`expected_revision`, `actual_revision`, and the current `document`. Retain the
user's draft, show the conflict, and do not silently resubmit it. Refreshing the
saved baseline must not erase newer input. Undo history built on superseded
content must not overwrite an external edit.

Before navigation or workflow actions, finish pending edits and flush dirty
fields. A failed flush prevents navigation and leaves drafts available to retry.
A successful response acknowledges only the values that request submitted.

## Tasks

Start via WebSocket `/ws/translate/{project}` with `action`, `config` and optional
`target_stored_name`. Actions include `detect`, `translate`, `resume-translate`,
`translate-page` and `rerender`.

- `GET /api/tasks/{task}` returns a task snapshot.
- `GET /api/projects/{project}/task` returns `{task: snapshot_or_null}`.
- Reconnect with `{task_id, after_sequence}` to subscribe to an existing task.
- `POST /api/tasks/{task}/cancel` requests cancellation.

After a start command has been sent, an uncertain acknowledgement must not
cause another start. Recover by task ID and sequence. Terminal events include
completed, failed/error, cancelled and interrupted. Views must subscribe to the
same project and retain task feedback while routes change.

If the start command crossed the socket but the backend task ID has not arrived,
the cancel control must not claim that the server task was cancelled. Stop any
unsent client-side batch pages, keep the subscription alive, and wait for the
`start` event to supply the task ID; then issue exactly one
`POST /api/tasks/{task}/cancel`. If no acknowledgement arrives, recovery may
query the project task snapshot, but it must never send a second start command.

InkStage's pending-page rerender is an explicit client queue of page-scoped
`rerender` commands, selected after dirty edits are saved. Only successful
completion advances it; cancellation or failure stops later pages. Reconnection
to a completed task must advance at most once. Route navigation retains the
queue; closing/reloading the app retains the accepted backend task, but unsent
pages are not a persisted server batch. Do not describe them as one.

## Erase and repair

`POST /api/pages/{project}/{page}/advanced-erase` accepts an action and config.
Actions and scope are distinct:

- `local-selection`: selected rectangles/strokes, local processing.
- `local-advanced-preview`: whole-page LaMa preview; this action does not use
  submitted rectangle/stroke selections to narrow its scope.
- `local-advanced-apply`: apply a preview using `attempt_id`.
- `erase`: online repair, using the configured Ark/Seedream service.

Selections use `{x,y,width,height}` or `{x1,y1,x2,y2}`, not `{bbox: [...]}`.
Stroke entries use `{size, points: [{x,y}, ...]}`, not `radius` or array points.
`local_mask_mode` is `text` (text strokes) or `selection` (entire selected area).

`POST .../advanced-erase/suggest-selection` takes `point: {x,y}` normalized to
0–1 and returns `selection: {x,y,width,height,source,confidence}` normalized to
0–1. Convert coordinates using original dimensions, not only preview dimensions.

LaMa preview responses contain **`advanced_erase.attempt_id`** and
`advanced_erase.preview` URLs. Preview images are also available under
`.../advanced-erase/previews/{attempt}/{kind}`. Do not read a top-level attempt ID.
The separate `brush-edit` endpoint accepts `{operations: [...]}` with mode
`paint`/`restore`, RGB `color`, `points: [{x,y}]`, diameter `size`, and `feather`.
`coordinate_space: "normalized"` maps points to the blank image. Set
`size_space: "normalized"` to express size/feather as fractions of its shorter
side, so custom blank resolutions match the preview. Omitted `size_space`
retains the legacy pixel units.

## Settings and first run

- `GET /api/app/settings` returns `{settings}` with secret values redacted.
- Real keys include `translator`, `selected_translator`, `translator_model`,
  `target_lang`, `api_key`, `openai_base_url`, `openai_model`, `font_key`,
  `font_style_mode`, `style_font_keys`, `use_gpu`, `rerender_output_format`,
  image cleanup and advanced erase provider/model/key fields.
- `configured_secrets` reports whether saved credentials exist. Empty redacted
  key fields do not mean the saved credential is absent.
- `PATCH /api/app/settings` merges changes. `_clear_secrets` explicitly names
  credentials to remove; leaving a secret empty preserves its stored value.
- `POST /api/app/settings/validate` validates the submitted draft configuration.
- `GET /api/app/local-models/lama-large` returns `{model: {...}}`.
- `GET /api/status` only reports service/auth status, not complete model readiness.
- `GET /api/fonts` returns `{fonts: [...]}` with `id`, `label`, `name`, `source`,
  `url` and format metadata. Use font IDs for selection, URLs for browser loading.

## Glossary

All glossary entries use `source`, not `term`. Other fields include `id`,
`translation`, `replacement`, `category`, `note`, `source_kind`, and optionally
occurrence counts/evidence (`page_id`, `region_id`, source text and translation).

- `GET /api/projects/{id}/glossary?include_occurrences=true` returns `{glossary}`.
- `PUT .../glossary` takes `{entries: [...]}` and replaces accepted terms.
- `POST .../glossary/extract` takes config and optional `preview_only: true`.
  Preview-only extraction returns candidates without accepting or persisting
  them. Omitting the flag retains the old frontend's extraction behavior.
- `POST .../glossary/preview` previews the supplied `{entries}`.
- `POST .../glossary/apply` applies `{entries}` and returns updated project state
  plus changes. The submitted entries must match the user-visible preview.

Missing/non-array `entries` on save/apply is an error. Explicit `[]` means clear.
An entry requires both source and translation. Edits invalidate a prior preview;
a failed save must preserve its draft. Evidence navigation carries both page and
region identity back to review.

## Export and verification limits

`GET /api/download/{project}` exports results; `/blank` exports blank pages.
Only current artifacts qualify for result export.

Mock mode is a visual aid, not acceptance evidence for inference, persistence,
settings or task recovery. Its document and settings fixtures are exported from real API handlers. The demo
uses full per-page artifact shapes, real bundled font files, and explicit mixed
page readiness. Mutations, model calls and downloads return 501; the demo banner
explains the boundary and WebSockets never forward demo work to a real backend.
Unknown identities return 404. These cases are covered by test-mock-api.mjs.
Real-backend API checks, old/new workflow tests and current UI screenshots are
separate release requirements. Passing the helper tests alone does not close them.
