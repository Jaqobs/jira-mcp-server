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

Edit `.env` and fill in your credentials. These are shared across all projects and should never be committed:

| Variable | Description |
|---|---|
| `JIRA_BASE_URL` | Your Atlassian base URL, e.g. `https://acme.atlassian.net` |
| `JIRA_EMAIL` | Your Atlassian account email |
| `JIRA_API_TOKEN` | API token from https://id.atlassian.com/manage-profile/security/api-tokens |

### 3. Add a .jira.json to each project

In the root of each project you want to connect to a Jira board, create a `.jira.json` file:

```json
{
  "project_key": "ACME"
}
```

The server walks up from the current working directory to the nearest `.git` root looking for this file, so it automatically picks up the right board for whichever project you are working in.

You can also override the default fetch settings per project:

```json
{
  "project_key": "ACME",
  "fetch_statuses": ["In Progress", "In Review"],
  "max_results": 25
}
```

Status names are case-sensitive and must match exactly what appears in Jira. If no `.jira.json` is found, the server falls back to the `JIRA_PROJECT_KEY` environment variable.

### 4. Configure global defaults (optional)

Edit `config.json` in the server directory to set the default statuses and result limit used when a project does not override them:

```json
{
  "fetch_statuses": ["To Do", "In Progress"],
  "max_results": 50
}
```

### 5. Register with Claude Code

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
├── server.py              # MCP server — registers tools
├── jira_client.py         # Async Jira REST API v3 client
├── config.json            # Global default statuses and result limit
├── .env.example           # Credential template
├── .jira.json.example     # Per-project config template
├── requirements.txt
└── tests/
    └── test_jira_client.py
```
