from fastapi.testclient import TestClient

from dragontag.app.main import app


def test_default_host_policy_allows_docker_proxy_host():
    """A bridge/proxy-provided host must not break normal Docker access."""
    response = TestClient(app).get("/health", headers={"host": "172.31.0.7:7593"})
    assert response.status_code == 200
