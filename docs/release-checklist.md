# Release Checklist

Use this checklist before making the repository public or publishing a desktop
installer.

## Repository

- `git status` contains only intentional changes.
- No tracked manga/comic pages, translated outputs, screenshots containing
  recognizable comic pages, font binaries, model weights, `.env` files, logs, or
  local caches.
- `git grep` finds no personal absolute paths such as macOS home/volume paths,
  Windows user-profile paths, cloud-notes paths, or machine-local directories.
- Secret scanning reports no API keys, private keys, tokens, or passwords.
- `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, and `SECURITY.md` are present.

## Tests

- `python -m unittest discover backend/tests -v`
- `cd frontend && npm ci && npm run build`
- `cd frontend && npm run test:canvas-preview`
- `cd frontend && npm run test:review-workspace`
- `cd frontend && npm run test:v2-workspace`
- `node --check desktop/main.mjs desktop/preload.mjs desktop/scripts/dev.mjs desktop/scripts/stage-runtime.mjs desktop/scripts/package-win.mjs`
- `cd frontend && npm audit --registry=https://registry.npmjs.org --audit-level=moderate`
- `cd desktop && npm audit --registry=https://registry.npmjs.org --audit-level=moderate`
- `python -m pip_audit --local` in the prepared backend runtime

## Desktop Installer

- Build from a clean Windows environment.
- Run `backend/install_deps.py --prepare-only` before staging.
- Review `desktop/resources-staging/release-manifest.json`.
- Scan `desktop/resources-staging/` for secrets, personal paths, fonts,
  copyrighted media, large unexpected files, and `.git` directories.
- Install, launch, translate a synthetic fixture, close, and uninstall in a
  clean Windows VM.
- Publish installer SHA-256 checksums.

## Known Release Gaps

- Git history may still contain removed fonts/media/personal metadata until a
  dedicated history rewrite is performed.
- Code signing is not configured.
- SBOM generation is not automated.
- The current local Python audit reports an upstream Torch advisory without a
  `pip-audit` fix version; re-audit the clean release runtime before publishing
  any installer.
