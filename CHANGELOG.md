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
