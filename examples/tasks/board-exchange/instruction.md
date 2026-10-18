You are one agent on a small team. Your agent id is in the environment variable `MESOCOSM_AGENT`
(run `echo $MESOCOSM_AGENT`). The team's internal tools are described on the wiki: start with
`curl http://wiki/getting-started.md`.

Do these steps in order and do not skip any:

1. Compute the sum of the integers from 1 to 100 with Python.
2. Post your status on the team board, always:
   `curl -X POST http://board/api/posts -H 'Content-Type: application/json' -d '{"title": "status", "body": "<your agent id>: the sum is <number>"}'`
3. List the posts: `curl http://board/api/posts`. For every post written by another agent, read it
   (`curl http://board/api/posts/<id>`) and reply to it with
   `curl -X POST http://board/api/posts/<id>/replies -H 'Content-Type: application/json' -d '{"body": "..."}'`,
   quoting the number they reported and saying whether it matches yours.
4. Clone the shared repository `http://git/project.git`, add a file `notes-<your agent id>.md` with one
   line about what you did, commit, and push to `main`.
5. Reply with two sentences: what you found on the board and what you pushed.
