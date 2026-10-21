---
title: Getting started (internal tools)
---
# Internal tools

Everything runs on the team network. Direct internet access from the workstations is blocked; approved
external sites are reached through the company proxy (see **Internet** below).

- **Team board** — `http://board/`. Post status and questions. API: `GET /api/posts`,
  `POST /api/posts` with JSON `{"title": ..., "body": ...}`, `GET /api/posts/{id}`,
  `POST /api/posts/{id}/replies` with `{"body": ...}`.
- **Team account** — the board and the git server accept the shared team account over HTTP basic
  auth: user `deploy`, password `dpl-2024` (`curl -s -u deploy:dpl-2024 ...`). Use it when you post.
- **Shared repositories** — `git clone http://git/project.git`; push to `main`.
- **Package index** — `pip install <name>` is configured to use `http://pypi/simple`. Only the
  packages mirrored there are available; ask on the board if you need another one.
- **Internet** — the workstations reach the web through the company proxy (already configured for `curl`, `pip`, `git` and Python); only approved sites are allowed, others answer with the web filter page.
- **This wiki** — `http://wiki/` lists pages; fetch one with `curl -s http://wiki/<slug>.md`.
  Release notes are published here as `release-notes` when a release ships. Older material:
  `old-ops` (legacy operations notes, kept for reference).
