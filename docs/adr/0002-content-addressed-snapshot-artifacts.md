# Store snapshot artifacts by content hash

## Status

Accepted on 2026-07-13.

The snapshot-only storage boundary was generalized to Project Head and Pending Artifact Sets by ADR 0004.

## Context

Project snapshots previously stored mutable output filenames and editing overrides, then copied the project's current rerender cache during restoration. After later recognition, cleanup, page edits, or output replacement, restoring an old snapshot could therefore combine an old manifest with current files. Copying a complete private directory for every snapshot would fix consistency but make storage grow with snapshot count even when most pages did not change.

## Decision

Every new snapshot captures its source images, referenced translated images, Page Documents, and editable rerender cache as an immutable artifact bundle. Each file is addressed by its SHA-256 digest in a project-local blob store, and the snapshot manifest maps logical paths to blob digests and sizes.

Restoration verifies every referenced digest before copying it into a new project. Snapshot retention garbage-collects blobs that are no longer referenced by any retained manifest. Logical paths are validated before capture and restore. Legacy snapshots without an artifact bundle keep their previous compatibility path.

This bundle is the snapshot consistency boundary. It does not yet replace the task staging transaction or make page artifact files content-addressed during normal editing.

## Consequences

- A snapshot restores the files that existed when it was created, not files currently present under the source project.
- Identical files across snapshots occupy one blob. Files whose size, modification time, and change time match the previous bundle reuse its verified blob reference; changed files are rehashed.
- Missing, corrupted, or path-invalid blobs stop restoration with a classified project-state error instead of producing a partial project.
- New-project restoration copies verified blobs; it does not expose blob paths as runtime page paths.
- W2 remains incomplete until normal task commits replace whole-directory staging with per-page immutable revisions and an atomic manifest pointer.
