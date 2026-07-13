# Use one page text-region model for every origin

The Page Document owns one ordered collection of Text Regions; `automatic`, `user`, and `derived` are provenance values rather than separate editing models. Recognition caches are derived materializations, while the schema-v2 `manual_regions` field remains only as a backward-compatible persistence and snapshot adapter until a future project-state migration can include page documents directly. All origins therefore share region IDs, page commands, overrides, typography, layout, rendering, revisions, and client payloads; only provenance-specific actions such as single-region OCR may differ.

## Consequences

- A missing editable cache cannot replace automatic regions with user-authored regions or resurrect a deleted region.
- A valid editable cache remains authoritative, including a valid empty recognition result.
- Existing projects and snapshots keep working without an eager project-state migration.
- Removing the compatibility adapter requires a separate versioned project-state and snapshot migration; it must not be done as an unversioned cleanup.
