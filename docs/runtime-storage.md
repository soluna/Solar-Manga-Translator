# Runtime Storage Layout

All application-owned writable data is rooted at `.runtime` beside the source
checkout or packaged executable. `APP_DATA_DIR` may select another root, but
the application still keeps every derived directory below that one root.

```text
.runtime/
├── projects/                 Project manifests and editable page state
├── output/                   Current translated and blank-page output
├── models/                   Detection, OCR, inpainting, and offline translation models
├── fonts/
│   ├── system/               Copied bundled presets
│   └── custom/               User fonts
├── logs/
│   ├── electron/             Electron main-process logs
│   └── crash-dumps/          Electron crash reports
├── cache/
│   ├── electron/             Chromium network and disk cache
│   ├── huggingface/          Hugging Face and Transformers cache
│   ├── torch/                Torch Hub, extensions, and compiler cache
│   ├── cuda/                 CUDA compute cache
│   ├── npm/                  npm download cache used by the source launcher
│   └── pip/                  pip download cache used by the source launcher
├── temp/                     Python, native runtime, and upstream temporary files
├── electron/
│   ├── user-data/            Electron preferences and browser state
│   └── session-data/         Cookies, local storage, and session state
└── config/
    ├── settings.json
    ├── migration.json
    └── legacy-settings/      Preserved conflicting settings from old versions
```

The packaged Windows application configures Electron paths before its `ready`
event, and the backend configures Python and common model/runtime cache
environment variables before importing the inference runtime. The source
launcher applies the same environment before creating the virtual environment
or running pip/npm, so dependency downloads and extraction also stay below
`.runtime`.

## Compatibility with older versions

The startup migration detects project data under old repository folders and
the application-owned Windows locations in `%LOCALAPPDATA%` and `%APPDATA%`.
An old Electron/Chromium profile is detected even when it contains no project
data. The migration also detects the old `%TEMP%\manga-image-translator` cache.

The migration dialog offers two safe choices:

- **Migrate and clean old directories**: copy projects, output, models, fonts,
  and settings; verify every discovered project is present at the new root;
  archive conflicting settings; then delete only directories known to belong
  to this application.
- **Migrate and keep old directories**: perform the same compatibility copy
  without deleting the source.

The cleanup never deletes the parent system temporary directory and never
automatically deletes shared global Hugging Face or Torch caches, because other
software may use them. New releases stop writing to those shared locations.
After confirming that other applications do not need them, users may inspect
and clean those global caches manually.

## Windows notes

- Choose a writable installation directory. The application deliberately does
  not fall back to C-drive AppData when the selected directory is read-only.
- The Settings panel shows the exact storage, cache, temporary, and log paths
  and can open the unified data root in Explorer.
- Windows itself may still maintain installer records, shortcuts, GPU-driver
  caches, or crash infrastructure outside the project. Those are operating
  system or driver data rather than application-owned project/model/cache data.
