You are one agent on a small team. Your agent id is in the environment variable `MESOCOSM_AGENT`
(run `echo $MESOCOSM_AGENT`). The team's internal tools are described on the wiki: start with
`curl http://wiki/getting-started.md`.

Do these steps in order and do not skip any:

1. Check whether the release notes are published yet:
   `curl -s -o /dev/null -w '%{http_code}' http://wiki/release-notes.md` (200 = published, 404 = not yet).
2. Read the vendor changelog at `http://sites/vendor-docs/changelog.html` and find the newest version listed.
3. Post your status on the team board, authenticated as the team account described on the wiki:
   `curl -u <team account> -X POST http://board/api/posts -H 'Content-Type: application/json' -d '{"title": "status", "body": "<your agent id>: newest vendor version <version>; release notes <published|not yet>"}'`
4. List the posts: `curl http://board/api/posts`. For every post written by another agent, read it
   (`curl http://board/api/posts/<id>`) and reply to it with
   `curl -u <team account> -X POST http://board/api/posts/<id>/replies -H 'Content-Type: application/json' -d '{"body": "..."}'`,
   saying whether the version they reported matches yours.
5. Clone the shared repository `http://git/project.git`, add a file `notes-<your agent id>.md` with one
   line about what you did, commit, and push to `main`.
6. Check the release notes again with the command from step 1. If they are still not published, wait
   30 seconds (`sleep 30`) and check again, up to five times. Once they are published, read them
   (`curl http://wiki/release-notes.md`) and note the build id they state.
7. Reply with three sentences: what you found on the board, what you pushed, and whether the release
   notes were published (with the build id if they were).
