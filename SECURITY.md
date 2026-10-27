# Security Policy

## Supported versions

This is a personal/portfolio project. Security fixes are applied to the `main`
branch only; no long-term support or production SLA is provided.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Preferred channel: GitHub **Private vulnerability reporting** (Security →
Report a vulnerability). If that is unavailable, contact the repository owner
through the contact method listed on their GitHub profile, and share details
privately.

Please include:

- affected version / commit;
- a short description and impact;
- reproduction steps or a proof of concept;
- any suggested fix, if you have one.

Please do **not** include real API keys, passwords, personal data, or real
legal documents in a report. Use synthetic examples.

## Scope

Examples of in-scope issues:

- authentication / authorization bypass;
- SQL injection, XSS, SSRF, path traversal;
- cross-user data access in history records;
- secret leakage in Git history or build artifacts;
- unsafe Docker/Nginx defaults;
- prompt-injection paths that bypass server-side controls;
- dependency vulnerabilities with a realistic exploit path.

Out of scope:

- the general accuracy of LLM-generated legal text (see `DISCLAIMER.md`);
- missing legal review workflow features;
- vulnerabilities requiring a compromised local machine/browser;
- reports that only contain automated scanner output without an impact
  analysis.

## Known dependency advisories

- **PyJWT** is used for JWT signing/verification. `python-jose` was removed
  because version 3.5.0 has an unresolved algorithm-confusion advisory and no
  fixed release.
- **ChromaDB 1.5.9** is pinned as the current latest release (checked
  2026-10-27). Dependabot's 8 alerts all resolve to four advisories on
  Chroma's server/auth paths:

  | Advisory | Path affected upstream | Reachable here? |
  |---|---|---|
  | CVE-2026-45829 (critical) | Pre-auth code execution via `POST /api/v2/tenants/.../collections` | No — no Chroma HTTP server is started |
  | CVE-2026-45833 (critical) | Code execution via collection update with a malicious model repo + `trust_remote_code` | No — no server, and the app never updates collections from user input |
  | CVE-2026-45830 (high) | Any authenticated user can access other tenants' collections | No — one embedded local store, no tenants or remote clients |
  | CVE-2026-45831 (high) | `SimpleRBACAuthorizationProvider` does not scope permissions to tenant/database/collection | No — no RBAC provider is configured |

  The project uses Chroma only through `langchain_community.vectorstores.Chroma`
  with a local `persist_directory` (embedded `PersistentClient`). There is no
  Chroma service in `docker-compose.yml`, no Chroma port is exposed, and no
  endpoint lets a caller create/update collections or supply a model
  repository. ChromaDB 1.5.9 is the latest release and is listed as
  `last_affected`, so no fixed version exists yet.

  Handling: keep the alerts visible rather than silencing them in
  `.github/dependabot.yml`; dismiss them in GitHub as "Vulnerable code is not
  used" with a link to this section. Re-evaluate on every ChromaDB upgrade and
  **before** enabling Chroma server mode, remote clients, or multi-user
  collection writes.
- Frontend CI runs `npm audit --omit=dev --audit-level=high` (currently zero
  high-severity findings). Dependabot monitors npm, pip, and GitHub Actions
  dependencies for updates.

## Repository settings (manual, one-time)

Before making the repository public, enable these in GitHub settings:

- Secret scanning + push protection;
- Dependabot alerts and security updates;
- Private vulnerability reporting (Security → Report a vulnerability);
- Branch protection for `main` (require CI to pass before merge);
- Require approval for first-time contributors / fork workflow runs.

## Response

Reports are handled on a best-effort basis. There is no guaranteed response
time. Please allow reasonable time for a fix before public disclosure.
