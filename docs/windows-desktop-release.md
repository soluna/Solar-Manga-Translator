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

- Source checkout: `<project>/.runtime/`
- Packaged app: `<install directory>/.runtime/`

`APP_DATA_DIR` can override this default. Versions that used
`%LOCALAPPDATA%/Solar-Manga-Translator/` or Electron's default
`%APPDATA%/Solar-Manga-Translator/` location are detected by the in-app
legacy-data migration flow. Complete that migration before deleting the old
directory. The in-app flow can copy and keep the old data, or copy, verify,
and clean only directories owned by this application. It also detects old
Electron/Chromium-only AppData directories that contain no projects.

Subdirectories:

- `projects/`
- `output/`
- `models/`
- `logs/`
- `cache/`
- `temp/`
- `fonts/`
- `electron/`
- `config/settings.json`

The selected install directory must be writable because it contains `.runtime/`.
Electron profiles and caches, Python temporary files, model-framework caches,
and pip/npm packaging caches are redirected into this tree. Shared legacy
Hugging Face or Torch caches are not automatically deleted because other
applications may still use them. See `runtime-storage.md` for details.

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
- Confirm `%APPDATA%`, `%LOCALAPPDATA%`, `%TEMP%`, and the user-profile cache
  root receive no new application-owned files during launch and one synthetic
  translation; all such writes must appear below `<install directory>/.runtime/`.
- Upgrade from a build with AppData projects and Electron-only cache data;
  verify both migration choices, project verification, and targeted cleanup.
- Confirm uninstall leaves or removes user data according to the published
  release note.

## Known Open Items

- Code signing is not configured yet.
- A fully reproducible Python runtime build is still needed.
- SBOM generation and installer checksum publication are still needed.
- The clean release runtime must pass dependency audit, or explicitly document
  any remaining upstream Torch advisory before installer distribution.
