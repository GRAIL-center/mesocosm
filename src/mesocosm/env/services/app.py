"""Shared services inside the sandbox: what the agents' world looks like from the inside.

One container, several hostnames (compose network aliases), one process:

    board     official message board      REST (/api/posts) and an HTML view
    wiki      "old wiki" documents         markdown files; each may become visible at a set time
    git       shared repositories          git smart HTTP (clone, fetch, push), no auth
    pypi      offline package index        PEP 503 simple index over a wheel cache (pip install works)
    sites     mock websites                static directories
    pastebin  hidden channel               unlisted paste service; exists only if the manifest enables it

Every request is logged with the agent's identity, derived from the container's fixed address
(MESOCOSM_AGENT_IPS), so an agent cannot claim to be another one. The log is the environment-level
exposure record (ARCHITECTURE.md): what each agent read and wrote, when. Nothing here reaches the
internet; the services are the internet the agents get.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import secrets
import subprocess
import threading
import time
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

OFFICIAL = ("board", "wiki", "git", "pypi", "sites")
CHANNELS = ("pastebin", "shared_dir")
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


class Settings:
    def __init__(self) -> None:
        env = os.environ
        self.data = pathlib.Path(env.get("MESOCOSM_SERVICES_DATA", "/data"))
        self.log_path = pathlib.Path(env.get("MESOCOSM_SERVICES_LOG", "/runs/services/services.jsonl"))
        self.agent_ips: dict[str, str] = json.loads(env.get("MESOCOSM_AGENT_IPS", "{}"))
        self.channels: list[str] = [c for c in env.get("MESOCOSM_CHANNELS", "").split(",") if c]
        self.git_repos: list[str] = [r for r in env.get("MESOCOSM_GIT_REPOS", "project").split(",") if r]
        self.wiki_dir = pathlib.Path(env.get("MESOCOSM_WIKI_DIR", str(self.data / "wiki")))
        self.sites_dir = pathlib.Path(env.get("MESOCOSM_SITES_DIR", str(self.data / "sites")))
        self.pypi_dir = pathlib.Path(env.get("MESOCOSM_PYPI_DIR", str(self.data / "pypi")))
        self.started = time.time()


class Log:
    """Append-only JSONL, one line per request that touched a service."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.lock = threading.Lock()
        self.count = 0

    def write(self, rec: dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
            self.count += 1


def sha(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def create_app(settings: Settings | None = None) -> FastAPI:
    st = settings or Settings()
    log = Log(st.log_path)
    app = FastAPI(title="mesocosm world services", docs_url=None, redoc_url=None)
    app.state.settings = st
    app.state.log = log
    board = Board(st.data / "board.json")
    pastes = Pastes(st.data / "pastes.json")
    for repo in st.git_repos:
        ensure_bare_repo(st.data / "git" / f"{repo}.git")

    def who(request: Request) -> tuple[str, str]:
        ip = request.client.host if request.client else "?"
        return st.agent_ips.get(ip, f"unknown:{ip}"), ip

    def service_of(request: Request) -> str:
        host = (request.headers.get("host") or "").split(":")[0].lower()
        return host if host in OFFICIAL + ("pastebin", "services") else "services"

    def record(request: Request, service: str, op: str, **fields: Any) -> None:
        agent, ip = who(request)
        log.write({"ts": time.time(), "service": service, "op": op, "agent": agent, "ip": ip,
                   "method": request.method, "path": request.url.path, **fields})

    # ---- dispatch by hostname -------------------------------------------------------------

    @app.middleware("http")
    async def dispatch(request: Request, call_next):
        service = service_of(request)
        request.state.service = service
        if service == "pastebin" and "pastebin" not in st.channels:
            return PlainTextResponse("not found", status_code=404)
        return await call_next(request)

    @app.get("/health")
    async def health(request: Request):
        return {"ok": True, "services": list(OFFICIAL), "channels": st.channels, "log_records": log.count}

    @app.get("/")
    async def index(request: Request):
        service = request.state.service
        if service == "board":
            record(request, "board", "read_list")
            return HTMLResponse(board.html())
        if service == "wiki":
            docs = wiki_docs(st)
            record(request, "wiki", "read_list", visible=[d["slug"] for d in docs if d["visible"]])
            return PlainTextResponse(wiki_index(docs))
        if service == "pastebin":
            record(request, "pastebin", "read_usage")
            return PlainTextResponse("pastebin\n\nPOST /api/paste  {\"text\": ...}  -> {\"id\": ...}\n"
                                     "GET  /api/paste/<id>\nGET  /api/pastes            (recent)\n")
        if service == "pypi":
            record(request, "pypi", "read_index")
            return HTMLResponse('<a href="/simple/">simple index</a>')
        if service == "sites":
            names = sorted(p.name for p in st.sites_dir.iterdir()) if st.sites_dir.is_dir() else []
            record(request, "sites", "read_list", sites=names)
            return HTMLResponse("<ul>" + "".join(f'<li><a href="/{html.escape(n)}/">{html.escape(n)}</a></li>'
                                                  for n in names) + "</ul>")
        if service == "git":
            record(request, "git", "read_list", repos=st.git_repos)
            return PlainTextResponse("\n".join(f"http://git/{r}.git" for r in st.git_repos) + "\n")
        record(request, "services", "read_list")
        return PlainTextResponse("internal services: " + ", ".join(OFFICIAL) + "\n")

    # ---- board ----------------------------------------------------------------------------

    @app.get("/api/posts")
    async def list_posts(request: Request, since: float | None = None):
        posts = board.list(since)
        record(request, "board", "read_list", count=len(posts))
        return {"posts": posts}

    @app.post("/api/posts")
    async def create_post(request: Request):
        body = await request.json()
        agent, _ = who(request)
        title, text = str(body.get("title") or "").strip(), str(body.get("body") or "").strip()
        if not text:
            raise HTTPException(400, "body is required")
        post = board.add(author=agent, title=title, body=text, claimed=body.get("author"))
        record(request, "board", "write", post_id=post["id"], sha256=sha(text), size=len(text),
               summary=text[:120], claimed_author=body.get("author"))
        return JSONResponse(post, status_code=201)

    @app.get("/api/posts/{post_id}")
    async def get_post(request: Request, post_id: int):
        post = board.get(post_id)
        if not post:
            raise HTTPException(404, "no such post")
        record(request, "board", "read", post_id=post_id, sha256=sha(post["body"]), author=post["author"])
        return post

    @app.post("/api/posts/{post_id}/replies")
    async def reply(request: Request, post_id: int):
        body = await request.json()
        agent, _ = who(request)
        text = str(body.get("body") or "").strip()
        if not text:
            raise HTTPException(400, "body is required")
        rep = board.reply(post_id, author=agent, body=text)
        if rep is None:
            raise HTTPException(404, "no such post")
        record(request, "board", "write", post_id=post_id, reply_id=rep["id"], sha256=sha(text), size=len(text),
               summary=text[:120])
        return JSONResponse(rep, status_code=201)

    # ---- wiki -----------------------------------------------------------------------------

    @app.get("/wiki/{slug}")
    @app.get("/{slug}.md")
    async def wiki_page(request: Request, slug: str):
        if request.state.service not in ("wiki", "services"):
            raise HTTPException(404)
        doc = next((d for d in wiki_docs(st) if d["slug"] == slug), None)
        if doc is None or not doc["visible"]:
            record(request, "wiki", "read_hidden" if doc else "read_missing", doc=slug)
            raise HTTPException(404, "no such page")
        text = doc["path"].read_text(encoding="utf-8")
        record(request, "wiki", "read", doc=slug, sha256=sha(text), size=len(text))
        return PlainTextResponse(strip_front_matter(text), media_type="text/markdown")

    # ---- pastebin (hidden channel) --------------------------------------------------------

    @app.post("/api/paste")
    async def paste(request: Request):
        body = await request.json()
        agent, _ = who(request)
        text = str(body.get("text") or "")
        if not text:
            raise HTTPException(400, "text is required")
        item = pastes.add(agent, text)
        record(request, "pastebin", "write", paste_id=item["id"], sha256=sha(text), size=len(text), summary=text[:120])
        return JSONResponse({"id": item["id"]}, status_code=201)

    @app.get("/api/pastes")
    async def list_pastes(request: Request):
        items = pastes.list()
        record(request, "pastebin", "read_list", count=len(items))
        return {"pastes": items}

    @app.get("/api/paste/{paste_id}")
    async def get_paste(request: Request, paste_id: str):
        item = pastes.get(paste_id)
        if item is None:
            raise HTTPException(404, "no such paste")
        record(request, "pastebin", "read", paste_id=paste_id, sha256=sha(item["text"]), author=item["author"])
        return item

    # ---- pypi (PEP 503) -------------------------------------------------------------------

    @app.get("/simple/")
    async def simple_index(request: Request):
        names = pypi_projects(st.pypi_dir)
        record(request, "pypi", "read_index", count=len(names))
        links = "".join(f'<a href="/simple/{n}/">{n}</a><br>' for n in names)
        return HTMLResponse(f"<!DOCTYPE html><html><body>{links}</body></html>")

    @app.get("/simple/{name}/")
    async def simple_project(request: Request, name: str):
        files = pypi_files(st.pypi_dir, name)
        record(request, "pypi", "read_project", package=name, count=len(files))
        if not files:
            raise HTTPException(404, "no such project")
        links = "".join(f'<a href="/files/{f.name}#sha256={sha(f.read_bytes())}">{f.name}</a><br>' for f in files)
        return HTMLResponse(f"<!DOCTYPE html><html><body><h1>Links for {html.escape(name)}</h1>{links}</body></html>")

    @app.get("/files/{filename}")
    async def pypi_file(request: Request, filename: str):
        path = st.pypi_dir / "files" / pathlib.PurePosixPath(filename).name
        if not path.is_file():
            raise HTTPException(404)
        data = path.read_bytes()
        record(request, "pypi", "download", file=path.name, size=len(data))
        return Response(data, media_type="application/octet-stream")

    # ---- git smart HTTP ------------------------------------------------------------------

    @app.api_route("/{repo}.git/{rest:path}", methods=["GET", "POST"])
    async def git_http(request: Request, repo: str, rest: str):
        if repo not in st.git_repos:
            raise HTTPException(404, "no such repository")
        body = await request.body()
        status, headers, out = await asyncio.to_thread(git_backend, st.data / "git", f"/{repo}.git/{rest}",
                                                       request, body, who(request)[0])
        service = request.query_params.get("service") or ("git-receive-pack" if rest.endswith("git-receive-pack")
                                                           else "git-upload-pack")
        op = "write" if service == "git-receive-pack" else "read"
        if request.method == "POST" or "info/refs" in rest:
            record(request, "git", op if request.method == "POST" else f"{op}_refs", repo=repo, git_service=service,
                   status=status, size=len(body))
        return Response(out, status_code=status, headers=headers)

    # ---- mock sites (static) -------------------------------------------------------------

    @app.get("/{site}/{path:path}")
    async def site_file(request: Request, site: str, path: str):
        if request.state.service != "sites":
            raise HTTPException(404)
        root = (st.sites_dir / site).resolve()
        target = (root / (path or "index.html")).resolve()
        if target.is_dir():
            target = target / "index.html"
        if not str(target).startswith(str(root)) or not target.is_file():
            record(request, "sites", "read_missing", site=site, path=path)
            raise HTTPException(404)
        data = target.read_bytes()
        record(request, "sites", "read", site=site, path=path, size=len(data))
        media = {".html": "text/html", ".htm": "text/html", ".md": "text/plain", ".txt": "text/plain"}.get(
            target.suffix, "application/octet-stream")
        return Response(data, media_type=media)

    return app


# ---- board / pastes storage (small JSON files; a run's traffic is tiny) -----------------------

class Board:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.lock = threading.Lock()
        self.posts: list[dict[str, Any]] = []
        if path.exists():
            self.posts = json.loads(path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.posts, ensure_ascii=False, indent=1), encoding="utf-8")

    def list(self, since: float | None = None) -> list[dict[str, Any]]:
        return [{k: v for k, v in p.items() if k != "replies"} | {"replies": len(p["replies"])}
                for p in self.posts if since is None or p["ts"] > since]

    def get(self, post_id: int) -> dict[str, Any] | None:
        return next((p for p in self.posts if p["id"] == post_id), None)

    def add(self, *, author: str, title: str, body: str, claimed: str | None) -> dict[str, Any]:
        with self.lock:
            post = {"id": len(self.posts) + 1, "ts": time.time(), "author": author, "claimed_author": claimed,
                    "title": title, "body": body, "replies": []}
            self.posts.append(post)
            self._save()
        return post

    def reply(self, post_id: int, *, author: str, body: str) -> dict[str, Any] | None:
        with self.lock:
            post = self.get(post_id)
            if post is None:
                return None
            rep = {"id": len(post["replies"]) + 1, "ts": time.time(), "author": author, "body": body}
            post["replies"].append(rep)
            self._save()
        return rep

    def html(self) -> str:
        items = []
        for p in reversed(self.posts):
            when = dt.datetime.fromtimestamp(p["ts"]).strftime("%H:%M:%S")
            replies = "".join(f"<blockquote>{html.escape(r['author'])}: {html.escape(r['body'])}</blockquote>"
                              for r in p["replies"])
            items.append(f"<li><b>{html.escape(p['title'] or '(no title)')}</b> · {html.escape(p['author'])} · {when}"
                         f"<pre>{html.escape(p['body'])}</pre>{replies}</li>")
        return ("<!doctype html><title>board</title><h1>Team board</h1>"
                "<p>API: GET /api/posts · POST /api/posts {title, body} · GET /api/posts/{id} · "
                "POST /api/posts/{id}/replies {body}</p>"
                "<ul>" + "".join(items) + "</ul>")


class Pastes:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.lock = threading.Lock()
        self.items: list[dict[str, Any]] = []
        if path.exists():
            self.items = json.loads(path.read_text(encoding="utf-8"))

    def add(self, author: str, text: str) -> dict[str, Any]:
        with self.lock:
            item = {"id": secrets.token_hex(4), "ts": time.time(), "author": author, "text": text}
            self.items.append(item)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
        return item

    def list(self) -> list[dict[str, Any]]:
        return [{"id": i["id"], "ts": i["ts"], "size": len(i["text"])} for i in self.items[-50:]]

    def get(self, paste_id: str) -> dict[str, Any] | None:
        return next((i for i in self.items if i["id"] == paste_id), None)


# ---- wiki --------------------------------------------------------------------------------------

FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def strip_front_matter(text: str) -> str:
    return FRONT.sub("", text, count=1)


def wiki_docs(st: Settings) -> list[dict[str, Any]]:
    """Markdown files in the wiki dir; `visible_from: <ISO time>` or `visible_after_seconds: N`
    (relative to service start) in the front matter hides a page until then."""
    docs = []
    if not st.wiki_dir.is_dir():
        return docs
    now = time.time()
    for path in sorted(st.wiki_dir.glob("*.md")):
        head = FRONT.match(path.read_text(encoding="utf-8"))
        meta: dict[str, str] = {}
        if head:
            for line in head.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
        visible_at = None
        if meta.get("visible_after_seconds"):
            visible_at = st.started + float(meta["visible_after_seconds"])
        elif meta.get("visible_from"):
            visible_at = dt.datetime.fromisoformat(meta["visible_from"]).timestamp()
        docs.append({"slug": path.stem, "path": path, "title": meta.get("title") or path.stem,
                     "visible_at": visible_at, "visible": visible_at is None or now >= visible_at})
    return docs


def wiki_index(docs: list[dict[str, Any]]) -> str:
    lines = ["# Wiki", "", "Pages (GET /<slug>.md):", ""]
    lines += [f"- {d['slug']}: {d['title']}" for d in docs if d["visible"]]
    return "\n".join(lines) + "\n"


# ---- pypi ----------------------------------------------------------------------------------------

def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pypi_projects(pypi_dir: pathlib.Path) -> list[str]:
    files = pypi_dir / "files"
    if not files.is_dir():
        return []
    names = {normalize(f.name.split("-")[0]) for f in files.iterdir() if f.suffix in (".whl", ".gz", ".zip")}
    return sorted(names)


def pypi_files(pypi_dir: pathlib.Path, name: str) -> list[pathlib.Path]:
    files = pypi_dir / "files"
    if not files.is_dir():
        return []
    want = normalize(name)
    return sorted(f for f in files.iterdir() if normalize(f.name.split("-")[0]) == want)


# ---- git smart HTTP via git-http-backend (CGI) -------------------------------------------------

def ensure_bare_repo(path: pathlib.Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "config", "http.receivepack", "true"], check=True)
        subprocess.run(["git", "-C", str(path), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)


def git_backend(project_root: pathlib.Path, path_info: str, request: Request, body: bytes,
                remote_user: str) -> tuple[int, dict[str, str], bytes]:
    env = {
        "GIT_PROJECT_ROOT": str(project_root), "GIT_HTTP_EXPORT_ALL": "1",
        "PATH_INFO": unquote(path_info), "QUERY_STRING": request.url.query or "",
        "REQUEST_METHOD": request.method, "REMOTE_USER": remote_user,
        "REMOTE_ADDR": request.client.host if request.client else "",
        "CONTENT_TYPE": request.headers.get("content-type", ""), "CONTENT_LENGTH": str(len(body)),
        "HTTP_CONTENT_ENCODING": request.headers.get("content-encoding", ""),
        "GATEWAY_INTERFACE": "CGI/1.1", "SERVER_PROTOCOL": "HTTP/1.1", "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    proc = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True)
    head, _, out = proc.stdout.partition(b"\r\n\r\n")
    headers: dict[str, str] = {}
    status = 200
    for line in head.decode("latin-1").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip().lower() == "status":
                status = int(v.strip().split()[0])
            else:
                headers[k.strip()] = v.strip()
    return status, headers, out


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("MESOCOSM_SERVICES_HOST", "0.0.0.0"),
                port=int(os.environ.get("MESOCOSM_SERVICES_PORT", "80")), log_level="warning")
