from fastapi.testclient import TestClient

from openclm.oauth import callback_url as docusign_callback
from openclm.salesforce import callback_url as salesforce_callback
from tests.conftest import PASSWORD


def test_subpath_assets_login_session_logout_and_callbacks(app):
    app.state.settings.base_path = "/openclm"
    app.root_path = "/openclm"
    with TestClient(
        app,
        base_url="https://testserver",
        headers={"X-OpenCLM": "1", "Origin": "https://testserver"},
    ) as client:
        page = client.get("/openclm/")
        assert page.status_code == 200
        assert 'src="/openclm/static/app.js"' in page.text
        assert client.get("/openclm/static/app.js").status_code == 200
        assert 'href="/openclm/openapi.json"' in client.get("/openclm/docs").text
        response = client.post(
            "/openclm/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200
        assert "Path=/openclm/" in response.headers["set-cookie"]
        assert client.get("/openclm/api/v1/auth/me").status_code == 200
        assert client.post("/openclm/api/v1/auth/logout").status_code == 204
        assert client.get("/openclm/api/v1/auth/me").status_code == 401
        assert (
            docusign_callback(app.state.settings)
            == "https://testserver/openclm/api/v1/integrations/docusign/callback"
        )
        assert (
            salesforce_callback(app.state.settings)
            == "https://testserver/openclm/api/v1/integrations/salesforce/callback"
        )


def test_tunnel_origin_redirect_and_proxy_auth_boundary(app):
    app.state.settings.base_path = "/openclm"
    app.state.settings.app_url = "https://legalops.dev"
    app.state.settings.proxy_hostname = "testserver"
    app.root_path = "/openclm"
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as client:
        response = client.get("/docs?test=1")
        assert response.status_code == 308
        assert response.headers["location"] == "https://legalops.dev/openclm/docs?test=1"
        assert client.get("/", headers={"X-OpenCLM-Proxy": "legalops.dev"}).status_code == 200
        assert (
            client.get("/api/v1/contracts", headers={"X-OpenCLM-Proxy": "legalops.dev"}).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                headers={
                    "X-OpenCLM-Proxy": "legalops.dev",
                    "X-OpenCLM": "1",
                    "Origin": "https://evil.example",
                },
                json={"email": "admin@example.com", "password": PASSWORD},
            ).status_code
            == 403
        )
