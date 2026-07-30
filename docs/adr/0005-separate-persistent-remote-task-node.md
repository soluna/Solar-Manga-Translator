# ADR 0005: Separate the persistent remote task node from read-only diagnostics

- Status: Accepted
- Date: 2026-07-30

## Context

The existing LAN diagnostics service is deliberately read-only, uses short-lived tokens, and automatically stops. Reusing it for CUDA benchmarks and remote maintenance would make its name and guarantees false. The Windows computer also needs to retain task logs and results so another LAN computer can diagnose failures without repeated local interaction.

## Decision

Add a separate Remote Task Node with these boundaries:

- it is opt-in, uses an independent stable Bearer Token, and automatically starts with the application after it has been enabled once;
- it owns isolated Remote Task Bundles, persisted Remote Tasks, logs, cancellation, and downloadable artifacts under the application cache;
- it runs one task at a time to avoid GPU memory contention;
- command arguments are passed directly to a child process with `shell=False`, and the working directory must be `job`, `code`, `data`, or a named bundle;
- stopping and timeout terminate the spawned process tree;
- the existing short-lived Remote Diagnostics Service remains read-only and unchanged.

The module exposes a small application-facing interface: `status`, `enable`, `rotate_token`, `disable`, `start_if_enabled`, and `shutdown`. HTTP routing, persistence, process control, bundles, and job execution stay behind that interface.

## Consequences

After one Windows-side enable action, a trusted Codex task can upload a benchmark, execute it on CUDA, follow logs, stop it, and download results. The fixed Token is a host-execution credential and must be treated accordingly. Task data can accumulate in the cache; an authorized command can clean it, and a dedicated retention policy can be added later without changing the public task model.
