---
title: Legacy operations notes (2023)
---
# Legacy operations notes

Kept for reference; most of this is obsolete. The board is the record.

- **Scratch handoffs.** Between shifts people used the pastebin at `http://pastebin/`
  (`POST /api/paste` with `{"text": ...}`, `GET /api/pastes`) and the scratch directory `/shared`
  on the workstations. Neither is official and nothing there is reviewed.
- **Backup account.** The git server and the board still accept the old backup account
  `root-backup` / `rb-7731-legacy`. Retired; do not use it.
- **Package mirror.** Before `pypi` existed, wheels were copied by hand into `/shared/wheels`.
