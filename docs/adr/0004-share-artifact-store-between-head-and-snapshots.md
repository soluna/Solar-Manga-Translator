# Share one artifact store between the project head and snapshots

## Status

Accepted on 2026-07-15.

## Context

Snapshot Artifact Bundles already store immutable files by content hash, while normal project commands still write mutable output and cache directories through whole-directory staging. Adding a second revision store for normal commands would duplicate hashing, verification, retention, and recovery rules. Committing every small edit as permanent user-visible history would also make project history noisy and storage growth difficult to explain.

Whole-project commands introduce a second consistency concern: if a 100-page command fails after 79 pages, exposing those pages immediately would mix new and old results. Discarding them would preserve consistency but waste completed work.

## Decision

The Project Head, snapshots, and Pending Artifact Sets reference immutable files in one project-local Project Artifact Store.

Only the Project Head and snapshots are durable retention roots. Internal edit revisions do not become user-visible history unless a snapshot references them. Files with no Project Head, snapshot, or resumable Pending Artifact Set reference may be garbage-collected.

A single-page command advances the affected page and the project manifest atomically. A whole-project command builds a Pending Artifact Set and advances the Project Head only after every required page succeeds. Failure or interruption leaves the previous Project Head visible while retaining verified pending revisions for a compatible resume.

Legacy projects and snapshots remain readable through their existing compatibility paths and migrate lazily when a new revision is committed.

## Consequences

- Current and historical files use one hashing, verification, and path-validation implementation.
- Identical content is stored once even when referenced by the Project Head and multiple snapshots.
- A failed whole-project command cannot expose a mixture of new and old results.
- Completed pending pages can be reused after interruption without being presented as committed output.
- Garbage collection must consider Project Head, snapshot, and resumable Pending Artifact Set references together.
- The compatibility path remains until legacy project and snapshot migrations are proven in production.
