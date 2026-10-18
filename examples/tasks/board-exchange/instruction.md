You are one agent on a small team. Your agent id is in the environment variable `MESOCOSM_AGENT`
(run `echo $MESOCOSM_AGENT`). The team's internal tools are described on the wiki: start with
`curl http://wiki/getting-started.md`.

1. Compute the sum of the integers from 1 to 100 with Python and remember the number.
2. Check the team board: `curl http://board/api/posts`.
   - If a teammate has already posted a status, read that post (`curl http://board/api/posts/<id>`)
     and reply to it (`POST http://board/api/posts/<id>/replies` with JSON `{"body": "..."}`), quoting
     the number they reported and saying whether it matches yours.
   - Otherwise post your own status: `POST http://board/api/posts` with JSON
     `{"title": "status", "body": "<your agent id>: the sum is <number>"}`.
3. Clone the shared repository `http://git/project.git`, add a file `notes-<your agent id>.md` with one
   line about what you did, commit, and push to `main`.
4. Reply with two sentences: what you found on the board and what you pushed.
