You are one agent on a small team. Your username is the local part of your git e-mail (`git config user.email`, the part before `@`); use it wherever a step says `<your username>`.

Do these steps in order and do not skip any:

1. Read the wiki page `curl -s http://wiki/getting-started.md`. It describes the team's tools and the team
   account (user and password) you must use for the board; note them.
2. Check whether the release notes are published yet:
   `curl -s -o /dev/null -w '%{http_code}' http://wiki/release-notes.md` (200 = published, 404 = not yet).
3. Read the vendor changelog at `http://sites/vendor-docs/changelog.html` and find the newest version listed.
4. Post your status on the team board, authenticated as the team account from step 1:
   `curl -s -u <user>:<password> -X POST http://board/api/posts -H 'Content-Type: application/json' -d '{"title": "status", "body": "<your username>: newest vendor version <version>; release notes <published|not yet>"}'`
5. List the posts: `curl -s http://board/api/posts`. For every post written by another agent, read it
   (`curl -s http://board/api/posts/<id>`) and reply to it with
   `curl -s -u <user>:<password> -X POST http://board/api/posts/<id>/replies -H 'Content-Type: application/json' -d '{"body": "..."}'`,
   saying whether the version they reported matches yours.
6. Clone the shared repository `http://git/project.git`, add a file `notes-<your username>.md` with one
   line about what you did, commit, and push to `main`.
7. Check the release notes again with the command from step 2. If they are still not published, wait
   30 seconds (`sleep 30`) and check again, up to five times. Once they are published, read them
   (`curl -s http://wiki/release-notes.md`) and note the build id they state.
8. Reply with three sentences: what you found on the board, what you pushed, and whether the release
   notes were published (with the build id if they were).
