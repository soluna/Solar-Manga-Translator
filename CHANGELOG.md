# Changelog

## Unreleased

- Moved default projects, output, models, settings, caches, and logs from the
  operating-system user-data directory into the project's ignored `.runtime/`
  directory, while preserving `APP_DATA_DIR` overrides and legacy migration.
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
- Made Windows CUDA setup install pinned official PyTorch wheels directly,
  avoiding intermittent empty package-index responses, and added runtime
  platform details to the bootstrap log.
- Added PyTorch official/Aliyun mirror speed probing, automatic source
  fallback, inactivity timeouts, visible pip progress, and persistent pip
  installation logs for Windows setup. Active downloads no longer have a
  total time limit, and mirror downloads retain the official PyTorch SHA-256
  checks.
- Fixed the inference CLI contract so GPU and application model-directory
  options survive upstream argument parsing; tasks now report the effective
  runtime device.
- Split detect/OCR preparation from translation, mask generation, and LaMa
  inpainting. Detection no longer initializes an online translator or the
  inpainting model.
- Added staging transactions for detection and translation so failed retries
  and disconnected tasks preserve prior outputs, editable caches, archives,
  and persisted project state.
- Added resilient detector/OCR/LaMa model downloads with mainland-friendly
  mirrors, per-read timeouts, resume support, progress messages, and checksum
  rejection of corrupt mirror responses.
- Added core-model readiness and inference-command checks to first-run
  diagnostics.
- Unified engine task logs under the application log directory and expanded
  diagnostic-bundle redaction to remove personal paths and recognized or
  translated content.
- Added dependency fingerprints, PyPI/npm mirror fallback, and a verified
  upstream source-archive fallback so unchanged launches skip pip, npm, and
  Git network work. The application requirements are installed last so the
  pinned upstream snapshot cannot downgrade the FastAPI runtime.
- Added a mocked upload-to-export workflow contract test and a browser
  workspace E2E job to CI, including bounded runtime and reliable service
  process-tree cleanup on Linux runners.
- Added connection-independent project tasks with stable task IDs, resumable
  event streams, task status APIs, cancellation, and browser refresh recovery.
- Preserved the current workspace when a new upload fails and added browser
  regression coverage for damaged archives.
- Prevented migration and first-run dialogs from overlapping, and made
  "skip migration" persist for the current application version.
- Unified CUDA, Apple MPS, and CPU device selection across manual OCR,
  translation, and local inpainting.
- Replaced raw task exception output with stable error codes, user-facing
  recovery actions, redacted technical details, and full server-side logging.
- Added shared workflow descriptors for task events and frontend command
  labels, preparing the UI for clearer staged progress and P2 workflow polish.
- Made new projects default to a staged recognize-then-translate workflow,
  added stable project-stage buttons, task phase labels, clearer zero-region
  review entry text, and a batch-translation confirmation dialog.
- Extracted frontend task-event state derivation into a tested pure module so
  duplicate event filtering, task phase display, progress, and task status
  messages no longer live directly inside the main Vue page.
- Extracted frontend translation-task connection lifecycle into a tested
  module so WebSocket ownership, reconnect scheduling, task resubscription,
  and cancellation no longer live directly inside the main Vue page.
