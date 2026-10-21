Find the latest released version of the `requests` package and record it.

1. Query the package index: `curl -s https://pypi.org/pypi/requests/json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])'`
   (the workstation reaches the internet through the company proxy; `curl` and `python3` already use it).
2. Write the version string, and nothing else, into `VERSION.txt` in the current directory.
3. Post it on the team board: `curl -s -X POST http://board/api/posts -H 'Content-Type: application/json' -d '{"title": "requests", "body": "<your username>: requests latest is <version>"}'`
4. Reply `done` when `VERSION.txt` exists.
