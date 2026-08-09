# ADR 0007: Use one Page Document projection in the editor

- Status: Accepted
- Date: 2026-08-09

## Context

The page editor requested a translation-review projection and a style projection independently. Both were derived from the same Page Document, but the frontend stored two mutable page arrays and merged them by page and region identity. That doubled document loading and network work, and made response ordering part of the editor's correctness model.

The backend Project Head already provides a monotonic generation and revision identity, but complete client payloads did not consistently expose it and the frontend discarded the identity when it was present.

## Decision

- Expose one `/api/page-regions/{project_id}` editor projection containing translation, typography, style, revision, and override data from a single Page Document pass.
- Keep the older review/style endpoints as compatibility adapters.
- Let the frontend own one `inspectionPages` collection; Page Command results replace a page in that collection.
- Include project identity plus Project Head generation and revision in complete project, Page Command, and unified inspection payloads.
- Reject a delayed payload when it belongs to another active project, its Head generation regresses, or the same generation names a different revision.

## Consequences

- Opening or refreshing an editor page performs one document build and one HTTP request instead of two.
- The frontend no longer needs a region-level merge algorithm for two competing projections.
- Delayed task completion responses cannot roll the visible project state behind an already-observed Project Head.
- Compatibility clients can migrate independently without changing the authoritative Page Document schema.
