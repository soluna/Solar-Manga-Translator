# Release Checklist

Use this checklist before making the repository public or publishing a desktop
installer.

## Repository

- `git status` contains only intentional changes.
- No tracked manga/comic pages, translated outputs, screenshots containing
  recognizable comic pages, non-allowlisted font binaries, model weights, `.env`
  files, logs, or local caches.
- `fonts/system/` contains only the allowlisted open-source presets and license;
  `fonts/custom/` contains no tracked user font files.
- `git grep` finds no personal absolute paths such as macOS home/volume paths,
  Windows user-profile paths, cloud-notes paths, or machine-local directories.
- Secret scanning reports no API keys, private keys, tokens, or passwords.
- Reachable Git history has been scanned for forbidden assets, private paths,
  and common secret patterns.
- GitHub exposes only reviewed release branches.
- `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, and `SECURITY.md` are present.

## Tests

- `python -m unittest discover -s backend/tests -t . -v`
- `cd frontend && npm ci && npm run build`
- `cd frontend && npm run test:config-persistence`
- `cd frontend && npm run test:canvas-local`
- `node --check desktop/main.mjs`
- `node --check desktop/preload.mjs`
- `node --check desktop/runtime-paths.mjs`
- `node --check desktop/scripts/dev.mjs`
- `node --check desktop/scripts/test-runtime-paths.mjs`
- `node --check desktop/scripts/stage-runtime.mjs`
- `node --check desktop/scripts/package-win.mjs`
- `cd desktop && npm run test:runtime-paths`
- `cd frontend && npm audit --registry=https://registry.npmjs.org --audit-level=moderate`
- `cd desktop && npm audit --registry=https://registry.npmjs.org --audit-level=moderate`
- `python -m pip_audit --local` in the prepared backend runtime

## Desktop Installer

- Build from a clean Windows environment.
- For the source workflow, delete `backend\venv`, run `start.bat`, and confirm
  the complete dependency bootstrap succeeds before testing an upgrade.
- Run `backend/install_deps.py --prepare-only` before staging.
- Review `desktop/resources-staging/release-manifest.json`.
- Scan `desktop/resources-staging/` for secrets, personal paths,
  non-allowlisted fonts, copyrighted media, large unexpected files, and `.git`
  directories.
- Confirm staging contains `fonts/system/` presets and an empty
  `fonts/custom/`, never the developer's local custom fonts.
- Install, launch, translate a synthetic fixture, close, and uninstall in a
  clean Windows VM.
- On an NVIDIA machine, confirm `runtime_bootstrap.py --json` reports the
  expected CUDA build and supported GPU architecture before translation.
- On Windows x64 with Python 3.10 and 3.11, confirm the CUDA bootstrap selects
  the pinned official wheel URLs without package-index version discovery.
- Test both the PyTorch official source and Aliyun mirror: confirm speed-based
  ordering, visible progress, timeout handling, fallback, official SHA-256
  verification, and bootstrap logging.
- Block the official GitHub/Hugging Face model sources and confirm detector,
  OCR, and LaMa downloads switch to a mirror, resume partial files, reject
  checksum mismatches, and remain under the application model directory.
- Start a previously translated project with an invalid translation API key;
  confirm a failed retry preserves the previous images, cache, archive, and
  project stage.
- Confirm detect-only completes without a translation API key and does not
  load LaMa or enter mask/inpainting stages.
- Start a long detection task, disconnect or refresh the browser, and confirm
  the task continues and the UI resumes from the last event.
- Stop a running task from the UI and confirm the model process exits, GPU
  memory is released, the project leaves busy state, and prior outputs remain.
- Upload a damaged archive while another project is open and confirm the
  current project, page list, edits, and export state remain available.
- Publish installer SHA-256 checksums.

## Known Installer Release Gaps

- Code signing is not configured.
- SBOM generation is not automated.
- Re-audit the clean release runtime and confirm it keeps
  `torch>=2.12.1` before publishing any installer.
