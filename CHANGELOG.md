# Changelog

## Unreleased

- Removed bundled manga/comic assets, design exports, private planning files,
  and non-redistributable fonts from the tracked project.
- Added three OFL-licensed Source Han Sans SC presets while keeping
  user-provided fonts under the ignored `fonts/custom/` directory.
- Added loopback-by-default API startup, runtime API token checks, safer CORS
  defaults, security headers, request-size checks, and safer archive extraction.
- Redacted persisted API keys from settings responses while preserving saved
  secrets server-side.
- Added pinned upstream dependency metadata and a local requirements snapshot.
- Reworked Electron desktop staging to copy an explicit allowlist, including
  only approved preset fonts instead of local custom fonts.
- Added safe project path validation, fresh pinned-upstream setup, and Windows
  `%LOCALAPPDATA%` migration coverage.
- Added open-source governance documents and release guidance.
- Added hardware-aware PyTorch setup for Windows/Linux, including RTX 50
  diagnostics and explicit CUDA wheel selection.
- Fixed OpenAI Compatible provider, Base URL, model, and secret persistence
  across application restarts.
- Decoupled manual region creation from OCR and translation so failed
  recognition no longer removes the user's box.
- Added rotating persistent logs, log-folder access, and redacted diagnostic
  bundle export.
- Added first-run runtime checks, dependency failure stops, and dynamic backend
  port selection for the managed Windows launcher.
- Fixed clean Windows setup by removing the unused `torchaudio` package from
  the PyTorch runtime plan; its pinned CUDA 13 wheel was not published.
- Changed the Windows bootstrap log timestamp to a locale-independent format
  so Chinese weekday names are not written as mojibake.
