# ADR 0006: Persist the local project task journal

- Status: Accepted
- Date: 2026-08-09

## Context

Project Head, snapshots, and Pending Artifact Sets survive a backend restart, but the local `TaskManager` previously kept task identity, events, and terminal errors only in memory. After a restart, a client could still see durable project artifacts while the task that produced or interrupted them appeared not to exist.

## Decision

Keep the existing small `TaskManager` application interface and put persistence behind it:

- store one versioned task state document and one append-only event journal per task under the application data root;
- compact event journals periodically and retain the existing 500-event subscription window;
- restore terminal tasks and their event cursors on startup;
- classify every recovered non-terminal task as `interrupted`, publish a durable typed terminal event, and release no synthetic busy lease;
- keep journal failures non-fatal to the running translation while logging the exact retained evidence path for diagnostics.

This journal records local task observation state. It does not attempt to resume a Python coroutine or replace the Pending Artifact Set used by whole-project command recovery.

## Consequences

- A browser can reconnect after a backend restart and receive an explicit interruption reason instead of a false “task not found”.
- Project busy state remains process-owned and cannot be stranded by a recovered record.
- Event persistence is append-only on the progress hot path; state files remain small and atomically replaced.
- Corrupt entries are preserved and reported in logs rather than silently rewritten.
