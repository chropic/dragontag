"""Queue page merge: legacy URLs redirect, queue requires auth."""
from fastapi.testclient import TestClient

from dragontag.app.main import app

client = TestClient(app, follow_redirects=False)


def test_jobs_redirects_to_queue():
    r = client.get("/jobs")
    assert r.status_code == 308
    assert r.headers["location"] == "/queue"


def test_jobs_redirect_preserves_page():
    r = client.get("/jobs", params={"page": 3})
    assert r.status_code == 308
    assert r.headers["location"] == "/queue?page=3"


def test_review_redirects_to_queue():
    r = client.get("/review")
    assert r.status_code == 308
    assert r.headers["location"] == "/queue"


def test_queue_is_auth_gated():
    r = client.get("/queue")
    # No password configured in the test env → bounced to /setup (303),
    # or /login when one is. Either way: not a 200 without a session.
    assert r.status_code == 303
    assert r.headers["location"] in ("/setup", "/login")


def test_job_detail_route_still_exists():
    # /jobs/{id} must NOT be swallowed by the /jobs redirect.
    r = client.get("/jobs/999999")
    assert r.status_code == 303  # auth bounce, not 308 redirect to /queue
