"""Azure Recall: centralized, access-controlled knowledge that outlives the project that produced it.

Knowledge (lessons, pitfalls, AI-session findings) is captured per *project*. Anyone in the org can discover that
a project solved a problem; reading restricted knowledge needs the project owner's approval (Entra ID identity).
Query it from the web UI, REST, or MCP (any coding agent: Claude Code, GitHub Copilot, Cursor...).
"""
import contextlib, contextvars, datetime, json, os, pathlib, re, urllib.request, uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

ROOT = pathlib.Path(__file__).parent
ID_RE = r"[a-z0-9][a-z0-9\-]{0,79}"
API_KEY = os.environ.get("RECALL_API_KEY")  # if set: agents without an Entra token must send x-api-key
AUTH_MODE = os.environ.get("AUTH_MODE", "demo")  # "entra": trust App Service Easy Auth headers; "demo": persona switcher
AGENT = "agent@recall"
DEMO_USERS = ["priya@contoso.com", "arjun@contoso.com", "maria@contoso.com"]
USER: contextvars.ContextVar[str] = contextvars.ContextVar("user", default="anonymous")

if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor()


# ---- store: Cosmos DB (one container, partition key /kind) when configured, else JSON files on disk
class FileStore:
    def __init__(self, root: pathlib.Path):
        self.root = root

    def _dir(self, kind: str) -> pathlib.Path:
        d = self.root if kind == "entry" else self.root / f"_{kind}"  # entries stay at kb/*.json (seed format)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get(self, kind, id):
        p = self._dir(kind) / f"{id}.json"
        return json.loads(p.read_text()) if re.fullmatch(ID_RE, id) and p.exists() else None

    def put(self, kind, doc):
        (self._dir(kind) / f"{doc['id']}.json").write_text(json.dumps(doc))

    def list(self, kind):
        return [json.loads(p.read_text()) for p in self._dir(kind).glob("*.json")]

    def delete(self, kind, id):
        (self._dir(kind) / f"{id}.json").unlink(missing_ok=True)


class CosmosStore:
    def __init__(self, endpoint: str):
        from azure.cosmos import CosmosClient
        cred = os.environ.get("COSMOS_KEY")
        if not cred:
            from azure.identity import DefaultAzureCredential  # managed identity in Azure, `az login` locally
            cred = DefaultAzureCredential()
        self.c = CosmosClient(endpoint, cred).get_database_client("recall").get_container_client("items")

    def get(self, kind, id):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        try:
            return self._clean(self.c.read_item(id, partition_key=kind))
        except CosmosResourceNotFoundError:
            return None

    def put(self, kind, doc):
        self.c.upsert_item({**doc, "kind": kind})

    def list(self, kind):
        return [self._clean(d) for d in self.c.query_items("SELECT * FROM c", partition_key=kind)]

    def delete(self, kind, id):
        with contextlib.suppress(Exception):
            self.c.delete_item(id, partition_key=kind)

    @staticmethod
    def _clean(d):
        return {k: v for k, v in d.items() if not k.startswith("_") and k != "kind"}


KB = pathlib.Path(os.environ.get("KB_DIR", ROOT / "kb"))
db = CosmosStore(os.environ["COSMOS_ENDPOINT"]) if os.environ.get("COSMOS_ENDPOINT") else FileStore(KB)

m = FastMCP("azure-recall", stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))  # auth below instead


@contextlib.asynccontextmanager
async def lifespan(_):
    async with m.session_manager.run():
        yield


app = FastAPI(title="Azure Recall", lifespan=lifespan)


# ---- identity: Entra ID via App Service Easy Auth (it strips client-sent X-MS-* headers), or a demo persona
def identify(req: Request) -> str:
    if AUTH_MODE == "entra" and req.headers.get("x-ms-client-principal-name"):
        return req.headers["x-ms-client-principal-name"].lower()
    if AUTH_MODE == "demo":
        return (req.headers.get("x-demo-user") or DEMO_USERS[2]).lower()
    if API_KEY and req.headers.get("x-api-key") == API_KEY:
        return AGENT
    return "anonymous"


