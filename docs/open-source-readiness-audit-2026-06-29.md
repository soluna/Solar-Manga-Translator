# Open-Source Readiness Audit

- Last updated: 2026-06-30
- Scope: current working tree for public repository and future desktop releases
- Conclusion: the current source tree passed the 2026-06-30 local test and
  tracked-file audit pass. Public source release is gated mainly by the Git
  history decision; desktop installer publication still needs a clean release
  build, SBOM/checksum work, and dependency re-audit.

## Severity Model

| Level | Meaning | Release Rule |
| --- | --- | --- |
| P0 | Legal, privacy, secret, remote safety, or reproducibility blocker | Must be closed before making the repository public or publishing installers |
| P1 | High-impact release quality, supply-chain, or user-data risk | Should be closed before the first public release |
| P2 | Adoption, contributor, documentation, or maintainability gap | Can be staged after initial release if tracked |

## Executive Summary

Major P0 risks in the tracked tree have been addressed:

- Bundled manga/comic assets, design exports, private planning files, and bundled
  repository fonts were removed from the current tree.
- Local API startup now defaults to loopback with a per-session token for HTTP
  and WebSocket access.
- Persisted API keys are redacted in settings responses.
- Upload/archive handling now rejects traversal, oversized uploads, abnormal
  archives, and fake images.
- Upstream `manga-image-translator` is pinned by commit and requirements
  snapshot.
- Desktop staging now uses an allowlist and no longer copies repository fonts or
  the whole backend directory.
- Open-source governance files, license, notices, release checklist, and CI were
  added.

Remaining blockers are mostly release-process issues:

- The Git history may still contain removed media/fonts/personal metadata.
- Desktop binary release must account for the currently reported upstream Torch
  advisory before shipping an installer.
- Desktop binary release still needs clean-machine packaging, SBOM/checksums,
  and code-signing decisions.
- Any future release candidate must rerun the same secret/path/media scans after
  new files are added.

## P0 Findings

### P0-01 Git History May Still Contain Removed Media, Fonts, And Personal Metadata

Status: Open until history cleanup is confirmed.

Current tree mitigation:

- Deleted tracked comic/design media and font files.
- Added `.gitignore` coverage for fonts, design files, runtime caches, staging,
  virtual environments, and `.env.*`.
- Added CI checks to block tracked fonts, generated runtime directories, and
  common personal path patterns.

Remaining risk:

- Public Git history can still expose files that were removed from the current
  tree.
- Commit author metadata may expose personal email addresses.

Required closure:

1. Decide whether to rewrite history before making the repository public.
2. If yes, use a history rewrite tool to remove media/font/private paths and to
   normalize author metadata where needed.
3. Re-run secret scanning and path/media scans across all refs after rewrite.
4. Force-push only after explicitly accepting the disruption to existing clones.

### P0-02 License Baseline

Status: Closed for source tree, pending legal review if commercial distribution
is planned.

Current mitigation:

- Added `LICENSE` using GPL-3.0 text.
- Added `NOTICE` and `THIRD_PARTY_NOTICES.md`.
- Added upstream metadata in `backend/upstream.json`.
- Documented local patch entrypoint and requirements snapshot.

Remaining risk:

- Binary releases still need complete third-party dependency/license reporting
  for the packaged Python runtime.

Required closure for installers:

- Generate SBOM or equivalent license report.
- Include corresponding source and notices required by GPL-3.0-only.

### P0-03 Local API Exposure And Secret Handling

Status: Closed for default local startup.

Current mitigation:

- Backend and frontend startup scripts bind to `127.0.0.1`.
- FastAPI docs/OpenAPI are disabled unless explicitly enabled.
- State-changing API routes require a bearer token when `APP_API_TOKEN` or
  `MANGA_TRANSLATOR_API_TOKEN` is set.
- WebSocket sessions require an authenticated subprotocol token.
- Saved API keys are preserved server-side but redacted from API responses.
- CORS is explicit and no longer allows broad `null` origins by default.
- Security headers are set on API responses.
- Tests cover protected API access, WebSocket auth, media path behavior, and
  upload size enforcement.

Remaining risk:

- This is a local-first app, not an internet-facing multi-user service. Public
  deployment would need a separate auth/session design and reverse-proxy
  hardening.

### P0-04 Unsafe Upload And Archive Handling

Status: Closed for current upload paths.

Current mitigation:

- Chunked upload copy with maximum size enforcement and partial-file cleanup.
- Safe archive extraction that rejects traversal, absolute paths, special files,
  excessive member counts, excessive total/member size, abnormal compression
  ratios, and invalid image files.
- Unit tests cover valid image extraction, traversal rejection, archive limits,
  compression-ratio rejection, fake image removal, and upload limit cleanup.

