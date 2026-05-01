import json
import os
import sys
import time
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


# --- _get_auth_headers / _fetch_oauth_token ---

@pytest.mark.asyncio
async def test_get_auth_headers_uses_basic_when_no_client_creds(monkeypatch):
    monkeypatch.delenv("JIRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("JIRA_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "mytoken")
    headers = await jira_client._get_auth_headers()
    assert headers["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_get_auth_headers_uses_bearer_when_client_creds_set(monkeypatch):
    monkeypatch.setenv("JIRA_CLIENT_ID", "cid")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(jira_client, "_fetch_oauth_token", AsyncMock(return_value="myoauthtoken"))
    headers = await jira_client._get_auth_headers()
    assert headers["Authorization"] == "Bearer myoauthtoken"


@pytest.mark.asyncio
async def test_fetch_oauth_token_calls_atlassian_and_caches(monkeypatch):
    monkeypatch.setenv("JIRA_CLIENT_ID", "cid")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "csecret")
    jira_client._oauth_token_cache["token"] = None
    jira_client._oauth_token_cache["expires_at"] = 0.0

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"access_token": "tok123", "expires_in": 3600}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        token = await jira_client._fetch_oauth_token()

    assert token == "tok123"
    assert jira_client._oauth_token_cache["token"] == "tok123"
    assert jira_client._oauth_token_cache["expires_at"] > time.time()
    url = mock_client.post.call_args.args[0]
    assert url == "https://auth.atlassian.com/oauth/token"
    body = mock_client.post.call_args.kwargs["json"]
    assert body["grant_type"] == "client_credentials"
    assert body["client_id"] == "cid"


