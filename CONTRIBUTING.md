# Contributing

Thank you for helping improve Solar-Manga-Translator.

## Ground Rules

- Do not commit copyrighted manga/comic pages, translated outputs, screenshots
  containing recognizable copyrighted pages, font binaries, model weights,
  `.env` files, API keys, local logs, or personal machine paths.
- Keep upstream `manga-image-translator` pinned through `backend/upstream.json`
  and `backend/requirements-upstream.txt`.
- Prefer small, focused changes with tests when behavior changes.
- Keep local-only caches and generated files ignored.

## Development Setup

Backend:

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python install_deps.py
```

Frontend:

```bash
cd frontend
npm ci
npm run build
```

## Tests

Run relevant tests before opening a pull request:

```bash
python -m unittest discover backend/tests -v
cd frontend && npm run build
```

For UI/workflow changes, also run the Playwright smoke scripts listed in
`README.md`.

## Pull Requests

Include:

- What changed and why.
- Tests run.
- Any migration or compatibility notes.
- Confirmation that no private/copyrighted assets or secrets were added.

## License

By contributing, you agree that your contribution is provided under the
repository license, GPL-3.0-only.
