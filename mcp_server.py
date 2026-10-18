"""stdio MCP server (local use). Remote agents: use the hosted app's /mcp URL instead.
Register: claude mcp add azure-recall -- <path>/.venv/bin/python <path>/mcp_server.py"""
import app

if __name__ == "__main__":
    app.m.run()
