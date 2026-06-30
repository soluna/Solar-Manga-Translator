# Solar-Manga-Translator Desktop

This directory contains the Electron shell for the local Windows desktop build.
Electron owns the window and process lifecycle; the translation work still runs
in the local Python backend.

## Goals

- Start from a desktop window without asking end users to run terminal commands.
- Keep projects, output, logs, models, cache, and settings in the user data
  directory instead of the application install directory.
- Inject the local backend URL and per-session API token into the renderer.
- Package only allowlisted runtime files.

## Scripts

- `npm run dev`: start Vite and Electron for local development.
- `npm run stage-runtime`: stage the frontend build, allowlisted backend files,
  pinned upstream runtime subset, and Python runtime.
- `npm run dist:win`: build the Windows NSIS installer.
- `npm run dist:win:dir`: build an unpacked Windows app directory.

## Development

```bash
cd desktop
npm ci
npm run dev
```

The dev script starts the frontend dev server on `127.0.0.1`, then opens an
Electron window. The backend is launched through `backend/desktop_server.py`.

## Windows Packaging

Build on Windows after preparing `backend/venv`:

```powershell
cd desktop
npm ci
npm run dist:win
```

The packaging flow runs:

1. `frontend` production build.
2. `backend/install_deps.py --prepare-only` to ensure the pinned upstream
   checkout is patched.
3. `desktop/scripts/stage-runtime.mjs`.
4. `electron-builder`.

The staging script copies only allowlisted bundled typefaces. It does not copy
local fonts, local models, examples, temporary uploads, output folders, or the
upstream `.git` directory.

## Runtime Data

Desktop builds use:

- Windows: `%LOCALAPPDATA%/Solar-Manga-Translator/`

Expected subdirectories:

- `projects/`
- `output/`
- `models/`
- `logs/`
- `cache/`
- `config/`

Review `docs/release-checklist.md` before publishing any installer.
