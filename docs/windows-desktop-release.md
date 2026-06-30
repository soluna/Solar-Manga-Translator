# Windows Desktop Release Notes

The Windows desktop build packages the Vue frontend, the FastAPI backend, the
pinned upstream translation runtime, and a Python runtime into an Electron app.

## Support Matrix

- OS: Windows 10/11 x64
- GPU: NVIDIA CUDA recommended
- CPU-only mode: allowed, but performance is not a release promise

Document expected model download size and GPU memory requirements in every
public release note.

## Runtime Data

Writable data lives under:

- `%LOCALAPPDATA%/Solar-Manga-Translator/`

Subdirectories:

- `projects/`
- `output/`
- `models/`
- `logs/`
- `cache/`
- `config/settings.json`

The install directory should stay read-only application code.

## Build Flow

Prepare the Windows Python runtime first:

```powershell
cd backend
py -3.11 -m venv venv
venv\Scripts\python -m pip install --upgrade pip
venv\Scripts\python install_deps.py
venv\Scripts\python -m pip install -r requirements.txt
```

Build the desktop app:

```powershell
cd desktop
npm ci
npm run dist:win
```

The build script:

1. Builds `frontend/dist`.
2. Runs `backend/install_deps.py --prepare-only`.
3. Stages an allowlisted runtime subset into `desktop/resources-staging/`.
4. Runs `electron-builder`.

## What Must Not Be Packaged

- Non-allowlisted repository fonts or any user files from `fonts/custom/`
- Manga/comic source pages or translated outputs
- `.env` files or API keys
- Logs, temporary uploads, output folders, cache, screenshots, or fixtures from
  real user material
- Upstream `.git`, examples, model caches, test folders, or result folders
- Developer machine absolute paths in generated manifests

## Release Verification

Before distributing an installer:

- Run the full test set listed in `docs/release-checklist.md`.
- Inspect `desktop/resources-staging/release-manifest.json`.
- Scan `desktop/resources-staging/` for secrets, personal paths,
  non-allowlisted fonts, comic media, and large unexpected files.
- Confirm only `fonts/system/` presets are bundled and `fonts/custom/` is empty.
- Install in a clean Windows VM.
- Confirm the backend listens only on loopback and requires the runtime token.
- Confirm settings persist while saved API keys are redacted in renderer data.
- Confirm uninstall leaves or removes user data according to the published
  release note.

## Known Open Items

- Code signing is not configured yet.
- A fully reproducible Python runtime build is still needed.
- SBOM generation and installer checksum publication are still needed.
- The clean release runtime must pass dependency audit, or explicitly document
  any remaining upstream Torch advisory before installer distribution.
