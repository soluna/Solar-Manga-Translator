# Security Policy

## Supported Versions

This project is pre-1.0. Security fixes are applied to the default branch unless
a release branch is explicitly announced.

## Reporting a Vulnerability

Please do not open a public issue for a vulnerability that could expose user
data, credentials, local files, or remote execution paths.

Use GitHub's private vulnerability reporting when available, or contact the
maintainers through a private channel listed on the repository page.

Include:

- Affected version or commit.
- Steps to reproduce.
- Impact and affected platform.
- Any proof-of-concept details needed to validate the issue.

Do not include real API keys, private user files, copyrighted manga pages, or
other sensitive material in a report.

## Security Baseline

- Local servers should bind to loopback by default.
- State-changing API routes and WebSockets should require a per-session token.
- Saved API keys must be redacted in API responses and logs.
- Upload and archive handling must validate size, paths, file type, and image
  structure.
- Release artifacts must be scanned for secrets, personal paths, fonts,
  copyrighted media, and unexpected large files before publication.
