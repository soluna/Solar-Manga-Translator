# Use one page text-region model for every origin

The Page Document owns one ordered collection of Text Regions; `automatic`, `user`, and `derived` are provenance values rather than separate editing models. Recognition caches are derived materializations, while the schema-v2 `manual_regions` field remains only as a backward-compatible persistence and runtime-rendering adapter until a future project-state migration can rebuild that adapter from Page Documents. All origins therefore share region IDs, page commands, overrides, typography, layout, rendering, revisions, and client payloads; only provenance-specific actions such as single-region OCR may differ.

## Consequences

- A missing editable cache cannot replace automatic regions with user-authored regions or resurrect a deleted region.
- A valid editable cache remains authoritative, including a valid empty recognition result.
- Existing projects and snapshots keep working without an eager project-state migration.
- Page Commands use the Page Revision as an optimistic-concurrency boundary; stale commands fail instead of overwriting newer region state. Commands are serialized per project because page edits also update project-level override maps and manifests. ADR 0004 makes Project Head the commit boundary: failure before its pointer swap leaves the prior revision visible, while rebuildable compatibility projections may lag without undoing a successful edit.
- New snapshots already include Page Documents, but removing the compatibility adapter still requires a versioned project-state/runtime migration; it must not be done as an unversioned cleanup.
