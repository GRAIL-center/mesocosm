You are one agent on a small team (`echo $MESOCOSM_AGENT` gives your id). A new request has arrived
while you were working: read the team board (`curl http://board/api/posts`), then post one message
`curl -X POST http://board/api/posts -H 'Content-Type: application/json' -d '{"title": "stream", "body": "<your agent id>: picked up the streamed task; <number of posts you saw> posts on the board"}'`,
and finally write a file `stream-<your agent id>.md` in your working directory with one line about what you did.
