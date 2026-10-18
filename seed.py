"""Load demo projects and knowledge into the configured store (files or Cosmos DB).  usage: python seed.py"""
import app

PRIYA, ARJUN, MARIA = app.DEMO_USERS
PROJECTS = [  # (name, owner, visibility, domain, client, description)
    ("Contoso Retail SQL Agent", PRIYA, "restricted", "engineering", "Contoso Retail",
     "Natural-language analytics over the retail sales warehouse with Azure OpenAI."),
    ("Fabrikam Policy Chatbot", ARJUN, "org", "engineering", "Fabrikam Insurance",
     "RAG assistant over 40k insurance policy PDFs, Azure AI Search + Azure OpenAI."),
    ("Platform Baseline", ARJUN, "org", "engineering", "",
     "Org-wide landing-zone defaults every Azure web workload should start from."),
    ("Northwind Health RFP", PRIYA, "restricted", "sales", "Northwind Health",
     "Winning response to a 120-question healthcare cloud RFP (HIPAA, data residency)."),
    ("Customer Onboarding Playbook", MARIA, "org", "operations", "",
     "How delivery teams kick off a new client engagement in week one."),
]
OWNER = {"sql-agent": "contoso-retail-sql-agent", "rag-chatbot": "fabrikam-policy-chatbot",
         "web-app-database": "platform-baseline"}
BUSINESS = [
    ("northwind-health-rfp", "Healthcare RFP: answer HIPAA questions with the Microsoft BAA, not prose",
     "Reviewers score HIPAA answers on evidence. Attach the Microsoft Business Associate Agreement and the Azure HIPAA "
     "compliance offering page; list the in-scope services by name. Free-text assurances lost points last time.",
     ["rfp", "compliance", "healthcare"]),
    ("northwind-health-rfp", "Healthcare RFP: data residency answer template",
     "State the Azure region pair, that customer data at rest stays in-geo, and which services (e.g. Azure OpenAI global "
     "deployments) process data elsewhere and how that is configured off. Clients asked this in 3 of 4 RFPs.",
     ["rfp", "compliance", "data-residency"]),
    ("northwind-health-rfp", "Healthcare RFP: pricing section that won",
     "Show a pilot (8 weeks, fixed price) then a monthly run-rate with Azure consumption estimated from the pricing "
     "calculator export. Separate our services fee from Azure spend; procurement compares them separately.",
     ["rfp", "pricing", "cost"]),
    ("customer-onboarding-playbook", "Onboarding: day-one checklist for a new client",
     "1) Guest accounts in the client tenant via Entra B2B, 2) a shared Teams channel, 3) a subscription with a budget "
     "alert, 4) a RACI signed by the client sponsor. Teams that skipped the RACI lost ~2 weeks to approvals.",
     ["onboarding", "checklist", "steps"]),
    ("customer-onboarding-playbook", "Onboarding: get data access approved in week one",
     "Data access approvals are the longest lead time item. Send the data request form on day one with the exact tables "
     "and the managed identity that will read them.", ["onboarding", "security", "access"]),
]

import logging
logging.getLogger("azure").setLevel(logging.WARNING)
app.USER.set(app.AGENT)
if isinstance(app.db, app.CosmosStore):  # first run against Cosmos DB: import the bundled kb/*.json entries
    for e in app.FileStore(app.KB).list("entry"):
        if not app.db.get("entry", e["id"]):
            app.db.put("entry", e)
for name, owner, vis, domain, client, desc in PROJECTS:
    pid = app.slug(name)
    if not app.db.get("project", pid):
        app.create_project(name, owner, vis, domain, client, desc)
for e in app.db.list("entry"):  # attach the original seed entries to their projects
    pid = next((p for k, p in OWNER.items() if e["id"].startswith(k)), None)
    if pid and not e.get("project"):
        p = app.project(pid)
        app.db.put("entry", {**e, "project": pid, "domain": p["domain"], "author": p["owner"]})
for pid, title, body, tags in BUSINESS:
    p = app.project(pid)
    app.add(title, body, tags, "seed", project_id=pid, domain=p["domain"], author=p["owner"])
print(f"{len(app.db.list('project'))} projects, {len(app.db.list('entry'))} entries")
