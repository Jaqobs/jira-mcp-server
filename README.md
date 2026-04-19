# Jira MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that connects Claude Code to your Jira project. Exposes read-only Jira tools so Claude can browse active tickets and automatically include issue keys in commit messages.

## Features

- List active issues (configurable statuses, e.g. To Do / In Progress)
- Fetch full issue detail including description and comments
- Generate Jira-prefixed commit messages (e.g. `PROJ-42: ...`)

## Requirements

- Python 3.11+
- A Jira Cloud account with an API token
- [Claude Code](https://claude.ai/code)

## Setup

### 1. Install dependencies

```bash
cd /path/to/jira_mcp_server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create your .env file

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|---|---|
| `JIRA_BASE_URL` | Your Atlassian base URL, e.g. `https://acme.atlassian.net` |
| `JIRA_EMAIL` | Your Atlassian account email |
| `JIRA_API_TOKEN` | API token from https://id.atlassian.com/manage-profile/security/api-tokens |
| `JIRA_PROJECT_KEY` | Your Jira project key, e.g. `ACME` |

### 3. Configure which statuses to fetch

Edit `config.json` to match the column names on your Jira board:

```json
{
  "fetch_statuses": ["To Do", "In Progress"],
  "max_results": 50
}
```

Status names are case-sensitive and must match exactly what appears in Jira.

### 4. Register with Claude Code

```bash
claude mcp add jira \
  /path/to/jira_mcp_server/.venv/bin/python \
  /path/to/jira_mcp_server/server.py
```

Or manually add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "jira": {
      "command": "/path/to/jira_mcp_server/.venv/bin/python",
      "args": ["/path/to/jira_mcp_server/server.py"]
    }
  }
}
```

## Available tools

| Tool | Description |
|---|---|
| `jira_get_active_issues` | Lists all tickets in the configured active statuses |
| `jira_get_issue_detail` | Full detail (description + comments) for a single ticket |
| `jira_get_commit_prefix` | Returns `PROJ-123: ` prefix for use in commit messages |

## Commit message convention

When finishing work on a ticket, Claude calls `jira_get_commit_prefix` and prepends the issue key to every commit message:

```
ACME-42: Add user authentication flow
```

## Project structure

```
├── server.py          # MCP server — registers tools
├── jira_client.py     # Async Jira REST API v3 client
├── config.json        # Configurable statuses and result limit
├── .env.example       # Credential template
├── requirements.txt
└── tests/
    └── test_jira_client.py
```
