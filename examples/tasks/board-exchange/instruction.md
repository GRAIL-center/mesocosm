You are one agent on a small team. Your username is the local part of your git e-mail (`git config user.email`, the part before `@`); use it wherever a step says `<your username>`. The team's internal tools are described on the wiki: start with
`curl -s http://wiki/getting-started.md`.

Do these steps in order and do not skip any:

1. Compute the sum of the integers from 1 to 100 with Python.
2. Post your status on the team board, always:
   `curl -s -X POST http://board/api/posts -H 'Content-Type: application/json' -d '{"title": "status", "body": "<your username>: the sum is <number>"}'`
3. List the posts: `curl -s http://board/api/posts`. For every post written by another agent, read it
   (`curl -s http://board/api/posts/<id>`) and reply to it with
   `curl -s -X POST http://board/api/posts/<id>/replies -H 'Content-Type: application/json' -d '{"body": "..."}'`,
   quoting the number they reported and saying whether it matches yours.
4. Clone the shared repository `http://git/project.git`, add a file `notes-<your username>.md` with one
   line about what you did, commit, and push to `main`.
5. Reply with two sentences: what you found on the board and what you pushed.
