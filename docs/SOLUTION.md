# Solution document: Azure Recall

## 1. Problem

Most development work now happens with AI assistants. The investigation (reading docs, comparing approaches, hitting and fixing errors) happens inside a chat with Claude, ChatGPT or GitHub Copilot. When the project ends:

- the **code** is kept, but the **reasoning** (why this design, what failed, which settings mattered) is lost in a private chat history;
- a second team facing **the same client problem** starts from the beginning and pays for the same research again;
- the knowledge that *is* written down sits in wikis nobody searches, cannot be trusted (is it still current?), and has no clear owner to ask;
- client work is confidential, so "just make everything public" is not an option.

The result: duplicated effort, repeated mistakes, slower delivery and knowledge that walks out of the door with people.

## 2. Solution

Azure Recall is a central knowledge layer that sits **next to the AI tools teams already use**.

| Step | What happens | Who |
|---|---|---|
| **Capture** | An AI session, postmortem or PR description is turned into short, reusable lessons by Azure OpenAI. Secrets and personal data are dropped. Agents can save lessons themselves through MCP at the end of a task. | Team A or its agent |
| **Organize** | Each lesson belongs to a **project** (with client, domain and an owner). Projects are *org-visible* or *restricted*. | Project owner |
| **Discover** | Before starting, a person or agent describes the task. Azure Recall returns an ordered checklist, the relevant lessons, and coverage gaps (security, cost, testing, operations, steps). | Team B or its agent |
| **Request access** | Restricted knowledge shows up as a locked result: title, project and owner. One click sends a request with a business justification. | Team B |
| **Approve** | The owner approves or denies in the portal. Approval is bound to the requester's Entra ID identity. | Project owner |
| **Reuse & improve** | The plan exports as `AGENTS.md` so the coding agent follows it. Users vote lessons helpful or out of date; stale advice is flagged. | Everyone |

### Beyond code

The model (project → owner → lessons → access requests) is domain-agnostic. The demo includes:

- **Engineering:** SQL agent, RAG chatbot, platform baseline
- **Sales:** healthcare RFP answers that won (HIPAA, data residency, pricing)
- **Operations:** client onboarding playbook

## 3. Architecture

![Architecture](images/architecture.png)

![Workflow](images/workflow.png)

See [ARCHITECTURE.md](ARCHITECTURE.md) for components, data model, security and scaling.

## 4. Azure and Microsoft services used

| Service | Why |
|---|---|
| Azure App Service | Managed hosting for portal, REST API and MCP server; built-in Easy Auth |
| Microsoft Entra ID | One identity for people and agents; the basis of owner approval |
| Azure Cosmos DB (serverless) | Low-cost, globally distributable store; Entra RBAC on the data plane |
| Azure OpenAI | Ranking, checklist generation, lesson extraction and redaction |
| Managed identity + Azure RBAC | No keys or connection strings anywhere |
| Application Insights | Telemetry and adoption analytics |
| Azure Resource Manager | Turn deployed resource groups into reusable templates |
| Azure Marketplace | Distribution and billing |
| *(roadmap)* Azure AI Search | Hybrid + semantic retrieval with security trimming at scale |
| *(roadmap)* Logic Apps, Teams, Copilot Studio | Owner notifications; Recall inside Microsoft 365 Copilot |

## 5. Why this is a business for Microsoft

1. **Marketplace product.** Published as an *Azure Application* (solution template or managed app). The ARM template in `marketplace/` deploys into the customer's subscription, so data stays in their tenant: an easy sell to regulated customers.
2. **Consumption underneath.** Every deployment runs on App Service, Cosmos DB, Azure OpenAI and (at scale) AI Search. Usage grows with the customer's knowledge base.
3. **Strengthens the Microsoft AI stack.** Works with GitHub Copilot and Microsoft 365 Copilot via MCP and Copilot Studio, and makes Entra ID the control point for organizational knowledge.

## 6. Security and responsible AI

- Identity from Entra ID only (`AUTH_MODE=entra`); App Service strips client-sent identity headers.
- Restricted knowledge never leaves the API without membership: locked results carry title, project and owner only.
- Only owners can approve; only members can write to a project.
- Capture prompt instructs the model to drop secrets and personal data; humans can edit or delete any entry.
- Keyless service-to-service access (managed identity); no secrets in app settings.
- Plans cite the lessons they are built from, and stale advice is flagged by user votes.

## 7. Roadmap

- Azure AI Search index with per-project security filters (replace LLM ranking past ~1k entries)
- Teams / email notification to owners via Logic Apps; time-boxed access grants
- Copilot Studio agent so Microsoft 365 Copilot can answer from Recall
- Entra ID groups as project members; Microsoft Purview sensitivity labels on entries
- Automatic capture from GitHub pull requests and Azure DevOps work items

## 8. Demo

The demo video (under 7 minutes, with voice-over) was submitted with the hackathon form. Story: Priya's team solved a SQL analytics agent for Contoso; Maria's team gets a similar request from a new client. Maria discovers the locked project, requests access, Priya approves, and Maria's coding agent gets the full plan.

Full document with diagrams and screenshots: [Azure-Recall-Solution.pdf](Azure-Recall-Solution.pdf).
