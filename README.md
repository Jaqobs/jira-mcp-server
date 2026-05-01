# Jira MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that connects Claude Code to your Jira project. Exposes tools so Claude can browse tickets, create and edit issues, transition workflow status, and leave comments — without leaving the editor.

## Features

- List active issues (configurable statuses, e.g. To Do / In Progress)
- Fetch full issue detail including description and comments
- Create issues — Epics, Stories, Tasks, Bugs, Sub-tasks
- Edit issues — summary, description, labels (add/remove), assignee, priority
- Transition issues between workflow statuses (e.g. To Do → In Progress → Done)
- Add and update comments on any issue
- Generate Jira-prefixed commit messages (e.g. `PROJ-42: ...`)

## Requirements

- Python 3.11+
- A Jira Cloud account
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

Edit `.env` and choose one of the two authentication methods below.

#### Option A — OAuth 2.0 client credentials (recommended for service accounts)

Create an OAuth 2.0 app at [developer.atlassian.com](https://developer.atlassian.com/console/myapps/) and grant it the **classic** Jira scopes `read:jira-work` and `write:jira-work`. Atlassian is migrating to granular scopes but the Jira REST API v3 bulk endpoints require classic scopes.

| Variable | Description |
|---|---|
| `JIRA_BASE_URL` | Your Atlassian base URL, e.g. `https://acme.atlassian.net` |
| `JIRA_CLIENT_ID` | OAuth app client ID |
| `JIRA_CLIENT_SECRET` | OAuth app client secret |

The server fetches a Bearer token from `https://auth.atlassian.com/oauth/token` (1 hour TTL, auto-refreshed) and resolves your Cloud ID from `https://api.atlassian.com/oauth/token/accessible-resources`.

#### Option B — Basic auth (personal API token)

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

### Read

| Tool | Description |
|---|---|
| `jira_get_active_issues` | Lists all tickets in the configured active statuses |
| `jira_get_issue_detail` | Full detail (description + comments) for a single ticket |
| `jira_get_commit_prefix` | Returns `PROJ-123: ` prefix for use in commit messages |

### Write

| Tool | Key parameters | Description |
|---|---|---|
| `jira_create_issue` | `summary`, `issue_type` | Create an Epic, Story, Task, Bug, or Sub-task. Pass `parent_key` to nest a Story under an Epic. |
| `jira_update_issue` | `issue_key` | Edit summary, description, assignee, priority, or labels. Use `add_labels` / `remove_labels` to manage tags without replacing the full set. |
| `jira_transition_issue` | `issue_key`, `status_name` | Move a ticket to a new workflow status (e.g. `In Progress`, `In Review`, `Done`). Fetches available transitions automatically. |
| `jira_add_comment` | `issue_key`, `comment` | Post a new comment on a ticket. |
| `jira_update_comment` | `issue_key`, `comment_id`, `comment` | Replace the body of an existing comment. `comment_id` is shown in `jira_get_issue_detail` output. |

## Commit message convention

When finishing work on a ticket, Claude calls `jira_get_commit_prefix` and prepends the issue key to every commit message:

```
ACME-42: Add user authentication flow
```

## Project structure

```
├── server.py              # MCP server — registers tools
├── jira_client.py         # Async Jira REST API v3 client
├── .env.example           # Credential template
├── .jira.json.example     # Per-project config template
├── requirements.txt
└── tests/
    └── test_jira_client.py
```
