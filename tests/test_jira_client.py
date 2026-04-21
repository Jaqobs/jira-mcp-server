import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import jira_client


# --- helpers ---

def _make_issue(key="PROJ-1", status="In Progress", priority="Medium", assignee=None):
    return {
        "key": key,
        "fields": {
            "summary": f"Test issue {key}",
            "status": {"name": status},
            "issuetype": {"name": "Story"},
            "priority": {"name": priority} if priority else None,
            "assignee": {"displayName": assignee} if assignee else None,
            "updated": "2024-03-15T10:30:00.000+0000",
        },
    }


def _make_detail_response(key="PROJ-1"):
    return {
        "key": key,
        "fields": {
            "summary": "Detailed issue",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "assignee": {"displayName": "Alice"},
            "description": {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Fix the login bug."}]}
                ],
            },
            "comment": {
                "comments": [
                    {
                        "author": {"displayName": "Bob"},
                        "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Reproduced."}]}]},
                        "created": "2024-03-16T08:00:00.000+0000",
                    }
                ]
            },
            "created": "2024-03-10T09:00:00.000+0000",
            "updated": "2024-03-16T08:00:00.000+0000",
        },
    }


# --- _extract_text ---

def test_extract_text_plain_string():
    assert jira_client._extract_text("hello") == "hello"


def test_extract_text_none():
    assert jira_client._extract_text(None) == ""


def test_extract_text_adf():
    node = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Line one."}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Line two."}]},
        ],
    }
    result = jira_client._extract_text(node)
    assert "Line one." in result
    assert "Line two." in result


# --- _iso_to_unix ---

def test_iso_to_unix_valid():
    ts = jira_client._iso_to_unix("2024-03-15T10:30:00.000+0000")
    assert isinstance(ts, int)
    assert ts > 0


def test_iso_to_unix_empty():
    assert jira_client._iso_to_unix("") is None


def test_iso_to_unix_invalid():
    assert jira_client._iso_to_unix("not-a-date") is None


# --- get_active_issues ---

# --- _find_project_config ---

def test_find_project_config_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert jira_client._find_project_config() == {}


def test_find_project_config_loads_jira_json(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".jira.json").write_text(json.dumps({"project_key": "MYPROJ"}))
    monkeypatch.chdir(tmp_path)
    assert jira_client._find_project_config() == {"project_key": "MYPROJ"}


def test_find_project_config_walks_up_to_git_root(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".jira.json").write_text(json.dumps({"project_key": "ROOT"}))
    subdir = tmp_path / "src" / "feature"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    assert jira_client._find_project_config() == {"project_key": "ROOT"}


def test_find_project_config_stops_at_git_root(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / ".jira.json").write_text(json.dumps({"project_key": "PARENT"}))
    (child / ".git").mkdir()
    monkeypatch.chdir(child)
    assert jira_client._find_project_config() == {}


# --- _project_key ---

def test_project_key_from_jira_json(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".jira.json").write_text(json.dumps({"project_key": "BOARD"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JIRA_PROJECT_KEY", raising=False)
    assert jira_client._project_key() == "BOARD"


def test_project_key_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ENVPROJ")
    assert jira_client._project_key() == "ENVPROJ"


def test_project_key_jira_json_takes_precedence_over_env(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".jira.json").write_text(json.dumps({"project_key": "FROMFILE"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIRA_PROJECT_KEY", "FROMENV")
    assert jira_client._project_key() == "FROMFILE"


def test_project_key_raises_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JIRA_PROJECT_KEY", raising=False)
    with pytest.raises(KeyError):
        jira_client._project_key()


# --- get_active_issues ---

@pytest.mark.asyncio
async def test_get_active_issues_returns_formatted_issues(tmp_path, monkeypatch):
    config = {"fetch_statuses": ["To Do", "In Progress"], "max_results": 10}
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))
    monkeypatch.setattr(jira_client, "CONFIG_PATH", config_file)
    monkeypatch.setattr(jira_client, "_find_project_config", lambda: {})

    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token123")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")

    mock_response = MagicMock()
    mock_response.json.return_value = {"issues": [_make_issue("PROJ-1"), _make_issue("PROJ-2", status="To Do", assignee="Alice")]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        issues = await jira_client.get_active_issues()

    assert len(issues) == 2
    assert issues[0]["key"] == "PROJ-1"
    assert issues[0]["status"] == "In Progress"
    assert issues[0]["url"] == "https://test.atlassian.net/browse/PROJ-1"
    assert issues[1]["assignee"] == "Alice"
    assert isinstance(issues[0]["updated_ts"], int)


@pytest.mark.asyncio
async def test_get_active_issues_project_config_overrides_fetch_statuses(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"fetch_statuses": ["To Do", "In Progress"], "max_results": 50}))
    monkeypatch.setattr(jira_client, "CONFIG_PATH", config_file)
    monkeypatch.setattr(jira_client, "_find_project_config", lambda: {"project_key": "PROJ", "fetch_statuses": ["In Review"]})

    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")

    mock_response = MagicMock()
    mock_response.json.return_value = {"issues": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    captured = {}

    async def capture_post(url, **kwargs):
        captured["jql"] = kwargs.get("json", {}).get("jql", "")
        return mock_response

    mock_client.post = capture_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        await jira_client.get_active_issues()

    assert '"In Review"' in captured["jql"]
    assert "In Progress" not in captured["jql"]


@pytest.mark.asyncio
async def test_get_active_issues_empty(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"fetch_statuses": ["To Do"], "max_results": 10}))
    monkeypatch.setattr(jira_client, "CONFIG_PATH", config_file)
    monkeypatch.setattr(jira_client, "_find_project_config", lambda: {})

    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")

    mock_response = MagicMock()
    mock_response.json.return_value = {"issues": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        issues = await jira_client.get_active_issues()

    assert issues == []


# --- get_issue_detail ---

@pytest.mark.asyncio
async def test_get_issue_detail(monkeypatch):
    monkeypatch.setattr(jira_client, "_find_project_config", lambda: {})
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")

    mock_response = MagicMock()
    mock_response.json.return_value = _make_detail_response("PROJ-5")
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        detail = await jira_client.get_issue_detail("PROJ-5")

    assert detail["key"] == "PROJ-5"
    assert detail["description"] == "Fix the login bug."
    assert len(detail["comments"]) == 1
    assert detail["comments"][0]["author"] == "Bob"
    assert detail["comments"][0]["body"] == "Reproduced."
    assert isinstance(detail["created_ts"], int)
    assert detail["url"] == "https://test.atlassian.net/browse/PROJ-5"
