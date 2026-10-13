import base64
from uuid import uuid4

from .conftest import create_contract, login
from .test_organizations import browser, signup


def service(client):
    return client.get("/api/v1/intake/services").json()[0]


def submit(client, selected=None, **extra):
    selected = selected or service(client)
    answers = {
        q["key"]: (
            False if q["type"] == "boolean" else 100 if q["type"] == "number" else "Test context"
        )
        for q in selected["questions"]
    }
    return client.post(
        "/api/v1/intake/requests",
        json=dict(
            service_id=selected["id"],
            title="Legal request",
            description="Please review this agreement and confirm next steps.",
            answers=answers,
            submission_key=str(uuid4()),
            **extra,
        ),
    )


def test_requester_privacy_and_internal_notes(client, app):
    login(client, "viewer")
    result = submit(client)
    assert result.status_code == 201, result.text
    item = result.json()
    assert client.patch("/api/v1/intake/requests/" + item["id"], json={}).status_code == 403
    assert (
        client.post(
            f"/api/v1/intake/requests/{item['id']}/messages",
            json={"body": "secret", "internal": True},
        ).status_code
        == 403
    )
    login(client, "editor")
    assert client.get("/api/v1/intake/requests").json()["total"] == 0
    assert client.get("/api/v1/intake/metrics").json()["total"] == 0
    assert client.get("/api/v1/intake/requests/" + item["id"]).status_code == 404
    assert (
        client.post(
            f"/api/v1/intake/requests/{item['id']}/messages", json={"body": "foreign write"}
        ).status_code
        == 404
    )
    login(client, "reviewer")
    assert client.get("/api/v1/intake/requests").json()["total"] == 1
    assert (
        client.post(
            f"/api/v1/intake/requests/{item['id']}/messages",
            json={"body": "Privileged internal note", "internal": True},
        ).status_code
        == 201
    )
    assert "Privileged internal note" in client.get("/api/v1/intake/requests/" + item["id"]).text
    login(client, "viewer")
    assert (
        "Privileged internal note" not in client.get("/api/v1/intake/requests/" + item["id"]).text
    )
    with browser(app) as outsider:
        signup(outsider, "other-org@example.com")
        assert outsider.get("/api/v1/intake/requests/" + item["id"]).status_code == 404
        assert outsider.get("/api/v1/intake/requests").json()["total"] == 0
        assert submit(outsider, service(client)).status_code == 422


def test_service_routing_sla_snapshot_and_idempotence(client):
    admin = login(client)
    selected = service(client)
    body = {k: v for k, v in selected.items() if k != "id"}
    body.update(assignee_id=admin["id"], sla_hours=24)
    updated = client.put("/api/v1/intake/services/" + selected["id"], json=body)
    assert updated.status_code == 200, updated.text
    result = submit(client)
    assert result.status_code == 201, result.text
    item = result.json()
    assert item["assignee_id"] == admin["id"]
    from datetime import datetime, timedelta

    assert abs(
        (datetime.fromisoformat(item["due_at"]) - datetime.fromisoformat(item["created_at"]))
        - timedelta(hours=24)
    ) < timedelta(seconds=2)
    repeat = client.post(
        "/api/v1/intake/requests",
        json={
            k: item[k] for k in ["service_id", "title", "description", "answers", "submission_key"]
        },
    )
    assert repeat.status_code == 201 and repeat.json()["id"] == item["id"]
    body.update(revision=updated.json()["revision"], name="Changed name", questions=[])
    assert client.put("/api/v1/intake/services/" + selected["id"], json=body).status_code == 200
    stored = client.get("/api/v1/intake/requests/" + item["id"]).json()
    assert (
        stored["service_name"] == selected["name"]
        and stored["questions_snapshot"] == selected["questions"]
    )
    assert client.put("/api/v1/intake/services/" + selected["id"], json=body).status_code == 409


def test_triage_permissions_stale_revision_and_resolution(client, app):
    viewer = login(client, "viewer")
    item = submit(client).json()
    legal = login(client, "reviewer")
    url = "/api/v1/intake/requests/" + item["id"]
    body = {
        "revision": item["revision"],
        "status": "waiting",
        "assignee_id": legal["id"],
        "priority": "high",
        "resolution": "",
    }
    updated = client.patch(url, json=body)
    assert updated.status_code == 200, updated.text
    assert client.patch(url, json=body).status_code == 409
    login(client, "viewer")
    assert (
        client.post(url + "/messages", json={"body": "Here is the missing context."}).status_code
        == 201
    )
    assert client.get(url).json()["status"] == "new"
    login(client, "reviewer")
    current = client.get(url).json()
    body.update(revision=current["revision"], status="resolved")
    assert client.patch(url, json=body).status_code == 422
    body.update(resolution="Reviewed and answered.", assignee_id=viewer["id"])
    assert client.patch(url, json=body).status_code == 422
    with browser(app) as other:
        foreign = signup(other, "foreign-staff@example.com")["user"]
        body["assignee_id"] = foreign["id"]
        assert client.patch(url, json=body).status_code == 422
    body["assignee_id"] = legal["id"]
    assert client.patch(url, json=body).status_code == 200
    login(client, "viewer")
    assert client.get(url).json()["resolution"] == "Reviewed and answered."


