# Third-Party Notices

## manga-image-translator

- Project: https://github.com/zyddnys/manga-image-translator
- License: GPL-3.0-only
- Pinned commit: `8e256098da2a85f6b99ec1f4cec3c28118c00bfd`
- Metadata: `backend/upstream.json`
- Requirements snapshot: `backend/requirements-upstream.txt`
- Local patch entrypoint: `backend/patch_pydensecrf.py`

The Windows desktop staging process includes only the allowlisted upstream
runtime subset needed by this application.

## Python And JavaScript Dependencies

Python dependencies are declared in:

- `backend/requirements.txt`
- `backend/requirements-upstream.txt`

JavaScript dependencies are declared in:

- `frontend/package.json`
- `frontend/package-lock.json`
- `desktop/package.json`
- `desktop/package-lock.json`

Before publishing a binary release, generate and review an SBOM or equivalent
dependency/license report for the packaged runtime.

## Assets, Fonts, Models

This repository does not ship manga/comic media, proprietary font binaries, or
model weights. Users are responsible for using lawful input material.

Bundled open-source fonts:

- Source Han Sans SC Regular, Medium, and Bold
- Source: https://github.com/adobe-fonts/source-han-sans
- License: SIL Open Font License 1.1, included at
  `backend/typefaces/OFL-1.1-Source-Han-Sans.txt`
