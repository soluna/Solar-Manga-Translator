# Manga Auto-Translator WebUI

Manga Auto-Translator WebUI is a local web and desktop interface around
[`manga-image-translator`](https://github.com/zyddnys/manga-image-translator).
It focuses on translating user-provided manga/comic pages locally: upload
images or a `.zip`/`.cbz`, run OCR/translation/inpainting/typesetting, review
pages, and export the translated result.

This project is currently optimized for Windows machines with NVIDIA CUDA, but
the web development flow also works on macOS/Linux for UI and API testing.

## Important Release Notes

- No manga pages, translated comic files, proprietary fonts, model weights, API
  keys, or private user data are part of this repository.
- Use only source images you own or are legally allowed to process and share.
- The app discovers operating-system fonts and user-installed custom fonts at
  runtime. The repository intentionally does not ship font binaries.
- AI model files are downloaded or provided by the user at runtime and should
  stay in the local app data directory, not in Git.
- The integrated upstream engine is GPL-3.0-only; this repository is licensed
  under GPL-3.0-only as well.
- Desktop installers are not release-ready until the packaged Python runtime is
  re-audited. The latest local audit still reports a Torch advisory from the
  upstream ML dependency stack with no fix version from `pip-audit`.

## Features

- Upload single images, folders as archives, `.zip`, and `.cbz` files.
- Translate with local or remote translators supported by the upstream engine.
- Stream page progress while long-running jobs continue in the backend.
- Review and edit translated regions in the browser UI.
- Persist local settings without returning saved API keys to the frontend.
- Run as a browser-based local app or through the Electron desktop shell.

## Requirements

- Git
- Python 3.10 or 3.11
- Node.js 18 or newer
- Windows users: NVIDIA GPU and CUDA-compatible PyTorch are recommended for
  practical performance.

First-time setup can download several gigabytes of Python packages and model
files, depending on the translators/inpainters/OCR backends you use.

## Quick Start

### Windows

Run:

```bat
start.bat
```

The script creates `backend/venv`, installs backend dependencies, checks out the
pinned upstream `manga-image-translator` commit, applies local runtime patches,
installs frontend dependencies, and launches a managed browser session.

### macOS Development

Run:

```bash
chmod +x start.mac.sh
bash ./start.mac.sh
```

This uses `backend/.venv-mac` so it does not touch the Windows-oriented
`backend/venv`.

### Linux Or Manual Local Run

Run:

```bash
chmod +x start.sh
./start.sh
```

All default local launch scripts bind the backend and frontend dev server to
`127.0.0.1` and generate a per-session API token for browser requests.

## Manual Development Setup

Backend:

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python install_deps.py
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm ci
VITE_API_BASE_URL=http://127.0.0.1:8000 VITE_API_TOKEN=<token> npm run dev -- --host 127.0.0.1
```

For desktop development:

```bash
cd desktop
npm ci
npm run dev
```

## Pinned Upstream Engine

The upstream engine is prepared by `backend/install_deps.py`.

- Metadata: `backend/upstream.json`
- Requirements snapshot: `backend/requirements-upstream.txt`
- Local patches: `backend/patch_pydensecrf.py` and `backend/patched_*.py`

Do not replace this with an unpinned `main` branch install. Reproducible public
builds depend on the fixed commit and tracked requirements snapshot.

## Security Defaults

- Backend docs/OpenAPI are disabled unless `APP_ENABLE_API_DOCS=1`.
- Local APIs default to loopback hosts.
- `APP_API_TOKEN` or `MANGA_TRANSLATOR_API_TOKEN` protects state-changing API
  routes and WebSocket sessions.
- Saved API keys are redacted in API responses; the frontend sees only whether
  a secret is configured.
- Upload and archive extraction paths enforce size, traversal, file type, and
  image validation checks.

See `SECURITY.md` for reporting guidance.

## Testing

Backend unit tests:

```bash
python -m unittest discover backend/tests -v
```

Frontend build:

```bash
cd frontend
npm ci
npm run build
```

Frontend/browser smoke tests:

```bash
cd frontend
npm run test:canvas-preview
npm run test:review-workspace
npm run test:v2-workspace
```

Desktop script checks:

```bash
node --check desktop/main.mjs
node --check desktop/preload.mjs
node --check desktop/scripts/dev.mjs
node --check desktop/scripts/stage-runtime.mjs
node --check desktop/scripts/package-win.mjs
```

## Windows Desktop Packaging

The desktop package is experimental. Build on Windows after preparing
`backend/venv`:

```powershell
cd desktop
npm ci
npm run dist:win
```

The staging script copies only an allowlisted runtime subset. It intentionally
excludes local fonts, models, examples, temporary uploads, output folders,
ignored files, and the upstream `.git` directory.

Review `docs/release-checklist.md` before publishing any installer.

## Repository Layout

- `backend/`: FastAPI app, runtime paths, settings handling, upload validation,
  and upstream engine integration.
- `frontend/`: Vue/Vite web UI and browser smoke tests.
- `desktop/`: Electron desktop shell and Windows packaging scripts.
- `docs/`: release/audit notes for public distribution.
- `scripts/`: local helper scripts that are safe to publish.

## Contributing

Please read `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` before sending changes.
Do not commit copyrighted comic pages, translated manga output, font binaries,
model weights, local logs, personal paths, `.env` files, or API credentials.

## License

GPL-3.0-only. See `LICENSE` and `THIRD_PARTY_NOTICES.md`.
