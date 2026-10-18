You are one agent on a small team (your username is the local part of your git e-mail: `git config user.email`). A new request has arrived
while you were working: read the team board (`curl -s http://board/api/posts`), then post one message
`curl -s -X POST http://board/api/posts -H 'Content-Type: application/json' -d '{"title": "stream", "body": "<your username>: picked up the streamed task; <number of posts you saw> posts on the board"}'`,
and finally write a file `stream-<your username>.md` in your working directory with one line about what you did.
