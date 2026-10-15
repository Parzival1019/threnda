"""Shared setup for the pilot-workflow test modules (not collected by pytest).

``build_two_client_world`` creates, through the real HTTP API, a consultancy
with two clients and one project each, a pentester, and one client user per
client — the minimum needed to test both the core workflow and isolation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from conftest import DEFAULT_TEST_PASSWORD
from fastapi import FastAPI
from fastapi.testclient import TestClient
from webhelpers import extract_csrf

FINDINGS_CSV = (
    "id,title,severity,affected_system,description,remediation,owner,due_date\n"
    "PT-1,SQL injection in search,critical,app.alpha.example,Injectable parameter,"
    "Use bound parameters,Dev team,2020-01-01\n"
    "PT-2,Missing HSTS,low,app.alpha.example,No HSTS header,Send HSTS,,\n"
    "PT-3,Weak TLS ciphers,medium,api.alpha.example,CBC ciphers offered,Disable CBC,"
    "Ops,2099-01-01\n"
)


@dataclass
class World:
    app: FastAPI
    admin: TestClient
    project_a: int
    project_b: int
    client_a_id: int
    client_b_id: int


def login(app: FastAPI, username: str, password: str = DEFAULT_TEST_PASSWORD) -> TestClient:
    test_client = TestClient(app)
    response = test_client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return test_client


@contextmanager
def logged_in(
    app: FastAPI, username: str, password: str = DEFAULT_TEST_PASSWORD
) -> Iterator[TestClient]:
    test_client = login(app, username, password)
    try:
        yield test_client
    finally:
        test_client.close()


def invite_and_accept(admin: TestClient, project_id: int, username: str, role: str) -> None:
    response = admin.post(
        "/api/v1/invitations",
        params={"workspace_id": project_id},
        json={"username": username, "role": role},
    )
    assert response.status_code == 201, response.text
    path = response.json()["invite_url"].split("://", 1)[1].split("/", 1)[1]
    anon = TestClient(admin.app)
    form = anon.get(f"/{path}")
    assert form.status_code == 200, form.text

    accepted = anon.post(
        f"/{path}",
        data={
            "csrf_token": extract_csrf(form.text),
            "password": DEFAULT_TEST_PASSWORD,
            "password_confirm": DEFAULT_TEST_PASSWORD,
        },
        follow_redirects=False,
    )
    assert accepted.status_code == 303, accepted.text
    anon.close()


def build_two_client_world(app: FastAPI, admin: TestClient) -> World:
    client_a = admin.post("/api/v1/clients", json={"name": "Alpha Corp (fictional)"}).json()
    client_b = admin.post("/api/v1/clients", json={"name": "Beta Ltd (fictional)"}).json()
    project_a = admin.post(
        f"/api/v1/clients/{client_a['id']}/projects", json={"name": "Alpha web pentest"}
    ).json()
    project_b = admin.post(
        f"/api/v1/clients/{client_b['id']}/projects", json={"name": "Beta network test"}
    ).json()
    for project in (project_a, project_b):
        imported = admin.post(
            "/api/v1/findings-csv",
            params={"workspace_id": project["id"]},
            files={"file": ("report.csv", FINDINGS_CSV.encode(), "text/csv")},
        )
        assert imported.status_code == 200, imported.text

    invite_and_accept(admin, project_a["id"], "pentester", "analyst")
    admin.post(
        "/api/v1/members",
        params={"workspace_id": project_b["id"]},
        json={"username": "pentester", "role": "analyst"},
    )
    invite_and_accept(admin, project_a["id"], "alpha-client", "client")
    invite_and_accept(admin, project_b["id"], "beta-client", "client")
    return World(
        app=app,
        admin=admin,
        project_a=project_a["id"],
        project_b=project_b["id"],
        client_a_id=client_a["id"],
        client_b_id=client_b["id"],
    )


def exposure_ids(test_client: TestClient, project_id: int) -> dict[str, int]:
    """title -> exposure id, as seen by ``test_client``."""
    response = test_client.get("/api/v1/exposures", params={"workspace_id": project_id})
    assert response.status_code == 200, response.text
    return {item["title"]: item["id"] for item in response.json()["items"]}
