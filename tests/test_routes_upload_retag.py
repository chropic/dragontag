"""Upload + full-library-retag surface errors as on-page toasts, not raw JSON.

Auth is overridden so the route bodies run; the assertions target the toast
behavior (204 + HX-Trigger showToast) and the absence of a raw 422.
"""
import json

import pytest
from fastapi.testclient import TestClient

from dragontag.app.main import app, require_auth


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _toast(resp):
    trig = resp.headers.get("HX-Trigger")
    return json.loads(trig)["showToast"] if trig else None


def test_bulk_retag_empty_path_is_toast_not_422(client):
    r = client.post("/library/bulk-retag", data={"source_path": ""})
    assert r.status_code == 204                       # not a 422 validation page
    assert _toast(r)["level"] == "error"
    assert "folder path" in _toast(r)["message"].lower()


def test_bulk_retag_missing_field_does_not_422(client):
    # No source_path field at all — previously a hard 422 before the handler.
    r = client.post("/library/bulk-retag", data={})
    assert r.status_code == 204
    assert _toast(r)["level"] == "error"


def test_bulk_retag_valid_path_queues(client, tmp_path):
    r = client.post("/library/bulk-retag", data={"source_path": str(tmp_path)})
    assert r.status_code == 204
    assert _toast(r)["level"] == "success"


def test_upload_unsupported_file_is_error_toast(client):
    r = client.post("/upload", files={"files": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 204
    t = _toast(r)
    assert t["level"] == "error" and "Rejected" in t["message"]


def test_upload_empty_file_is_error_toast(client):
    r = client.post("/upload", files={"files": ("song.flac", b"", "audio/flac")})
    assert r.status_code == 204
    assert _toast(r)["level"] == "error"
