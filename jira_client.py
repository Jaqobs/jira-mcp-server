import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

def _find_project_config() -> dict:
    """Walk up from cwd looking for .jira.json, stopping at the git root."""
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / ".jira.json"
        if candidate.exists():
            with open(candidate) as f:
                return json.load(f)
        if (directory / ".git").exists():
            break
    return {}


def _auth_header() -> dict[str, str]:
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


def _base_url() -> str:
    return os.environ["JIRA_BASE_URL"].rstrip("/")


def _project_key() -> str:
    project_config = _find_project_config()
    if "project_key" in project_config:
        return project_config["project_key"]
    key = os.environ.get("JIRA_PROJECT_KEY")
    if not key:
        raise KeyError(
            "Jira project key not found. Add a .jira.json file to your project root "
            'with {"project_key": "PROJ"} or set JIRA_PROJECT_KEY in your .env file.'
        )
    return key


async def get_active_issues() -> list[dict[str, Any]]:
    project_config = _find_project_config()
    statuses = project_config.get("fetch_statuses", ["To Do", "In Progress"])
    max_results = project_config.get("max_results", 50)

    status_jql = " OR ".join(f'status = "{s}"' for s in statuses)
    jql = f'project = "{_project_key()}" AND ({status_jql}) ORDER BY updated DESC'

    url = f"{_base_url()}/rest/api/3/search/jql"
    payload = {
        "jql": jql,
        "maxResults": max_results,
        "fields": ["summary", "status", "assignee", "priority", "issuetype", "updated"],
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=_auth_header(), json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

    issues = []
    for item in data.get("issues", []):
        fields = item["fields"]
        updated_iso = fields.get("updated", "")
        issues.append({
            "key": item["key"],
            "summary": fields["summary"],
            "status": fields["status"]["name"],
            "issue_type": fields["issuetype"]["name"],
            "priority": fields["priority"]["name"] if fields.get("priority") else None,
            "assignee": fields["assignee"]["displayName"] if fields.get("assignee") else None,
            "updated_iso": updated_iso,
            "updated_ts": _iso_to_unix(updated_iso),
            "url": f"{_base_url()}/browse/{item['key']}",
        })

    return issues


async def get_issue_detail(issue_key: str) -> dict[str, Any]:
    url = f"{_base_url()}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,status,assignee,priority,issuetype,description,comment,updated,created"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=_auth_header(), params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

    fields = data["fields"]
    description = _extract_text(fields.get("description"))

    comments = []
    for c in (fields.get("comment") or {}).get("comments", []):
        created_iso = c.get("created", "")
        comments.append({
            "author": c["author"]["displayName"],
            "body": _extract_text(c.get("body")),
            "created_iso": created_iso,
            "created_ts": _iso_to_unix(created_iso),
        })

    created_iso = fields.get("created", "")
    updated_iso = fields.get("updated", "")

    return {
        "key": data["key"],
        "summary": fields["summary"],
        "status": fields["status"]["name"],
        "issue_type": fields["issuetype"]["name"],
        "priority": fields["priority"]["name"] if fields.get("priority") else None,
        "assignee": fields["assignee"]["displayName"] if fields.get("assignee") else None,
        "description": description,
        "comments": comments,
        "created_iso": created_iso,
        "created_ts": _iso_to_unix(created_iso),
        "updated_iso": updated_iso,
        "updated_ts": _iso_to_unix(updated_iso),
        "url": f"{_base_url()}/browse/{data['key']}",
    }


def _iso_to_unix(iso_str: str) -> int | None:
    if not iso_str:
        return None
    from datetime import datetime, timezone
    try:
        # Jira returns timestamps like 2024-01-15T10:30:00.000+0000
        dt = datetime.fromisoformat(iso_str.replace("+0000", "+00:00").replace(".000", ""))
        return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt.tzinfo is None else int(dt.timestamp())
    except ValueError:
        return None


def _extract_text(adf_node: Any) -> str:
    """Recursively extract plain text from Atlassian Document Format nodes."""
    if adf_node is None:
        return ""
    if isinstance(adf_node, str):
        return adf_node
    if isinstance(adf_node, dict):
        if adf_node.get("type") == "text":
            return adf_node.get("text", "")
        parts = []
        for child in adf_node.get("content", []):
            parts.append(_extract_text(child))
        return "\n".join(p for p in parts if p)
    return ""