@pytest.mark.asyncio
async def test_api_base_returns_base_url_for_basic_auth(monkeypatch):
    monkeypatch.delenv("JIRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("JIRA_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    result = await jira_client._api_base()
    assert result == "https://test.atlassian.net"


@pytest.mark.asyncio
async def test_api_base_returns_cloud_url_for_oauth(monkeypatch):
    monkeypatch.setenv("JIRA_CLIENT_ID", "cid")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    jira_client._cloud_id_cache = None

    monkeypatch.setattr(jira_client, "_fetch_oauth_token", AsyncMock(return_value="tok"))

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [{"id": "cloud-abc", "url": "https://test.atlassian.net"}]

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await jira_client._api_base()

    assert result == "https://api.atlassian.com/ex/jira/cloud-abc"
    assert jira_client._cloud_id_cache == "cloud-abc"


@pytest.mark.asyncio
async def test_api_base_uses_cached_cloud_id(monkeypatch):
    monkeypatch.setenv("JIRA_CLIENT_ID", "cid")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "csecret")
    jira_client._cloud_id_cache = "cached-cloud-id"

    mock_client = AsyncMock()
    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await jira_client._api_base()

    assert result == "https://api.atlassian.com/ex/jira/cached-cloud-id"
    mock_client.__aenter__.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_oauth_token_uses_cache_when_valid(monkeypatch):
    monkeypatch.setenv("JIRA_CLIENT_ID", "cid")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "csecret")
    jira_client._oauth_token_cache["token"] = "cached_token"
    jira_client._oauth_token_cache["expires_at"] = time.time() + 600

    mock_client = AsyncMock()
    with patch("httpx.AsyncClient", return_value=mock_client):
        token = await jira_client._fetch_oauth_token()

    assert token == "cached_token"
    mock_client.__aenter__.assert_not_called()


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
    mock_response.json.return_value = {"issues": [_make_detail_response("PROJ-5")]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        detail = await jira_client.get_issue_detail("PROJ-5")

    assert detail["key"] == "PROJ-5"
    assert detail["description"] == "Fix the login bug."
    assert len(detail["comments"]) == 1
    assert detail["comments"][0]["author"] == "Bob"
    assert detail["comments"][0]["body"] == "Reproduced."
    assert isinstance(detail["created_ts"], int)
    assert detail["url"] == "https://test.atlassian.net/browse/PROJ-5"
    body = mock_client.post.call_args.kwargs["json"]
    assert body["issueIdsOrKeys"] == ["PROJ-5"]


# --- _text_to_adf ---

def test_text_to_adf_single_paragraph():
    adf = jira_client._text_to_adf("Hello world")
    assert adf["type"] == "doc"
    assert adf["version"] == 1
    assert adf["content"][0]["type"] == "paragraph"
    assert adf["content"][0]["content"][0]["text"] == "Hello world"


def test_text_to_adf_double_newline_splits_paragraphs():
    adf = jira_client._text_to_adf("First para\n\nSecond para")
    assert len(adf["content"]) == 2
    assert adf["content"][0]["content"][0]["text"] == "First para"
    assert adf["content"][1]["content"][0]["text"] == "Second para"


def test_text_to_adf_single_newline_inserts_hard_break():
    adf = jira_client._text_to_adf("Line one\nLine two")
    inline = adf["content"][0]["content"]
    types = [n["type"] for n in inline]
    assert "hardBreak" in types
    texts = [n.get("text") for n in inline if n["type"] == "text"]
    assert "Line one" in texts
    assert "Line two" in texts


def test_text_to_adf_empty_string_returns_empty_paragraph():
    adf = jira_client._text_to_adf("")
    assert adf["content"][0]["type"] == "paragraph"


# --- create_issue ---

@pytest.mark.asyncio
async def test_create_issue_minimal(monkeypatch):
    monkeypatch.setattr(jira_client, "_find_project_config", lambda: {})
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")

    mock_response = MagicMock()
    mock_response.json.return_value = {"issues": [{"key": "PROJ-42", "id": "10042"}], "errors": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await jira_client.create_issue(summary="New task", issue_type="Task")

    assert result["key"] == "PROJ-42"
    assert result["url"] == "https://test.atlassian.net/browse/PROJ-42"
    body = mock_client.post.call_args.kwargs["json"]
    fields = body["issueUpdates"][0]["fields"]
    assert fields["summary"] == "New task"
    assert fields["issuetype"]["name"] == "Task"
    assert fields["project"]["key"] == "PROJ"


@pytest.mark.asyncio
async def test_create_issue_with_all_optional_fields(monkeypatch):
    monkeypatch.setattr(jira_client, "_find_project_config", lambda: {})
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")

    mock_response = MagicMock()
    mock_response.json.return_value = {"issues": [{"key": "PROJ-43", "id": "10043"}], "errors": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await jira_client.create_issue(
            summary="Child story",
            issue_type="Story",
            description="Some details",
            labels=["backend", "api"],
            assignee_account_id="abc123",
            priority="High",
            parent_key="PROJ-10",
        )

    assert result["key"] == "PROJ-43"
    body = mock_client.post.call_args.kwargs["json"]
    fields = body["issueUpdates"][0]["fields"]
    assert fields["labels"] == ["backend", "api"]
    assert fields["assignee"] == {"id": "abc123"}
    assert fields["priority"] == {"name": "High"}
    assert fields["parent"] == {"key": "PROJ-10"}
    assert fields["description"]["type"] == "doc"


# --- update_issue ---

@pytest.mark.asyncio
async def test_update_issue_summary_and_labels(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.status_code = 204

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.put = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await jira_client.update_issue(
            "PROJ-5",
            summary="Updated title",
            add_labels=["frontend"],
            remove_labels=["backend"],
        )

    body = mock_client.put.call_args.kwargs["json"]
    assert body["fields"]["summary"] == "Updated title"
    label_ops = body["update"]["labels"]
    assert {"add": "frontend"} in label_ops
    assert {"remove": "backend"} in label_ops


@pytest.mark.asyncio
async def test_update_issue_no_fields_sends_empty_body(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.put = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await jira_client.update_issue("PROJ-5")

    body = mock_client.put.call_args.kwargs["json"]
    assert body == {}


# --- transition_issue ---

@pytest.mark.asyncio
async def test_transition_issue_success(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")

    transitions_response = MagicMock()
    transitions_response.raise_for_status = MagicMock()
    transitions_response.json.return_value = {
        "transitions": [
            {"id": "11", "to": {"name": "In Progress"}},
            {"id": "21", "to": {"name": "Done"}},
        ]
    }
    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=transitions_response)
    mock_client.post = AsyncMock(return_value=post_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        new_status = await jira_client.transition_issue("PROJ-5", "in progress")

    assert new_status == "In Progress"
    post_body = mock_client.post.call_args.kwargs["json"]
    assert post_body["transition"]["id"] == "11"


@pytest.mark.asyncio
async def test_transition_issue_not_found_raises(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")

    transitions_response = MagicMock()
    transitions_response.raise_for_status = MagicMock()
    transitions_response.json.return_value = {
        "transitions": [{"id": "21", "to": {"name": "Done"}}]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=transitions_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="No transition to 'Nonexistent'"):
            await jira_client.transition_issue("PROJ-5", "Nonexistent")


# --- add_comment ---

@pytest.mark.asyncio
async def test_add_comment(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "id": "100001",
        "created": "2024-03-20T12:00:00.000+0000",
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await jira_client.add_comment("PROJ-5", "Looks good to me!")

    assert result["comment_id"] == "100001"
    assert result["created_iso"] == "2024-03-20T12:00:00.000+0000"
    assert isinstance(result["created_ts"], int)
    body = mock_client.post.call_args.kwargs["json"]
    assert body["body"]["type"] == "doc"


# --- update_comment ---

@pytest.mark.asyncio
async def test_update_comment(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "u@t.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.put = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await jira_client.update_comment("PROJ-5", "100001", "Revised comment text")

    url = mock_client.put.call_args.args[0]
    assert "comment/100001" in url
    body = mock_client.put.call_args.kwargs["json"]
    assert body["body"]["type"] == "doc"