def test_attachment_download_private_and_bounded(client, app):
    login(client, "viewer")
    item = submit(client).json()
    url = "/api/v1/intake/requests/" + item["id"]
    data = {
        "filename": "context.txt",
        "content_base64": base64.b64encode(b"Confidential attachment").decode(),
    }
    uploaded = client.post(url + "/attachments", json=data)
    assert uploaded.status_code == 201, uploaded.text
    download = url + "/attachments/" + uploaded.json()["id"]
    assert client.get(download).content == b"Confidential attachment"
    assert "attachment;" in client.get(download).headers["content-disposition"]
    assert "content_base64" not in client.get(url).text
    for invalid in [
        {"filename": "../secret.txt"},
        {"filename": "run.html"},
        {"content_base64": "!!!"},
        {"content_base64": base64.b64encode(b"x" * (1024 * 1024 + 1)).decode()},
    ]:
        assert client.post(url + "/attachments", json={**data, **invalid}).status_code == 422
    login(client, "editor")
    assert client.get(download).status_code == 404
    with browser(app) as other:
        signup(other, "file-foreign@example.com")
        assert other.get(download).status_code == 404
    login(client, "viewer")
    for _ in range(4):
        assert client.post(url + "/attachments", json=data).status_code == 201
    assert client.post(url + "/attachments", json=data).status_code == 409


def test_knowledge_and_contract_linking_are_organization_scoped(client, app):
    admin = login(client)
    article = client.post(
        "/api/v1/intake/articles",
        json={"title": "Approved process", "body": "Please use the approved request process."},
    ).json()
    contract = create_contract(client)
    item = submit(client).json()
    url = "/api/v1/intake/requests/" + item["id"] + "/contract"
    assert (
        client.put(
            url, json={"revision": item["revision"], "contract_id": contract["id"]}
        ).status_code
        == 200
    )
    login(client, "viewer")
    assert client.get("/api/v1/intake/articles").json() == []
    assert (
        client.post(
            "/api/v1/intake/articles", json={"title": "Bad", "body": "Not authorized"}
        ).status_code
        == 403
    )
    login(client)
    body = {k: article[k] for k in ["title", "body", "revision"]}
    body["published"] = True
    assert client.put("/api/v1/intake/articles/" + article["id"], json=body).status_code == 200
    login(client, "viewer")
    assert len(client.get("/api/v1/intake/articles").json()) == 1
    with browser(app) as other:
        signup(other, "article-foreign@example.com")
        assert other.get("/api/v1/intake/articles").json() == []
        own = submit(other).json()
        assert (
            other.put(
                "/api/v1/intake/requests/" + own["id"] + "/contract",
                json={"revision": own["revision"], "contract_id": contract["id"]},
            ).status_code
            == 404
        )
        assert other.put("/api/v1/intake/articles/" + article["id"], json=body).status_code == 404
    assert admin["role"] == "admin"


def test_invalid_answers_inactive_service_and_expired_deadline(client):
    login(client)
    selected = service(client)
    assert (
        client.post(
            "/api/v1/intake/requests",
            json={
                "service_id": selected["id"],
                "title": "Missing answers",
                "description": "Context is provided but answers are missing.",
                "submission_key": str(uuid4()),
                "answers": {},
            },
        ).status_code
        == 422
    )
    assert submit(client, requested_deadline="2000-01-01T00:00:00Z").status_code == 422
    body = {k: v for k, v in selected.items() if k != "id"}
    body["active"] = False
    assert client.put("/api/v1/intake/services/" + selected["id"], json=body).status_code == 200
    assert submit(client, selected).status_code == 422


def test_removing_legal_assignment_returns_open_work_to_triage(client):
    reviewer = login(client, "reviewer")
    login(client)
    selected = service(client)
    body = {k: v for k, v in selected.items() if k != "id"}
    body["assignee_id"] = reviewer["id"]
    assert client.put("/api/v1/intake/services/" + selected["id"], json=body).status_code == 200
    item = submit(client).json()
    assert item["assignee_id"] == reviewer["id"]
    assert (
        client.patch(
            "/api/v1/organizations/current/members/" + reviewer["id"], json={"role": "viewer"}
        ).status_code
        == 200
    )
    updated = client.get("/api/v1/intake/requests/" + item["id"]).json()
    assert updated["assignee_id"] is None and updated["status"] == "new"
    assert updated["revision"] > item["revision"]
    assert (
        next(s for s in client.get("/api/v1/intake/services").json() if s["id"] == selected["id"])[
            "assignee_id"
        ]
        is None
    )
