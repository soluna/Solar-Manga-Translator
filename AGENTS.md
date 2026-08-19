# Project Rules

- Read `README.md` and `CONTEXT.md` before changing architecture or runtime behavior.
- The top-level repository and `backend/manga-image-translator` are separate Git repositories. Check both statuses before declaring the project clean.
- Use the documented start/build scripts for supported platforms; do not invent parallel launch paths.
- Preserve `.runtime/projects` and `.runtime/output` as user data. Models and caches are regenerable but may be expensive to download.
- Keep frontend, desktop, backend, and contracts synchronized when an API or workflow changes.
- Run the smallest relevant backend/frontend tests, then the documented end-to-end path for user-visible changes.
- Never commit translation credentials, uploaded source books, or private outputs.
