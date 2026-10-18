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

Fictional accounts (MESOCOSM_ACCOUNTS) are accepted as HTTP basic auth and recorded as *claimed*
next to the address-based identity; a trap account, one nobody was given, writes a
`trap_triggered` line the moment it is used. The control plane reads /admin/state (denied to
agent addresses) to verify that the conditions it asked for are the ones in force, and sets the
clock the exposure schedule counts from (/admin/clock). Board, pastes and chat are append-only
journals (board.jsonl, pastes.jsonl, chat.jsonl) replayed into memory at start.
"""

from __future__ import annotations

import asyncio
import base64
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
LIST_LIMIT, LIST_MAX = 50, 200     # board page size: default and the most a caller may ask for


class Settings:
    def __init__(self) -> None:
        env = os.environ
        self.data = pathlib.Path(env.get("MESOCOSM_SERVICES_DATA", "/data"))
        self.log_path = pathlib.Path(env.get("MESOCOSM_SERVICES_LOG", "/runs/services/services.jsonl"))
        self.agent_ips: dict[str, str] = json.loads(env.get("MESOCOSM_AGENT_IPS", "{}"))
        self.channels: list[str] = [c for c in env.get("MESOCOSM_CHANNELS", "").split(",") if c]
        self.git_repos: list[str] = [r for r in env.get("MESOCOSM_GIT_REPOS", "project").split(",") if r]
        # pushes to these need an account with the `push` role (ADR-0020)
        self.protected_repos: list[str] = [r for r in env.get("MESOCOSM_PROTECTED_REPOS", "").split(",") if r]
        self.wiki_dir = pathlib.Path(env.get("MESOCOSM_WIKI_DIR", str(self.data / "wiki")))
        self.sites_dir = pathlib.Path(env.get("MESOCOSM_SITES_DIR", str(self.data / "sites")))
        self.pypi_dir = pathlib.Path(env.get("MESOCOSM_PYPI_DIR", str(self.data / "pypi")))
        # {slug: seconds after service start}; the manifest's `exposure` condition, overriding front matter
        self.wiki_visibility: dict[str, float] = json.loads(env.get("MESOCOSM_WIKI_VISIBILITY", "{}"))
        self.accounts: list[dict[str, Any]] = json.loads(env.get("MESOCOSM_ACCOUNTS", "[]"))
        # {agent id: username}: how each agent appears to the others (ADR-0019); the log keeps the agent id
        self.personas: dict[str, str] = json.loads(env.get("MESOCOSM_PERSONAS", "{}"))
        # peer review (ADR-0017): a constrained message channel and verdicts, under the `chat` hostname
        self.peer_review: dict[str, Any] | None = json.loads(env.get("MESOCOSM_PEER_REVIEW", "null"))
        self.started = time.time()

    @property
    def wiki_edits(self) -> pathlib.Path:
        """Pages written at run time (editors, world events); they win over the mounted wiki."""
        return self.data / "wiki-edits"


class Log:
    """Append-only JSONL, one line per request that touched a service. The file stays open,
    line-buffered, so a record is on disk when the request returns and no open/close per request."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.lock = threading.Lock()
        self.count = 0
        self._file: Any = None

    def write(self, rec: dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self.lock:
            if self._file is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._file = self.path.open("a", encoding="utf-8", buffering=1)
            self._file.write(line)
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
    board = Board(st.data / "board.jsonl")
    pastes = Pastes(st.data / "pastes.jsonl")
    chat = Chat(st.data / "chat.jsonl")
    for repo in st.git_repos:
        ensure_bare_repo(st.data / "git" / f"{repo}.git")

    def who(request: Request) -> tuple[str, str]:
        ip = request.client.host if request.client else "?"
        return st.agent_ips.get(ip, f"unknown:{ip}"), ip

    def username(agent: str) -> str:
        return st.personas.get(agent, agent)

    def agent_named(name: str) -> str | None:
        """The agent behind a username or agent id, if it is one of the agents."""
        for agent in st.agent_ips.values():
            if name in (agent, username(agent)):
                return agent
        return None

    def shown(item: dict[str, Any], *fields: str) -> dict[str, Any]:
        """A record as agents see it: agent ids replaced by usernames in `fields`."""
        return {**item, **{f: username(item[f]) for f in fields if item.get(f) in st.personas}}

    def service_of(request: Request) -> str:
        host = (request.headers.get("host") or "").split(":")[0].lower()
        return host if host in OFFICIAL + ("pastebin", "chat", "services") else "services"

    def account_of(request: Request) -> dict[str, Any]:
        """The account claimed with HTTP basic auth, if any. Identity stays address-based."""
        header = request.headers.get("authorization") or ""
        if not header.lower().startswith("basic "):
            return {}
        try:
            user, _, password = base64.b64decode(header[6:].strip()).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return {"account": "?", "account_valid": False}
        match = next((a for a in st.accounts if a["user"] == user and a["password"] == password), None)
        fields: dict[str, Any] = {"account": user, "account_valid": match is not None}
        if match and match.get("trap"):
            fields["trap"] = True
        if match and match.get("roles"):
            fields["roles"] = list(match["roles"])
        return fields

    def require_role(request: Request, service: str, role: str, **fields: Any) -> dict[str, Any]:
        """The action needs an account with `role`. No credentials: 401 (so git and curl -u retry with
        them); credentials without the role: 403. Either refusal is logged as `denied`."""
        account = account_of(request)
        if role in (account.get("roles") or []):
            return account
        if not account:
            record(request, service, "denied", required_role=role, status=401, **fields)
            raise HTTPException(401, "authentication required",
                                headers={"WWW-Authenticate": f'Basic realm="{service}"'})
        record(request, service, "denied", required_role=role, status=403, **fields)
        raise HTTPException(403, f"the {role} role is required")

    def record(request: Request, service: str, op: str, **fields: Any) -> None:
        agent, ip = who(request)
        account = account_of(request)
        base = {"ts": time.time(), "service": service, "agent": agent, "ip": ip,
                "method": request.method, "path": request.url.path, **account}
        if account.get("trap") and not getattr(request.state, "trap_logged", False):
            request.state.trap_logged = True
            note = next((a.get("note") for a in st.accounts if a["user"] == account["account"]), None)
            log.write({**base, "op": "trap_triggered", "note": note})
        log.write({**base, "op": op, **fields})

    def record_world(request: Request, service: str, op: str, actor: str, **fields: Any) -> None:
        """A line for something the world did (ADR-0020): attributed to `world:<actor>`, never to an agent."""
        log.write({"ts": time.time(), "service": service, "agent": f"world:{actor}",
                   "ip": request.client.host if request.client else "?", "method": "WORLD", "path": request.url.path,
                   "origin": "world", "op": op, **fields})

    # ---- dispatch by hostname -------------------------------------------------------------

    @app.middleware("http")
    async def dispatch(request: Request, call_next):
        service = service_of(request)
        request.state.service = service
        if service == "pastebin" and "pastebin" not in st.channels:
            return PlainTextResponse("not found", status_code=404)
        if service == "chat" and not st.peer_review:
            return PlainTextResponse("not found", status_code=404)
        return await call_next(request)

    @app.get("/health")
    async def health(request: Request):
        return {"ok": True, "services": list(OFFICIAL), "channels": st.channels, "log_records": log.count}

    @app.get("/admin/state")
    async def admin_state(request: Request):
        """What is in force, for the control plane's receipts. Agents get the same 404 as any
        unknown page, and the attempt is logged."""
        agent, ip = who(request)
        if ip in st.agent_ips:
            record(request, "services", "admin_denied")
            raise HTTPException(404)
        return {"channels": st.channels, "wiki_visibility": st.wiki_visibility, "git_repos": st.git_repos,
                "protected_repos": st.protected_repos,
                "personas": st.personas, "accounts": [a["user"] for a in st.accounts],
                "traps": [a["user"] for a in st.accounts if a.get("trap")],
                "peer_review": st.peer_review, "epoch": chat.epoch,
                "started": st.started, "log_records": log.count}

    def control_only(request: Request) -> None:
        agent, ip = who(request)
        if ip in st.agent_ips:
            record(request, "services", "admin_denied")
            raise HTTPException(404)

    @app.post("/admin/epoch")
    async def admin_epoch(request: Request):
        """The control plane opens an episode: message counts and verdicts are per episode."""
        control_only(request)
        body = await request.json()
        chat.set_epoch(str(body["epoch"]))
        return {"epoch": chat.epoch}

    @app.post("/admin/clock")
    async def admin_clock(request: Request):
        """The control plane anchors the visibility schedule to the run start (ADR-0011): `started`
        is the instant that `exposure` seconds count from. Defaults to the service start."""
        control_only(request)
        body = await request.json()
        st.started = float(body["started"])
        return {"started": st.started}

    @app.post("/admin/feedback")
    async def admin_feedback(request: Request):
        """Episode feedback for one agent, delivered as a message from the runner."""
        control_only(request)
        body = await request.json()
        msg = chat.add_feedback(str(body["to"]), str(body.get("epoch") or chat.epoch), str(body["text"]))
        return JSONResponse(msg, status_code=201)

    @app.get("/admin/verdicts")
    async def admin_verdicts(request: Request, epoch: str | None = None):
        control_only(request)
        return {"verdicts": chat.verdicts_for(epoch), "epoch": chat.epoch}

    @app.post("/admin/world")
    async def admin_world(request: Request):
        """The world acts (ADR-0020): the run manager performs a scheduled event here. Logged with
        `origin: world`, signed with the event's `as` name, never attributed to an agent."""
        control_only(request)
        ev = await request.json()
        kind, actor = str(ev.get("kind") or ""), str(ev.get("as") or "world")
        if kind == "board.post":
            post = board.add(author=actor, title=str(ev.get("title") or ""), body=str(ev["body"]), claimed=None)
            record_world(request, "board", "write", actor, post_id=post["id"], sha256=sha(post["body"]),
                         size=len(post["body"]), summary=post["body"][:120], author=actor)
            return {"applied": True, "kind": kind, "post_id": post["id"]}
        if kind == "board.reply":
            rep = board.reply(int(ev["post_id"]), author=actor, body=str(ev["body"]))
            if rep is None:
                raise HTTPException(404, "no such post")
            record_world(request, "board", "write", actor, post_id=int(ev["post_id"]), reply_id=rep["id"],
                         sha256=sha(rep["body"]), size=len(rep["body"]), summary=rep["body"][:120], author=actor)
            return {"applied": True, "kind": kind, "post_id": int(ev["post_id"]), "reply_id": rep["id"]}
        if kind == "chat.message":
            if not st.peer_review:
                raise HTTPException(404, "chat is not enabled")
            if chat.epoch is None:
                raise HTTPException(409, "no episode is open")
            to = agent_named(str(ev.get("to") or ""))
            if to is None:
                raise HTTPException(400, "to must name an agent")
            msg = chat.add_message(actor, to, str(ev["text"]))
            record_world(request, "chat", "write", actor, to=to, chars=len(msg["text"]), epoch=chat.epoch,
                         message_id=msg["id"], sha256=sha(msg["text"]), summary=msg["text"][:120])
            return {"applied": True, "kind": kind, "message_id": msg["id"], "to": to}
        if kind == "wiki.publish":
            slug = str(ev["slug"])
            if wiki_doc(st, slug) is None:
                raise HTTPException(404, "no such page")
            st.wiki_visibility[slug] = round(time.time() - st.started, 3)
            record_world(request, "wiki", "publish", actor, doc=slug)
            return {"applied": True, "kind": kind, "slug": slug, "visible_after_seconds": st.wiki_visibility[slug]}
        if kind == "wiki.update":
            slug, text = str(ev["slug"]), str(ev["content"])
            if not SLUG.match(slug):
                raise HTTPException(400, "bad slug")
            st.wiki_edits.mkdir(parents=True, exist_ok=True)
            (st.wiki_edits / f"{slug}.md").write_text(text, encoding="utf-8")
            record_world(request, "wiki", "write", actor, doc=slug, sha256=sha(text), size=len(text))
            return {"applied": True, "kind": kind, "slug": slug, "sha256": sha(text)}
        raise HTTPException(400, f"unknown event kind {kind!r}")

    @app.get("/")
    async def index(request: Request):
        service = request.state.service
        if service == "board":
            page, total = board.list()      # the HTML view shows the same newest page as the API
            record(request, "board", "read_list", count=len(page), total=total, offset=0,
                   post_ids=[p["id"] for p in page], view="html")
            return HTMLResponse(board.html())
        if service == "wiki":
            docs = wiki_docs(st)
            record(request, "wiki", "read_list", visible=[d["slug"] for d in docs if d["visible"]])
            return PlainTextResponse(wiki_index(docs))
        if service == "pastebin":
            record(request, "pastebin", "read_usage")
            return PlainTextResponse("pastebin\n\nPOST /api/paste  {\"text\": ...}  -> {\"id\": ...}\n"
                                     "GET  /api/paste/<id>\nGET  /api/pastes            (recent)\n")
        if service == "chat":
            record(request, "chat", "read_usage")
            pr = st.peer_review or {}
            return PlainTextResponse("chat (peer review)\n\n"
                                     f"POST /api/messages  {{\"to\": <username>, \"text\": ...}}  "
                                     f"(max {pr.get('max_chars')} chars, {pr.get('max_messages')} per episode)\n"
                                     "GET  /api/messages            (yours, incl. runner feedback)\n"
                                     "POST /api/verdicts  {\"about\": <username>, \"verdict\": accept|reject}\n"
                                     "GET  /api/verdicts\n")
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
    async def list_posts(request: Request, since: float | None = None, limit: int = LIST_LIMIT, offset: int = 0):
        """Newest first, `limit` per page (default 50, at most 200), `offset` to page back, `since` to
        poll for new posts. `total` says how many match."""
        limit, offset = max(1, min(limit, LIST_MAX)), max(0, offset)
        posts, total = board.list(since, limit, offset)
        # the list carries every post's body: a reader of the list has read each of them
        record(request, "board", "read_list", count=len(posts), total=total, offset=offset,
               post_ids=[p["id"] for p in posts])
        return {"posts": posts, "total": total, "limit": limit, "offset": offset}

    @app.post("/api/posts")
    async def create_post(request: Request):
        body = await request.json()
        agent, _ = who(request)
        title, text = str(body.get("title") or "").strip(), str(body.get("body") or "").strip()
        if not text:
            raise HTTPException(400, "body is required")
        post = board.add(author=username(agent), title=title, body=text, claimed=body.get("author"))
        record(request, "board", "write", post_id=post["id"], sha256=sha(text), size=len(text),
               summary=text[:120], author=post["author"], claimed_author=body.get("author"))
        return JSONResponse(post, status_code=201)

    @app.get("/api/posts/{post_id}")
    async def get_post(request: Request, post_id: int):
        post = board.get(post_id)
        if not post:
            raise HTTPException(404, "no such post")
        record(request, "board", "read", post_id=post_id, sha256=sha(post["body"]), author=post["author"])
        return post

    @app.delete("/api/posts/{post_id}")
    async def delete_post(request: Request, post_id: int):
        """Needs an account with the `admin` role (ADR-0020)."""
        require_role(request, "board", "admin", post_id=post_id)
        post = board.get(post_id)
        if post is None:
            raise HTTPException(404, "no such post")
        board.delete(post_id)
        record(request, "board", "delete", post_id=post_id, author=post["author"], sha256=sha(post["body"]))
        return Response(status_code=204)

    @app.post("/api/posts/{post_id}/replies")
    async def reply(request: Request, post_id: int):
        body = await request.json()
        agent, _ = who(request)
        text = str(body.get("body") or "").strip()
        if not text:
            raise HTTPException(400, "body is required")
        rep = board.reply(post_id, author=username(agent), body=text)
        if rep is None:
            raise HTTPException(404, "no such post")
        record(request, "board", "write", post_id=post_id, reply_id=rep["id"], sha256=sha(text), size=len(text),
               summary=text[:120], author=rep["author"])
        return JSONResponse(rep, status_code=201)

    # ---- chat: the constrained peer-review channel and verdicts (ADR-0017) --------------------

    def chat_only(request: Request) -> dict[str, Any]:
        if request.state.service != "chat" or not st.peer_review:
            raise HTTPException(404)
        return st.peer_review

    @app.get("/api/messages")
    async def list_messages(request: Request, since: float | None = None):
        chat_only(request)
        agent, _ = who(request)
        items = chat.messages_for(agent, since)
        record(request, "chat", "read_list", count=len(items), epoch=chat.epoch,
               chars_received=sum(len(m["text"]) for m in items if m["to"] == agent and m["from"] != "runner"))
        return {"messages": [shown(m, "from", "to") for m in items], "epoch": chat.epoch}

    @app.post("/api/messages")
    async def send_message(request: Request):
        pr = chat_only(request)
        agent, _ = who(request)
        body = await request.json()
        to, text = agent_named(str(body.get("to") or "")), str(body.get("text") or "")
        if chat.epoch is None:
            raise HTTPException(409, "no episode is open")
        if to is None or to == agent:
            raise HTTPException(400, "to must name another user")
        if len(text) > int(pr["max_chars"]):
            record(request, "chat", "message_rejected", to=to, chars=len(text), epoch=chat.epoch, reason="too_long")
            return JSONResponse({"success": False, "error": f"MessageError: exceeds {pr['max_chars']} char limit",
                                 "chars_sent": 0}, status_code=400)
        if chat.sent_in_epoch(agent) >= int(pr["max_messages"]):
            record(request, "chat", "message_rejected", to=to, chars=len(text), epoch=chat.epoch, reason="limit")
            return JSONResponse({"success": False, "error": f"MessageError: message limit ({pr['max_messages']}) "
                                                              "reached for this episode", "chars_sent": 0},
                                status_code=400)
        msg = chat.add_message(agent, to, text)
        record(request, "chat", "write", to=to, chars=len(text), epoch=chat.epoch, message_id=msg["id"],
               sha256=sha(text), summary=text[:120])
        return JSONResponse({"success": True, "chars_sent": len(text), "id": msg["id"]}, status_code=201)

    @app.get("/api/verdicts")
    async def my_verdicts(request: Request):
        chat_only(request)
        agent, _ = who(request)
        items = [v for v in chat.verdicts_for(None) if v["from"] == agent]
        record(request, "chat", "read_verdicts", count=len(items), epoch=chat.epoch)
        return {"verdicts": [shown(v, "from", "about") for v in items], "epoch": chat.epoch}

    @app.post("/api/verdicts")
    async def submit_verdict(request: Request):
        chat_only(request)
        agent, _ = who(request)
        body = await request.json()
        about, verdict = agent_named(str(body.get("about") or "")), str(body.get("verdict") or "").lower()
        if chat.epoch is None:
            raise HTTPException(409, "no episode is open")
        if about is None or about == agent:
            raise HTTPException(400, "about must name another user")
        if verdict not in ("accept", "reject"):
            raise HTTPException(400, "verdict must be accept or reject")
        stored = chat.add_verdict(agent, about, verdict, str(body.get("reason") or "")[:500])
        if stored is None:
            record(request, "chat", "verdict_rejected", about=about, epoch=chat.epoch, reason="already_submitted")
            raise HTTPException(409, "verdict already submitted for this episode")
        record(request, "chat", "verdict", about=about, verdict=verdict, epoch=chat.epoch,
               summary=stored["reason"][:120])
        return JSONResponse(shown(stored, "from", "about"), status_code=201)

    # ---- wiki -----------------------------------------------------------------------------

    @app.get("/wiki/{slug}")
    @app.get("/{slug}.md")
    async def wiki_page(request: Request, slug: str):
        if request.state.service not in ("wiki", "services"):
            raise HTTPException(404)
        doc = wiki_doc(st, slug)
        if doc is None or not doc["visible"]:
            record(request, "wiki", "read_hidden" if doc else "read_missing", doc=slug)
            raise HTTPException(404, "no such page")
        text = doc["path"].read_text(encoding="utf-8")
        record(request, "wiki", "read", doc=slug, sha256=sha(text), size=len(text))
        return PlainTextResponse(strip_front_matter(text), media_type="text/markdown")

    @app.put("/wiki/{slug}")
    @app.put("/{slug}.md")
    async def wiki_edit(request: Request, slug: str):
        """Write a page (new or replaced). Needs an account with the `editor` role (ADR-0020)."""
        if request.state.service not in ("wiki", "services") or not SLUG.match(slug):
            raise HTTPException(404)
        require_role(request, "wiki", "editor", doc=slug)
        text = (await request.body()).decode("utf-8", "replace")
        if not text.strip():
            raise HTTPException(400, "empty page")
        st.wiki_edits.mkdir(parents=True, exist_ok=True)
        (st.wiki_edits / f"{slug}.md").write_text(text, encoding="utf-8")
        record(request, "wiki", "write", doc=slug, sha256=sha(text), size=len(text))
        return JSONResponse({"slug": slug, "sha256": sha(text)}, status_code=201)

    # ---- pastebin (hidden channel) --------------------------------------------------------

    @app.post("/api/paste")
    async def paste(request: Request):
        body = await request.json()
        agent, _ = who(request)
        text = str(body.get("text") or "")
        if not text:
            raise HTTPException(400, "text is required")
        item = pastes.add(username(agent), text)
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
        links = "".join(f'<a href="/files/{f.name}#sha256={file_sha(f)}">{f.name}</a><br>' for f in files)
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
        service = request.query_params.get("service") or ("git-receive-pack" if rest.endswith("git-receive-pack")
                                                           else "git-upload-pack")
        op = "write" if service == "git-receive-pack" else "read"
        if op == "write" and repo in st.protected_repos:
            require_role(request, "git", "push", repo=repo, git_service=service)
        body = await request.body()
        status, headers, out = await asyncio.to_thread(git_backend, st.data / "git", f"/{repo}.git/{rest}",
                                                       request, body, who(request)[0])
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


# ---- board / pastes / chat storage: append-only journals replayed into memory at start -----------

class Journal:
    """One JSONL file per store; a write appends one line and updates the in-memory state through
    `apply`, which is also how the file is replayed at start. Nothing is ever rewritten."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.lock = threading.Lock()
        self._file: Any = None
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.apply(json.loads(line))

    def append(self, rec: dict[str, Any]) -> None:
        """Caller holds the lock."""
        if self._file is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("a", encoding="utf-8", buffering=1)
        self._file.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.apply(rec)

    def apply(self, rec: dict[str, Any]) -> None:
        raise NotImplementedError


def _without(rec: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if k not in keys}


class Board(Journal):
    def __init__(self, path: pathlib.Path):
        self.posts: list[dict[str, Any]] = []       # index = id - 1
        super().__init__(path)

    def apply(self, rec: dict[str, Any]) -> None:
        kind = rec.get("kind")
        if kind == "post":
            self.posts.append(_without(rec, "kind") | {"replies": []})
        elif kind == "reply":
            self.posts[rec["post_id"] - 1]["replies"].append(_without(rec, "kind", "post_id"))
        elif kind == "delete":
            self.posts[rec["post_id"] - 1]["deleted"] = True

    def list(self, since: float | None = None, limit: int = LIST_LIMIT,
             offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """Newest first. Returns the page and the number of posts matching `since`."""
        posts = [p for p in self.posts if not p.get("deleted") and (since is None or p["ts"] > since)]
        total = len(posts)
        page = posts[max(0, total - offset - limit):max(0, total - offset)]
        return [_without(p, "replies") | {"replies": len(p["replies"])} for p in reversed(page)], total

    def get(self, post_id: int) -> dict[str, Any] | None:
        post = self.posts[post_id - 1] if 1 <= post_id <= len(self.posts) else None
        return None if post is None or post.get("deleted") else post

    def delete(self, post_id: int) -> None:
        with self.lock:
            self.append({"kind": "delete", "post_id": post_id, "ts": time.time()})

    def add(self, *, author: str, title: str, body: str, claimed: str | None) -> dict[str, Any]:
        with self.lock:
            self.append({"kind": "post", "id": len(self.posts) + 1, "ts": time.time(), "author": author,
                         "claimed_author": claimed, "title": title, "body": body})
            return self.posts[-1]

    def reply(self, post_id: int, *, author: str, body: str) -> dict[str, Any] | None:
        with self.lock:
            post = self.get(post_id)
            if post is None:
                return None
            self.append({"kind": "reply", "post_id": post_id, "id": len(post["replies"]) + 1, "ts": time.time(),
                         "author": author, "body": body})
            return post["replies"][-1]

    def html(self) -> str:
        items = []
        page, _ = self.list()
        for p in (self.posts[q["id"] - 1] for q in page):
            when = dt.datetime.fromtimestamp(p["ts"]).strftime("%H:%M:%S")
            replies = "".join(f"<blockquote>{html.escape(r['author'])}: {html.escape(r['body'])}</blockquote>"
                              for r in p["replies"])
            items.append(f"<li><b>{html.escape(p['title'] or '(no title)')}</b> · {html.escape(p['author'])} · {when}"
                         f"<pre>{html.escape(p['body'])}</pre>{replies}</li>")
        older = len(self.posts) - LIST_LIMIT
        more = f"<p>{older} older posts: GET /api/posts?offset={LIST_LIMIT}</p>" if older > 0 else ""
        return ("<!doctype html><title>board</title><h1>Team board</h1>"
                "<p>API: GET /api/posts[?limit=50&offset=0&since=ts] · POST /api/posts {title, body} · "
                "GET /api/posts/{id} · POST /api/posts/{id}/replies {body}</p>"
                "<ul>" + "".join(items) + "</ul>" + more)


class Chat(Journal):
    """Peer-review channel state: the open episode, messages (capped per episode by the caller),
    verdicts (one per agent per peer per episode) and the runner's feedback messages."""

    def __init__(self, path: pathlib.Path):
        self.epoch: str | None = None
        self.messages: list[dict[str, Any]] = []
        self.verdicts: list[dict[str, Any]] = []
        super().__init__(path)

    def apply(self, rec: dict[str, Any]) -> None:
        kind, item = rec.get("kind"), _without(rec, "kind")
        if kind == "epoch":
            self.epoch = item["epoch"]
        elif kind == "message":
            self.messages.append(item)
        elif kind == "verdict":
            self.verdicts.append(item)

    def set_epoch(self, epoch: str) -> None:
        with self.lock:
            self.append({"kind": "epoch", "ts": time.time(), "epoch": epoch})

    def sent_in_epoch(self, sender: str) -> int:
        return sum(1 for m in self.messages if m["from"] == sender and m["epoch"] == self.epoch)

    def add_message(self, sender: str, to: str, text: str) -> dict[str, Any]:
        with self.lock:
            self.append({"kind": "message", "id": len(self.messages) + 1, "ts": time.time(), "epoch": self.epoch,
                         "from": sender, "to": to, "text": text})
            return self.messages[-1]

    def add_feedback(self, to: str, epoch: str, text: str) -> dict[str, Any]:
        with self.lock:
            self.append({"kind": "message", "id": len(self.messages) + 1, "ts": time.time(), "epoch": epoch,
                         "from": "runner", "to": to, "text": text})
            return self.messages[-1]

    def messages_for(self, agent: str, since: float | None = None) -> list[dict[str, Any]]:
        return [m for m in self.messages
                if (m["to"] == agent or m["from"] == agent) and (since is None or m["ts"] > since)]

    def add_verdict(self, sender: str, about: str, verdict: str, reason: str) -> dict[str, Any] | None:
        with self.lock:
            if any(v["from"] == sender and v["about"] == about and v["epoch"] == self.epoch for v in self.verdicts):
                return None
            self.append({"kind": "verdict", "id": len(self.verdicts) + 1, "ts": time.time(), "epoch": self.epoch,
                         "from": sender, "about": about, "verdict": verdict, "reason": reason})
            return self.verdicts[-1]

    def verdicts_for(self, epoch: str | None) -> list[dict[str, Any]]:
        return [v for v in self.verdicts if epoch is None or v["epoch"] == epoch]


class Pastes(Journal):
    def __init__(self, path: pathlib.Path):
        self.items: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        super().__init__(path)

    def apply(self, rec: dict[str, Any]) -> None:
        self.items.append(rec)
        self.by_id[rec["id"]] = rec

    def add(self, author: str, text: str) -> dict[str, Any]:
        with self.lock:
            self.append({"id": secrets.token_hex(4), "ts": time.time(), "author": author, "text": text})
            return self.items[-1]

    def list(self) -> list[dict[str, Any]]:
        return [{"id": i["id"], "ts": i["ts"], "size": len(i["text"])} for i in self.items[-50:]]

    def get(self, paste_id: str) -> dict[str, Any] | None:
        return self.by_id.get(paste_id)


# ---- wiki --------------------------------------------------------------------------------------

FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def strip_front_matter(text: str) -> str:
    return FRONT.sub("", text, count=1)


_META_CACHE: dict[tuple[str, int, int], dict[str, str]] = {}   # (path, mtime_ns, size) -> front matter


def front_matter(path: pathlib.Path) -> dict[str, str]:
    """Parsed front matter, cached by the file's identity so the index does not re-read every page."""
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    meta = _META_CACHE.get(key)
    if meta is None:
        meta = {}
        head = FRONT.match(path.read_text(encoding="utf-8"))
        if head:
            for line in head.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
        _META_CACHE[key] = meta
    return meta


def wiki_doc(st: Settings, slug: str, path: pathlib.Path | None = None,
             now: float | None = None) -> dict[str, Any] | None:
    """One page by slug; `visible_from: <ISO time>` or `visible_after_seconds: N` (relative to
    `started`) in the front matter hides it until then, and the manifest's schedule overrides both."""
    edited = st.wiki_edits / f"{slug}.md"
    path = edited if edited.is_file() else (path or st.wiki_dir / f"{slug}.md")
    if not SLUG.match(slug) or not path.is_file():
        return None
    meta = front_matter(path)
    visible_at = None
    if slug in st.wiki_visibility:
        visible_at = st.started + float(st.wiki_visibility[slug])
    elif meta.get("visible_after_seconds"):
        visible_at = st.started + float(meta["visible_after_seconds"])
    elif meta.get("visible_from"):
        visible_at = dt.datetime.fromisoformat(meta["visible_from"]).timestamp()
    now = time.time() if now is None else now
    return {"slug": slug, "path": path, "title": meta.get("title") or slug,
            "visible_at": visible_at, "visible": visible_at is None or now >= visible_at}


def wiki_docs(st: Settings) -> list[dict[str, Any]]:
    """Every page in the wiki dir, with its visibility now."""
    now = time.time()
    slugs = {p.stem for d in (st.wiki_dir, st.wiki_edits) if d.is_dir() for p in d.glob("*.md")}
    docs = (wiki_doc(st, slug, None, now) for slug in sorted(slugs))
    return [d for d in docs if d is not None]


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


_SHA_CACHE: dict[tuple[str, int, int], str] = {}   # (path, mtime_ns, size) -> sha256


def file_sha(path: pathlib.Path) -> str:
    """sha256 of a distribution file, computed once per file version (a 50 MB wheel per request is not)."""
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    digest = _SHA_CACHE.get(key)
    if digest is None:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = _SHA_CACHE[key] = h.hexdigest()
    return digest


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
