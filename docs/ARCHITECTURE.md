# Architecture

![Architecture](images/architecture.png)

## Components

```mermaid
flowchart LR
  subgraph Clients
    U[People: web portal]
    A[AI agents: Copilot, Claude Code, Cursor]
  end
  U -->|SSO| E[Microsoft Entra ID]
  A -->|OAuth token| E
  E -->|verified principal| W[App Service: FastAPI<br/>portal + REST + MCP /mcp]
  W -->|managed identity| C[(Cosmos DB serverless<br/>projects · entries · requests)]
  W -->|managed identity| O[Azure OpenAI<br/>gpt-5-mini]
  W -.->|scale-out| S[Azure AI Search]
  W --> AI[Application Insights]
  W -->|optional| R[Azure Resource Manager<br/>export templates]
  M[Azure Marketplace<br/>ARM template] -.deploys.-> W
```

## Access flow

```mermaid
sequenceDiagram
  participant B as Maria (Team B) / her agent
  participant R as Azure Recall
  participant O as Priya (project owner)
  B->>R: plan_build("SQL agent for a retail client")
  R-->>B: org-visible lessons + locked: "Contoso Retail SQL Agent" (owner Priya)
  B->>R: request_project_access(project, reason)
  R-->>O: pending request in Access requests
  O->>R: Approve
  B->>R: plan_build(...) again
  R-->>B: full checklist + 7 lessons, export AGENTS.md
```

## Data model (Cosmos DB container `items`, partition key `/kind`)

| kind | Fields |
|---|---|
| `project` | id, name, owner (UPN), members[], visibility (`org` / `restricted`), domain, client, description |
| `entry` | id, title, body, tags[], project, domain, author, source, updated, helpful, outdated, template? |
| `request` | id, project, owner, requester, reason, status (`pending` / `approved` / `denied`), created, decided |

Locally (no `COSMOS_ENDPOINT`) the same documents are JSON files in `kb/`.

## Access rules (`app.py`)

| Action | Allowed for |
|---|---|
| Discover a title in a restricted project | everyone signed in |
| Read an entry | org-visible project, no project, or owner/member |
| Write to a project | owner/member |
| Approve or deny a request | project owner only |
| Delete an entry | author or project member |

## Identity

- `AUTH_MODE=entra`: user = `X-MS-CLIENT-PRINCIPAL-NAME` set by App Service Easy Auth after validating the Entra ID token (App Service removes any client-sent copy of the header). Agents call `/mcp` with an Entra access token; Easy Auth validates it the same way.
- `AUTH_MODE=demo`: persona switcher for demos and local development.
- `RECALL_API_KEY` (optional): a shared key for headless agents; it maps to a service identity that sees org-visible knowledge only.

## Scaling and cost

- Cosmos DB serverless: pay per request, near zero at idle; move to autoscale throughput for large tenants.
- Search: today Azure OpenAI ranks titles and tags (fine to roughly a thousand entries). Beyond that, index entries in **Azure AI Search** with vector + semantic ranking and a `project` filter computed from the caller's memberships (security trimming), so the LLM only sees permitted top-k results.
- App Service B1 → P1v3 with autoscale; stateless app, so scale-out is horizontal.
