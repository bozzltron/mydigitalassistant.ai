"""Endpoint tests ensuring /files and /chat-ui return clean HTML."""
import pytest
from fastapi.testclient import TestClient
from assistant.backend.main import app


@pytest.fixture
def client():
    from assistant.backend.main import app
    return TestClient(app)


@pytest.mark.parametrize("path", ["/files", "/chat-ui", "/brain-ui"])
def test_endpoints_return_html(client, path):
    """Ensure key endpoints return valid HTML."""
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"
    assert "<html" in resp.text.lower(), f"{path} did not return HTML"


def test_files_no_broken_js(client):
    """Ensure /files response has no broken JS references."""
    resp = client.get("/files")
    assert resp.status_code == 200
    assert "initFileTab" not in resp.text, "/files should not contain initFileTab"
    assert "files-tab-btn" not in resp.text, "/files should not contain files-tab-btn"
