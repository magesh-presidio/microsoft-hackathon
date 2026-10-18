"""Smoke test: python test_app.py  (temp file store, demo identities, no Azure needed)"""
import os, tempfile
os.environ["KB_DIR"] = tempfile.mkdtemp()
os.environ["AUTH_MODE"] = "demo"
from fastapi.testclient import TestClient
import app

PRIYA, ARJUN, MARIA = app.DEMO_USERS
as_ = lambda u: {"x-demo-user": u}

with TestClient(app.app) as c:
    # project owner creates a restricted project and writes to it
    assert c.post("/api/projects", json={"name": "Contoso SQL", "visibility": "restricted"}, headers=as_(PRIYA)).status_code == 200
    c.post("/api/entries", json={"title": "SQL agent: read-only user", "body": "Use db_datareader only.",
                                 "tags": ["sql-agent", "security"], "project": "contoso-sql"}, headers=as_(PRIYA))
    assert c.post("/api/entries", json={"title": "x", "body": "y", "project": "contoso-sql"}, headers=as_(MARIA)).status_code == 403

    # another team discovers it exists but cannot read it
    p = c.get("/api/plan", params={"q": "sql agent"}, headers=as_(MARIA)).json()
    assert not p["entries"] and p["locked_projects"][0]["owner"] == PRIYA
    hit = c.get("/api/search", params={"q": "sql agent"}, headers=as_(MARIA)).json()[0]
    assert hit["locked"] and "body" not in hit

    # access request -> only the owner can approve -> requester can read
    r = c.post("/api/projects/contoso-sql/access", json={"reason": "same problem for Fabrikam"}, headers=as_(MARIA)).json()
    assert c.get("/api/me", headers=as_(PRIYA)).json()["inbox"][0]["id"] == r["id"]
    assert c.post(f"/api/requests/{r['id']}", json={"approve": True}, headers=as_(ARJUN)).status_code == 403
    assert c.post(f"/api/requests/{r['id']}", json={"approve": True}, headers=as_(PRIYA)).json()["status"] == "approved"
    p = c.get("/api/plan", params={"q": "sql agent"}, headers=as_(MARIA)).json()
    assert p["entries"][0]["id"] == "sql-agent-read-only-user" and not p["locked_projects"]
    cov = {x["area"]: x["covered"] for x in p["coverage"]}
    assert cov["Security"] and not cov["Operations"], cov

    # votes rank up / flag stale; an edit resets the flag
    c.post("/api/entries/sql-agent-read-only-user/vote", json={"kind": "helpful"}, headers=as_(MARIA))
    for _ in range(2): v = c.post("/api/entries/sql-agent-read-only-user/vote", json={"kind": "outdated"}, headers=as_(MARIA)).json()
    assert v["helpful"] == 1 and v["stale"]
    c.post("/api/entries", json={"title": "SQL agent: read-only user", "body": "Updated.", "tags": ["security"]}, headers=as_(PRIYA))
    assert not c.get("/api/entries", headers=as_(PRIYA)).json()[0]["stale"]
    assert c.post("/api/entries/nope/vote", json={"kind": "helpful"}).status_code == 404

    # capture (org-wide) splits notes into entries; plan.md exports
    r = c.post("/api/capture", json={"text": "Always set a query timeout on the agent.\n\nLog every generated query for audit review."}).json()
    assert len(r["saved"]) == 2
    assert c.post("/api/capture", json={"text": ""}).status_code == 400
    assert "# Build plan: sql agent" in c.get("/api/plan.md", params={"q": "sql agent"}, headers=as_(MARIA)).text
    assert c.delete("/api/entries/sql-agent-read-only-user", headers=as_(ARJUN)).status_code == 403
    assert c.delete("/api/entries/sql-agent-read-only-user", headers=as_(PRIYA)).status_code == 200
    assert c.delete("/api/entries/../x").status_code in (404, 405)

    # entra mode: identity comes only from Easy Auth headers; anonymous writes are rejected
    app.AUTH_MODE = "entra"
    assert c.post("/api/entries", json={"title": "t", "body": "b"}).status_code == 401
    assert c.get("/api/me", headers={"x-ms-client-principal-name": "Dev@Contoso.com"}).json()["user"] == "dev@contoso.com"
    assert c.get("/api/me", headers={"x-demo-user": PRIYA}).json()["user"] == "anonymous"
print("ok")
