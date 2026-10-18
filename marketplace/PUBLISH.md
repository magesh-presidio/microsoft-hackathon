# Publish Azure Recall to Azure Marketplace

1. Build + push image (ACR must be reachable by customers, or use a public registry):
   `az acr build -r <acr> -t azure-recall:1.0 .`
   Then set `image` default in mainTemplate.json to `<acr>.azurecr.io/azure-recall:1.0`.
2. Package: `cd marketplace && zip ../app.zip mainTemplate.json createUiDefinition.json`
3. Partner Center -> Marketplace offers -> New offer -> **Azure Application** -> plan type **Solution template** (or Managed app).
   Upload `app.zip` on the Technical configuration tab, fill listing text/logo, submit for certification.
4. After install: share the `mcpUrl` output and your API key with the team (UI > Connect tab shows the ready-made commands).
5. Optional, to import Azure builds: give the site permission to scan (output `principalId`):
   `az role assignment create --assignee <principalId> --role Reader --scope /subscriptions/<sub-id>`

Test without Partner Center: `az deployment group create -g <rg> --template-file marketplace/mainTemplate.json --parameters appName=<unique> recallApiKey=<12+ char key>`