@app.middleware("http")
async def auth(req: Request, call_next):
    user = identify(req)
    guarded = req.method not in ("GET", "HEAD") or req.url.path.startswith("/mcp")
    if guarded and user == "anonymous":
        return JSONResponse({"error": "sign in with Entra ID or send x-api-key"}, 401)
    USER.set(user)  # read by REST handlers and MCP tools (contextvars flow into the handler task)
    return await call_next(req)


def today() -> str:
    return datetime.date.today().isoformat()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80]


# ---- projects + access: org-visible knowledge is readable by everyone; restricted needs owner approval
def project(pid: str | None) -> dict | None:
    return db.get("project", pid) if pid else None


def is_member(p: dict | None, user: str) -> bool:
    return bool(p) and (user == p["owner"] or user in p.get("members", []))


def can_read(e: dict, user: str) -> bool:
    p = project(e.get("project"))
    return not p or p.get("visibility") == "org" or is_member(p, user)


def create_project(name: str, owner: str, visibility="restricted", domain="engineering", client="", description="") -> str:
    pid = slug(name)
    if not pid:
        raise ValueError("project name required")
    if db.get("project", pid):
        raise ValueError("project already exists")
    db.put("project", {"id": pid, "name": name.strip(), "owner": owner, "members": [], "client": client,
                       "visibility": "org" if visibility == "org" else "restricted", "domain": domain,
                       "description": description, "created": today()})
    return pid


def request_access(pid: str, user: str, reason: str) -> dict:
    p = project(pid)
    if not p:
        raise KeyError(pid)
    if is_member(p, user):
        return {"status": "approved", "project": pid}
    for r in db.list("request"):
        if r["project"] == pid and r["requester"] == user and r["status"] == "pending":
            return r
    r = {"id": uuid.uuid4().hex[:12], "project": pid, "project_name": p["name"], "owner": p["owner"],
         "requester": user, "reason": reason.strip()[:500], "status": "pending", "created": today()}
    db.put("request", r)
    return r


def decide(rid: str, user: str, approve: bool) -> dict:
    r = db.get("request", rid)
    if not r:
        raise KeyError(rid)
    p = project(r["project"])
    if not p or p["owner"] != user:
        raise PermissionError("only the project owner can decide")
    r.update(status="approved" if approve else "denied", decided=today())
    if approve and r["requester"] not in p["members"]:
        p["members"].append(r["requester"])
        db.put("project", p)
    db.put("request", r)
    return r


# ---- entries: {id,title,body,tags,project,domain,author,source,updated,template?,helpful,outdated}
def add(title: str, body: str, tags=(), source="manual", template=None, project_id=None, domain=None, author=None) -> str:
    id = slug(title)
    author = author or USER.get()
    if not id or not body.strip() or len(body) > 50_000:
        raise ValueError("title and body (<50k chars) required")
    prev = db.get("entry", id) or {}
    p = project(project_id or prev.get("project"))
    if p and not is_member(p, author):
        raise PermissionError("only project members can write to this project")
    e = {"id": id, "title": title.strip(), "body": body.strip(), "tags": [str(t) for t in tags], "source": source,
         "project": p["id"] if p else None, "domain": domain or (p or {}).get("domain") or prev.get("domain") or "engineering",
         "author": prev.get("author", author), "updated": today(), "template": template,
         "helpful": prev.get("helpful", 0), "outdated": 0}
    db.put("entry", e)
    return id


def view(e: dict, user: str | None = None) -> dict:
    user = user or USER.get()
    p = project(e.get("project"))
    age = (datetime.date.today() - datetime.date.fromisoformat(e["updated"])).days
    base = {"id": e["id"], "title": e["title"], "tags": e["tags"], "domain": e.get("domain", "engineering"),
            "project": e.get("project"), "project_name": (p or {}).get("name"), "owner": (p or {}).get("owner"),
            "client": (p or {}).get("client"), "updated": e["updated"]}
    if not can_read(e, user):  # discoverable, not readable: title + who to ask
        return {**base, "locked": True}
    return {**base, "locked": False, "body": e["body"], "source": e["source"], "author": e.get("author"),
            "helpful": e.get("helpful", 0), "outdated": e.get("outdated", 0), "has_template": bool(e.get("template")),
            "stale": e.get("outdated", 0) >= 2 or age > 365}


