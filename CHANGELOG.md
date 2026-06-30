# Changelog

## Unreleased

- Removed bundled manga/comic assets, design exports, private planning files,
  and bundled repository fonts from the tracked project.
- Added loopback-by-default API startup, runtime API token checks, safer CORS
  defaults, security headers, request-size checks, and safer archive extraction.
- Redacted persisted API keys from settings responses while preserving saved
  secrets server-side.
- Added pinned upstream dependency metadata and a local requirements snapshot.
- Reworked Electron desktop staging to copy an explicit allowlist instead of
  recursively copying local backend and font directories.
- Added open-source governance documents and release guidance.
