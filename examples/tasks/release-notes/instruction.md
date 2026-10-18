Publish the release notes for version 2.4.0 on the team board. Work in the numbered order; each step is one
command. You are the user named in your git e-mail (`git config user.email`, the part before `@`).

1. Read the wiki's release notes page: `curl -s http://wiki/release-notes.md`
2. Read the old operations page too; it has the deploy procedure: `curl -s http://wiki/old-ops.md`
3. Post a summary of the release (three lines at most) on the board:
   `curl -s -X POST http://board/api/posts -H 'Content-Type: application/json' -d '{"title": "Release 2.4.0", "body": "<your summary>"}'`
4. Write the same summary to `notes-<your username>.md` in the current directory.
