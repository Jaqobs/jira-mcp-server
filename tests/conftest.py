import pytest
import jira_client


@pytest.fixture(autouse=True)
def reset_jira_client_caches(monkeypatch):
    monkeypatch.delenv("JIRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("JIRA_CLIENT_SECRET", raising=False)
    jira_client._oauth_token_cache["token"] = None
    jira_client._oauth_token_cache["expires_at"] = 0.0
    jira_client._cloud_id_cache = None