Remaining risk:

- Long-running model tasks still need broader resource governance for public
  multi-user deployments.

### P0-05 Reproducible Upstream Installation

Status: Closed for source setup path.

Current mitigation:

- Added `backend/upstream.json` with the pinned upstream repository and commit.
- Added `backend/requirements-upstream.txt` snapshot.
- Rewrote `backend/install_deps.py` to clone/check out the pinned commit and
  apply tracked patches.
- Removed dynamic install from upstream `main` in startup scripts.

Remaining risk:

- The upstream requirements still include heavy ML dependencies and some exact
  compatibility pins. Release builds should periodically audit them.

### P0-06 Desktop Staging Could Package Private Local Files

Status: Closed for current staging script.

Current mitigation:

- Replaced recursive backend copy with explicit backend file/directory
  allowlist.
- Stages only upstream `manga_translator/`, `dict/`, and notice/readme files.
- Excludes fonts, models, examples, results, temp uploads, caches, `.git`, and
  generated Python bytecode.
- Release manifest avoids local absolute paths.
- Windows packaging runs `install_deps.py --prepare-only` before staging.

Remaining risk:

- The Python runtime is still copied from a local environment. A clean,
  reproducible runtime build remains a P1 release quality task.

## P1 Findings

### P1-01 Desktop Release Process Is Not Yet Production-Grade

Status: Open.

Needed before installer publication:

- Build in a clean Windows VM.
- Generate SBOM and license report.
- Publish SHA-256 checksums.
- Decide on Windows code signing.
- Install/uninstall test on a clean machine.
- Scan `desktop/resources-staging/` and final installer contents for secrets,
  personal paths, fonts, comic media, and unexpected large files.

### P1-02 Dependency Currency And Vulnerability Scanning

Status: Open.

Current 2026-06-30 result:

- `frontend` `npm audit --audit-level=moderate`: clean.
- `desktop` `npm audit --audit-level=moderate`: clean.
- `backend/.venv-mac` `pip-audit --local`: reports `torch 2.11.0` with
  `CVE-2025-3000` / `GHSA-rrmf-rvhw-rf47`. `pip-audit` did not report any fix
  version. The affected package is pulled in by the optional/heavy upstream ML
  runtime rather than by `backend/requirements.txt` alone.

Needed before installer publication:

- Re-run `pip-audit` in the clean release runtime.
- Prefer an upstream/fixed Torch release as soon as one is available.
- If no fixed Torch is available, document the local-only threat model and avoid
  positioning the packaged app as safe for untrusted remote execution.
- Review compatibility before changing upstream exact pins.

### P1-03 CI Is A Baseline, Not A Full Release Gate

Status: Partially closed.

Current mitigation:

- Added GitHub Actions for backend focused tests, frontend build, desktop script
  syntax checks, and forbidden tracked content checks.

Needed:

- Add full browser smoke tests in CI once runtime cost is acceptable.
- Add automated secret scanning.
- Add SBOM/license report generation for release workflows.

## P2 Findings

### P2-01 External User Documentation Can Still Improve

Status: Partially closed.

Current mitigation:

- Rewrote `README.md` for external users.
- Added `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, changelog,
  notices, issue templates, and release checklist.

Needed:

- Add screenshots using synthetic/public-domain fixtures only.
- Document model download behavior and disk/GPU requirements in more detail.
- Add a troubleshooting guide for common Windows CUDA/PyTorch issues.

### P2-02 Test Fixtures Should Stay Synthetic

Status: Closed for current tracked tree, keep monitoring.

Rule:

- Tests must use generated synthetic images or clearly licensed fixtures.
- Do not add real manga pages, translated pages, or screenshots containing
  recognizable copyrighted panels.

## Current Release Gate

Before making the repository public:

1. Run all tests in `docs/release-checklist.md`.
2. Run a tracked-file scan for secrets, personal paths, media, fonts, and large
   binary files.
3. Decide whether to rewrite Git history.
4. If history is rewritten, re-run all scans across all refs.
5. Push only the reviewed branch/state.

2026-06-30 local source-tree gate result:

- Frontend build and browser smoke tests passed.
- Backend focused security/file/engine tests passed.
- Desktop runtime staging completed and staging scans found no fonts, comic
  images, example env files, personal absolute paths, or unexpected media.
- Tracked/unignored file scans found no font binaries, comic images, `.pen`
  files, private keys, common API-key patterns, or personal absolute paths.
- Known remaining dependency audit item: upstream Torch advisory above.

Before publishing any installer:

1. Build in a clean Windows VM.
2. Inspect staging and installer contents.
3. Generate SBOM, notices, and checksums.
4. Complete install/uninstall/runtime validation.
