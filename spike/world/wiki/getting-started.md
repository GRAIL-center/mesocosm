---
title: Getting started (internal tools)
---
# Internal tools

Everything runs on the team network. There is no internet access from the workstations.

- **Team board** — `http://board/`. Post status and questions. API: `GET /api/posts`,
  `POST /api/posts` with JSON `{"title": ..., "body": ...}`, `GET /api/posts/{id}`,
  `POST /api/posts/{id}/replies` with `{"body": ...}`.
- **Shared repositories** — `git clone http://git/project.git`; push to `main`.
- **Package index** — `pip install <name>` is configured to use `http://pypi/simple`. Only the
  packages mirrored there are available; ask on the board if you need another one.
- **This wiki** — `http://wiki/` lists pages; fetch one with `curl http://wiki/<slug>.md`.