def vote(id: str, kind: str) -> dict:
    e = db.get("entry", id)
    if kind not in ("helpful", "outdated") or not e or not can_read(e, USER.get()):
        raise KeyError(id)
    e[kind] = e.get(kind, 0) + 1
    db.put("entry", e)
    return view(e)


# ---- search: Azure OpenAI ranking when configured, keyword fallback
def tok(s: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", s.lower())}


def score(e: dict, q: set[str]) -> int:
    base = 3 * len(q & tok(e["title"])) + len(q & tok(" ".join(e["tags"]) + " " + e["body"]))
    return base + min(e.get("helpful", 0), 3) - min(e.get("outdated", 0), 2) if base else 0


_token = None


def ai_chat(system: str, user: str) -> str | None:
    global _token
    ep, key, dep = (os.environ.get(k) for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT"))
    if not (ep and dep):
        return None
    try:
        if key:
            auth = {"api-key": key}
        else:  # keyless: managed identity with the "Cognitive Services OpenAI User" role
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            _token = _token or get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
            auth = {"authorization": f"Bearer {_token()}"}
        req = urllib.request.Request(
            f"{ep.rstrip('/')}/openai/deployments/{dep}/chat/completions?api-version=2025-04-01-preview",
            json.dumps({"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        **({"reasoning_effort": "minimal"} if dep.startswith(("gpt-5", "o")) else {})}).encode(),
            {**auth, "content-type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=60))["choices"][0]["message"]["content"]
    except Exception:
        return None


def search(q: str, n: int = 10, domain: str | None = None) -> list[dict]:
    es = [e for e in db.list("entry") if not domain or e.get("domain") == domain]
    if q.strip() and es:
        digest = [{"id": e["id"], "title": e["title"], "tags": e["tags"]} for e in es]
        out = ai_chat("Rank the knowledge entries most useful for the request, best first. Omit unrelated ones. "
                      "Reply ONLY with a JSON array of ids.", f"Request: {q}\nEntries: {json.dumps(digest)}")
        try:  # past a few hundred entries: move to Azure AI Search (hybrid + semantic ranker), see docs/ARCHITECTURE.md
            ids = json.loads(re.search(r"\[.*\]", out, re.S).group())
            by = {e["id"]: e for e in es}
            return [by[i] for i in ids if i in by][:n]
        except Exception:
            pass
    qt = tok(q)
    ranked = sorted(((score(e, qt), e) for e in es), key=lambda x: -x[0])
    return [e for s, e in ranked if s or not qt][:n]


AREAS = [("Security", r"security|guardrail|identity|access|privacy|compliance"), ("Cost", r"cost|quota|budget|pricing"),
         ("Testing", r"evaluation|testing"), ("Operations", r"monitoring|operations|logging|observab"),
         ("Steps", r"how-to|checklist|steps")]


def coverage(es: list[dict]) -> list[dict]:
    text = [" ".join(e["tags"]) + " " + e["title"] for e in es]
    return [{"area": a, "covered": any(re.search(p, t, re.I) for t in text)} for a, p in AREAS]


def capture(text: str, source="capture", project_id=None) -> list[str]:
    """Turn an AI chat transcript, postmortem or PR description into entries."""
    out = ai_chat("Extract distinct, reusable lessons (decisions, pitfalls, fixes, steps) that another team could reuse. "
                  'Drop client secrets and personal data. Reply ONLY with a JSON array of {"title","body","tags"}.', text[:12000])
    try:
        items = json.loads(re.search(r"\[.*\]", out, re.S).group())
    except Exception:
        chunks = [c.strip(" -*\t") for c in re.split(r"\n\s*\n|\n[-*] ", text) if len(c.strip()) > 20]
        items = [{"title": " ".join(c.split()[:9]), "body": c, "tags": []} for c in chunks]
    ids = []
    for i in items[:20]:
        with contextlib.suppress(ValueError, KeyError, TypeError):
            ids.append(add(i["title"], i["body"], [*i.get("tags", []), "captured"], source, project_id=project_id))
    return ids


def plan(goal: str, domain: str | None = None) -> dict:
    user = USER.get()
    hits = search(goal, domain=domain)
    open_ = [e for e in hits if can_read(e, user)]
    locked = [view(e) for e in hits if not can_read(e, user)]
    summary = ai_chat("You are a solution advisor. Using ONLY the knowledge entries, write a concise ordered checklist of "
                      "what to consider and do. Cite entry titles.",
                      f"Goal: {goal}\nEntries: {json.dumps([{'title': e['title'], 'body': e['body']} for e in open_])}") if open_ else None
    projects = {}  # restricted projects that solved related problems: who to ask
    for v in locked:
        projects.setdefault(v["project"], {"project": v["project"], "name": v["project_name"], "owner": v["owner"],
                                           "client": v["client"], "titles": []})["titles"].append(v["title"])
    return {"goal": goal, "checklist": summary, "entries": [view(e) for e in open_], "coverage": coverage(open_),
            "locked_projects": list(projects.values())}


def plan_md(goal: str) -> str:
    """Plan as a context file a coding agent reads (save as AGENTS.md / CLAUDE.md / copilot-instructions.md)."""
    p = plan(goal)
    out = [f"# Build plan: {goal}", ""] + ([p["checklist"], ""] if p["checklist"] else [])
    out += ["## What earlier teams learned", ""]
    out += [f"- **{e['title']}** ({e['project_name'] or 'org-wide'}): {e['body']}" for e in p["entries"]] or ["- Nothing recorded yet."]
    if p["locked_projects"]:
        out += ["", "## Restricted projects with related knowledge (request access)", ""]
        out += [f"- {x['name']} (owner {x['owner']}): {', '.join(x['titles'])}" for x in p["locked_projects"]]
    gaps = [c["area"] for c in p["coverage"] if not c["covered"]]
    if gaps:
        out += ["", "## Not covered yet (decide these yourself)", ""] + [f"- {g}" for g in gaps]
    return "\n".join(out) + "\n"


# ---- Azure scan: each resource group becomes an entry carrying its redeployable ARM template
def scan(sub_id: str) -> list[str]:
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.resource import ResourceManagementClient
    c = ResourceManagementClient(DefaultAzureCredential(), sub_id)
    saved = []
    for rg in c.resource_groups.list():
        res = [f"{r.type} ({r.name})" for r in c.resources.list_by_resource_group(rg.name)]
        if not res:
            continue
        try:
            tpl = c.resource_groups.begin_export_template(
                rg.name, {"resources": ["*"], "options": "IncludeParameterDefaultValue"}).result().template
        except Exception:
            tpl = None
        saved.append(add(f"Azure build: {rg.name}", "Deployed resources:\n" + "\n".join(res),
                         ["azure-scan", *(rg.tags or {}).values()], "azure-scan", tpl))
    return saved


# ---- MCP tools (served at /mcp for remote agents; mcp_server.py runs the same tools over stdio)
@m.tool()
def plan_build(goal: str) -> dict:
    """Call BEFORE starting a task (e.g. 'SQL agent app', 'healthcare RFP response'). Returns what earlier teams
    learned, known pitfalls, gaps, and restricted projects that solved related problems (and who owns them)."""
    return plan(goal)


@m.tool()
def search_knowledge(query: str) -> list[dict]:
    """Search the shared knowledge base. Locked results show the title and owner only; use request_access."""
    return [view(e) for e in search(query)]


@m.tool()
def add_knowledge(title: str, body: str, tags: list[str] = [], project: str = "") -> str:
    """Save a lesson learned, pitfall or how-to so the next team gets it. Same title updates the entry."""
    return add(title, body, tags, source="agent", project_id=project or None)


@m.tool()
def capture_notes(notes: str, project: str = "") -> list[str]:
    """Call after finishing a task: pass a summary of the session (what was tried, what worked, what failed).
    It is split into reusable entries."""
    return capture(notes, source="agent", project_id=project or None)


@m.tool()
def rate_knowledge(id: str, helpful: bool) -> dict:
    """Report whether an entry worked (helpful) or was wrong/out of date."""
    return vote(id, "helpful" if helpful else "outdated")


@m.tool()
def request_project_access(project: str, reason: str) -> dict:
    """Ask a restricted project's owner for read access. The owner approves in Azure Recall."""
    return request_access(project, USER.get(), reason)


# ---- REST
def need(fn, *a):
    try:
        return fn(*a)
    except KeyError:
        raise HTTPException(404)
    except PermissionError as ex:
        raise HTTPException(403, str(ex))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/me")
def api_me():
    u = USER.get()
    mine = [p for p in db.list("project") if is_member(p, u)]
    reqs = db.list("request")
    inbox = [r for r in reqs if r["owner"] == u and r["status"] == "pending"]
    sent = [r for r in reqs if r["requester"] == u]
    return {"user": u, "auth_mode": AUTH_MODE, "demo_users": DEMO_USERS if AUTH_MODE == "demo" else [],
            "projects": [p["id"] for p in mine], "inbox": inbox, "sent": sent}


@app.get("/api/search")
def api_search(q: str = "", domain: str | None = None):
    return [view(e) for e in search(q, domain=domain)]


@app.get("/api/entries")
def api_list():
    return sorted((view(e) for e in db.list("entry")), key=lambda e: e["title"].lower())


@app.get("/api/plan")
def api_plan(q: str, domain: str | None = None):
    return plan(q, domain)


@app.get("/api/plan.md")
def api_plan_md(q: str):
    return Response(plan_md(q), media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="AGENTS.md"'})


@app.post("/api/capture")
def api_capture(body: dict):
    ids = need(capture, str(body.get("text", "")), "capture", body.get("project") or None)
    if not ids:
        raise HTTPException(400, "nothing to capture: paste at least a sentence or two")
    return {"saved": ids}


@app.post("/api/entries/{id}/vote")
def api_vote(id: str, body: dict):
    return need(vote, id, body.get("kind", ""))


@app.post("/api/entries")  # create or update (same title = same id)
def api_add(body: dict):
    return {"id": need(add, body.get("title", ""), body.get("body", ""), body.get("tags", []), body.get("source", "manual"),
                       None, body.get("project") or None, body.get("domain") or None)}


@app.delete("/api/entries/{id}")
def api_delete(id: str):
    e = db.get("entry", id)
    if not e:
        raise HTTPException(404)
    u, p = USER.get(), project(e.get("project"))
    if not (u == e.get("author") or (p and is_member(p, u)) or (not p and u != "anonymous")):
        raise HTTPException(403, "only the author or project members can delete")
    db.delete("entry", id)
    return {"deleted": id}


@app.get("/api/entries/{id}/template")
def api_template(id: str):
    e = db.get("entry", id)
    if not e or not can_read(e, USER.get()) or not e.get("template"):
        raise HTTPException(404)
    return JSONResponse(e["template"], headers={"Content-Disposition": f'attachment; filename="{id}.json"'})


@app.get("/api/projects")
def api_projects():
    u, es = USER.get(), db.list("entry")
    return sorted(({**p, "entries": sum(e.get("project") == p["id"] for e in es), "member": is_member(p, u)}
                   for p in db.list("project")), key=lambda p: p["name"].lower())


@app.post("/api/projects")
def api_create_project(body: dict):
    return {"id": need(create_project, body.get("name", ""), USER.get(), body.get("visibility", "restricted"),
                       body.get("domain", "engineering"), body.get("client", ""), body.get("description", ""))}


@app.post("/api/projects/{pid}/access")
def api_request_access(pid: str, body: dict):
    return need(request_access, pid, USER.get(), str(body.get("reason", "")))


@app.post("/api/requests/{rid}")
def api_decide(rid: str, body: dict):
    return need(decide, rid, USER.get(), bool(body.get("approve")))


@app.post("/api/scan")
def api_scan(body: dict):
    try:
        return {"saved": scan(body["subscription_id"])}
    except Exception as ex:
        return JSONResponse({"error": str(ex)}, 500)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/", m.streamable_http_app())  # last: serves /mcp; every route above wins first
