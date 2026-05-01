import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

_oauth_token_cache: dict = {"token": None, "expires_at": 0.0}
_cloud_id_cache: str | None = None

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


async def _fetch_oauth_token() -> str:
    now = time.time()
    if _oauth_token_cache["token"] and _oauth_token_cache["expires_at"] > now + 60:
        return _oauth_token_cache["token"]
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://auth.atlassian.com/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": os.environ["JIRA_CLIENT_ID"],
                "client_secret": os.environ["JIRA_CLIENT_SECRET"],
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    _oauth_token_cache["token"] = data["access_token"]
    _oauth_token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _oauth_token_cache["token"]


async def _get_auth_headers() -> dict[str, str]:
    if os.environ.get("JIRA_CLIENT_ID") and os.environ.get("JIRA_CLIENT_SECRET"):
        return {"Authorization": f"Bearer {await _fetch_oauth_token()}"}
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


async def _api_base() -> str:
    """Return the REST API base URL. OAuth requires api.atlassian.com/ex/jira/<cloudId>."""
    if not (os.environ.get("JIRA_CLIENT_ID") and os.environ.get("JIRA_CLIENT_SECRET")):
        return _base_url()
    global _cloud_id_cache
    if _cloud_id_cache:
        return f"https://api.atlassian.com/ex/jira/{_cloud_id_cache}"
    token = await _fetch_oauth_token()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.atlassian.com/oauth/token/accessible-resources",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        r.raise_for_status()
        resources = r.json()
    configured = _base_url().rstrip("/")
    matched = next((res for res in resources if res.get("url", "").rstrip("/") == configured), None)
    resource = matched or resources[0]
    _cloud_id_cache = resource["id"]
    return f"https://api.atlassian.com/ex/jira/{_cloud_id_cache}"


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

    url = f"{await _api_base()}/rest/api/3/search/jql"
    payload = {
        "jql": jql,
        "maxResults": max_results,
        "fields": ["summary", "status", "assignee", "priority", "issuetype", "updated"],
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=await _get_auth_headers(), json=payload, timeout=15)
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
    url = f"{await _api_base()}/rest/api/3/issue/bulkfetch"
    payload = {
        "issueIdsOrKeys": [issue_key],
        "fields": ["summary", "status", "assignee", "priority", "issuetype", "description", "comment", "updated", "created"],
        "fieldsByKeys": False,
        "properties": [],
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=await _get_auth_headers(), json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

    issues = data.get("issues", [])
    if not issues:
        raise ValueError(f"Issue {issue_key} not found")
    item = issues[0]
    fields = item["fields"]
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
        "key": item["key"],
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
        "url": f"{_base_url()}/browse/{item['key']}",
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


def _text_to_adf(text: str) -> dict:
    """Convert plain text to Atlassian Document Format (ADF)."""
    raw_paragraphs = text.split("\n\n") if "\n\n" in text else [text]
    content = []
    for para in raw_paragraphs:
        if not para.strip():
            continue
        inline: list[dict] = []
        lines = para.split("\n")
        for i, line in enumerate(lines):
            if line:
                inline.append({"type": "text", "text": line})
            if i < len(lines) - 1:
                inline.append({"type": "hardBreak"})
        if inline:
            content.append({"type": "paragraph", "content": inline})
    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]
    return {"version": 1, "type": "doc", "content": content}


async def create_issue(
    summary: str,
    issue_type: str,
    description: str | None = None,
    labels: list[str] | None = None,
    assignee_account_id: str | None = None,
    priority: str | None = None,
    parent_key: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "project": {"key": _project_key()},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }
    if description:
        fields["description"] = _text_to_adf(description)
    if labels:
        fields["labels"] = labels
    if assignee_account_id:
        fields["assignee"] = {"id": assignee_account_id}
    if priority:
        fields["priority"] = {"name": priority}
    if parent_key:
        fields["parent"] = {"key": parent_key}

    url = f"{await _api_base()}/rest/api/3/issue/bulk"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=await _get_auth_headers(), json={"issueUpdates": [{"fields": fields}]}, timeout=15
        )
        response.raise_for_status()
        data = response.json()

    created = data.get("issues", [{}])[0]
    return {
        "key": created["key"],
        "url": f"{_base_url()}/browse/{created['key']}",
    }


async def update_issue(
    issue_key: str,
    summary: str | None = None,
    description: str | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
    assignee_account_id: str | None = None,
    priority: str | None = None,
) -> None:
    fields: dict[str, Any] = {}
    update: dict[str, Any] = {}

    if summary is not None:
        fields["summary"] = summary
    if description is not None:
        fields["description"] = _text_to_adf(description)
    if assignee_account_id is not None:
        fields["assignee"] = {"id": assignee_account_id}
    if priority is not None:
        fields["priority"] = {"name": priority}

    label_ops = [{"add": lb} for lb in (add_labels or [])] + [{"remove": lb} for lb in (remove_labels or [])]
    if label_ops:
        update["labels"] = label_ops

    body: dict[str, Any] = {}
    if fields:
        body["fields"] = fields
    if update:
        body["update"] = update

    url = f"{await _api_base()}/rest/api/3/issue/{issue_key}"
    async with httpx.AsyncClient() as client:
        response = await client.put(url, headers=await _get_auth_headers(), json=body, timeout=15)
        response.raise_for_status()


async def get_transitions(issue_key: str) -> list[dict[str, Any]]:
    url = f"{await _api_base()}/rest/api/3/issue/{issue_key}/transitions"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=await _get_auth_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
    return data.get("transitions", [])


async def transition_issue(issue_key: str, status_name: str) -> str:
    transitions = await get_transitions(issue_key)
    matched = next(
        (t for t in transitions if t["to"]["name"].lower() == status_name.lower()),
        None,
    )
    if not matched:
        available = [t["to"]["name"] for t in transitions]
        raise ValueError(f"No transition to '{status_name}' found. Available statuses: {available}")

    url = f"{await _api_base()}/rest/api/3/issue/{issue_key}/transitions"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=await _get_auth_headers(), json={"transition": {"id": matched["id"]}}, timeout=15
        )
        response.raise_for_status()

    return matched["to"]["name"]


async def add_comment(issue_key: str, comment_text: str) -> dict[str, Any]:
    url = f"{await _api_base()}/rest/api/3/issue/{issue_key}/comment"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=await _get_auth_headers(), json={"body": _text_to_adf(comment_text)}, timeout=15
        )
        response.raise_for_status()
        data = response.json()

    created_iso = data.get("created", "")
    return {
        "comment_id": data["id"],
        "created_iso": created_iso,
        "created_ts": _iso_to_unix(created_iso),
    }


async def update_comment(issue_key: str, comment_id: str, comment_text: str) -> None:
    url = f"{await _api_base()}/rest/api/3/issue/{issue_key}/comment/{comment_id}"
    async with httpx.AsyncClient() as client:
        response = await client.put(
            url, headers=await _get_auth_headers(), json={"body": _text_to_adf(comment_text)}, timeout=15
        )
        response.raise_for_status()
