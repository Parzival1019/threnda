"""Client isolation and closure permissions — server-side, over direct requests.

Every assertion here is made against the HTTP surface a malicious or curious
client user could reach directly (API, portal URLs, attachment downloads,
exports, consultancy pages), never against the UI's hiding of links.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pilot_helpers import World, build_two_client_world, exposure_ids, logged_in
from sqlalchemy import select
from sqlalchemy.orm import Session
from webhelpers import extract_csrf

from threnda.app.models.user import User


@pytest.fixture
def world(app: FastAPI, client: TestClient) -> World:
    return build_two_client_world(app, client)


def _first_id(test_client: TestClient, project: int) -> int:
    return next(iter(exposure_ids(test_client, project).values()))


def test_client_cannot_reach_another_clients_project(world: World) -> None:
    b_id = _first_id(world.admin, world.project_b)
    with logged_in(world.app, "alpha-client") as alpha:
        p = {"workspace_id": world.project_b}
        assert alpha.get("/api/v1/exposures", params=p).status_code == 404
        assert alpha.get(f"/api/v1/exposures/{b_id}", params=p).status_code == 404
        assert alpha.get(f"/api/v1/exposures/{b_id}/comments", params=p).status_code == 404
        assert alpha.get(f"/api/v1/exposures/{b_id}/attachments", params=p).status_code == 404
        assert alpha.get(f"/api/v1/exposures/{b_id}/history", params=p).status_code == 404
        assert (
            alpha.post(
                f"/api/v1/exposures/{b_id}/client-status",
                params=p,
                json={"status": "fix_reported"},
            ).status_code
            == 404
        )
        assert alpha.get(f"/portal/projects/{world.project_b}").status_code == 404
        assert alpha.get(f"/portal/projects/{world.project_b}/findings/{b_id}").status_code == 404
        assert alpha.get(f"/portal/projects/{world.project_b}/report").status_code == 404
        # IDOR: another project's exposure id through *their own* project.
        own = {"workspace_id": world.project_a}
        assert alpha.get(f"/api/v1/exposures/{b_id}", params=own).status_code == 404
        assert alpha.get(f"/portal/projects/{world.project_a}/findings/{b_id}").status_code == 404
        # Their own project works.
        assert alpha.get("/api/v1/exposures", params=own).status_code == 200


def test_client_is_kept_out_of_consultancy_surfaces(world: World) -> None:
    with logged_in(world.app, "alpha-client") as alpha:
        own = {"workspace_id": world.project_a}
        for path in (
            "/api/v1/imports",
            "/api/v1/sources",
            "/api/v1/assets",
            "/api/v1/documents",
            "/api/v1/possible-duplicates",
            "/api/v1/members",
            "/api/v1/invitations",
            "/api/v1/audit-log",
            "/api/v1/webhooks",
        ):
            status = alpha.get(path, params=own).status_code
            assert status in (403, 404), (path, status)
        export = alpha.get("/api/v1/exports/exposures", params={**own, "format": "csv"})
        assert export.status_code == 403
        assert alpha.get("/api/v1/clients").status_code == 403
        # Consultancy web pages bounce to the portal; nothing leaks.
        for path in ("/", "/exposures", "/imports", "/report", "/settings/members"):
            response = alpha.get(f"{path}?workspace_id={world.project_a}", follow_redirects=False)
            assert response.status_code in (302, 403), (path, response.status_code)
            if response.status_code == 302:
                assert response.headers["location"] == "/portal"
        assert alpha.get("/clients").status_code == 403


def test_client_never_sees_internal_content(world: World) -> None:
    project = world.project_a
    with logged_in(world.app, "pentester") as pentester:
        exposure = _first_id(pentester, project)
        p = {"workspace_id": project}
        pentester.post(
            f"/api/v1/exposures/{exposure}/comments",
            params=p,
            json={"body": "INTERNAL-ONLY note", "internal": True},
        )
        pentester.post(
            f"/api/v1/exposures/{exposure}/comments", params=p, json={"body": "Shared note"}
        )
        internal_file = pentester.post(
            f"/api/v1/exposures/{exposure}/attachments",
            params=p,
            files={"file": ("internal.txt", b"internal evidence", "text/plain")},
            data={"internal": "true"},
        ).json()
        pentester.patch(f"/api/v1/exposures/{exposure}", params=p, json={"note": "INTERNAL-NOTE-2"})

    with logged_in(world.app, "alpha-client") as alpha:
        p = {"workspace_id": project}
        comments = alpha.get(f"/api/v1/exposures/{exposure}/comments", params=p).json()
        assert [c["body"] for c in comments] == ["Shared note"]
        assert alpha.get(f"/api/v1/exposures/{exposure}/attachments", params=p).json() == []
        download = alpha.get(f"/api/v1/attachments/{internal_file['id']}/download", params=p)
        assert download.status_code == 404
        history = alpha.get(f"/api/v1/exposures/{exposure}/history", params=p).text
        assert "INTERNAL" not in history
        page = alpha.get(f"/portal/projects/{project}/findings/{exposure}").text
        assert "INTERNAL" not in page and "internal.txt" not in page
        # A client asking for an internal comment gets a normal one.
        made = alpha.post(
            f"/api/v1/exposures/{exposure}/comments",
            params=p,
            json={"body": "client says hi", "internal": True},
        ).json()
        assert made["is_internal"] is False


def test_closure_permissions(world: World, app: FastAPI) -> None:
    project = world.project_a
    exposure = _first_id(world.admin, project)
    p = {"workspace_id": project}
    with logged_in(app, "alpha-client") as alpha:
        assert (
            alpha.post(
                f"/api/v1/exposures/{exposure}/retest", params=p, json={"outcome": "passed"}
            ).status_code
            == 403
        )
        assert (
            alpha.patch(
                f"/api/v1/exposures/{exposure}", params=p, json={"status": "risk_accepted"}
            ).status_code
            == 403
        )
        for forbidden in ("resolved", "risk_accepted", "open"):
            response = alpha.post(
                f"/api/v1/exposures/{exposure}/client-status",
                params=p,
                json={"status": forbidden},
            )
            assert response.status_code == 422, (forbidden, response.text)
        page = alpha.get(f"/portal/projects/{project}/findings/{exposure}")
        response = alpha.post(
            f"/portal/projects/{project}/findings/{exposure}/status",
            data={"csrf_token": extract_csrf(page.text), "status": "resolved"},
        )
        assert response.status_code == 422
        state = alpha.get(f"/api/v1/exposures/{exposure}", params=p).json()
        assert state["status"] == "open"

    # A viewer (internal, read-only) cannot close either.
    world.admin.post(
        "/api/v1/members",
        params=p,
        json={"username": "auditor", "password": "Auditor-Pass-123!", "role": "viewer"},
    )
    with logged_in(app, "auditor", "Auditor-Pass-123!") as viewer:
        assert viewer.get(f"/api/v1/exposures/{exposure}", params=p).status_code == 200
        assert (
            viewer.post(
                f"/api/v1/exposures/{exposure}/retest", params=p, json={"outcome": "passed"}
            ).status_code
            == 403
        )

    # Even a pentester can't set "resolved" without a retest.
    with logged_in(app, "pentester") as pentester:
        response = pentester.patch(
            f"/api/v1/exposures/{exposure}", params=p, json={"status": "resolved"}
        )
        assert response.status_code == 409
        retest = pentester.post(
            f"/api/v1/exposures/{exposure}/retest", params=p, json={"outcome": "passed"}
        )
        assert retest.status_code == 201
        again = pentester.post(
            f"/api/v1/exposures/{exposure}/retest", params=p, json={"outcome": "passed"}
        )
        assert again.status_code == 409


def test_membership_binding_rules(world: World, db_session: Session) -> None:
    p_a = {"workspace_id": world.project_a}
    p_b = {"workspace_id": world.project_b}
    # Client account onto another client's project: refused.
    response = world.admin.post(
        "/api/v1/members", params=p_b, json={"username": "alpha-client", "role": "client"}
    )
    assert response.status_code == 422
    # Client account promoted to a staff role: refused.
    alpha = db_session.execute(select(User).where(User.username == "alpha-client")).scalar_one()
    response = world.admin.patch(f"/api/v1/members/{alpha.id}", params=p_a, json={"role": "viewer"})
    assert response.status_code == 422
    # Consultancy account given the client role: refused.
    response = world.admin.post(
        "/api/v1/members", params=p_b, json={"username": "admin", "role": "client"}
    )
    assert response.status_code in (409, 422)
    pentester = db_session.execute(select(User).where(User.username == "pentester")).scalar_one()
    response = world.admin.patch(
        f"/api/v1/members/{pentester.id}", params=p_a, json={"role": "client"}
    )
    assert response.status_code == 422


def test_only_org_admins_create_clients_and_projects(app: FastAPI, client: TestClient) -> None:
    created = client.post("/api/v1/clients", json={"name": "Epsilon (fictional)"}).json()
    project = client.post(
        f"/api/v1/clients/{created['id']}/projects", json={"name": "Epsilon test"}
    ).json()["id"]
    client.post(
        "/api/v1/members",
        params={"workspace_id": project},
        json={"username": "tester2", "password": "Tester2-Pass-123!", "role": "analyst"},
    )
    with logged_in(app, "tester2", "Tester2-Pass-123!") as tester:
        assert tester.post("/api/v1/clients", json={"name": "Nope"}).status_code == 403
        assert (
            tester.post(
                f"/api/v1/clients/{created['id']}/projects", json={"name": "Nope"}
            ).status_code
            == 403
        )
        assert tester.post("/api/v1/workspaces", json={"name": "Nope"}).status_code == 403
        assert tester.get("/clients").status_code == 403


def test_attachment_validation_and_download_headers(world: World) -> None:
    project = world.project_a
    p = {"workspace_id": project}
    with logged_in(world.app, "alpha-client") as alpha:
        exposure = _first_id(alpha, project)
        url = f"/api/v1/exposures/{exposure}/attachments"
        for name, body in (
            ("page.html", b"<script>alert(1)</script>"),
            ("logo.svg", b"<svg onload=alert(1)></svg>"),
            ("tool.exe", b"MZ\x90\x00"),
            ("fake.png", b"<html>not a png</html>"),
            ("empty.txt", b""),
            ("binary.txt", b"abc\x00def"),
        ):
            response = alpha.post(url, params=p, files={"file": (name, body)})
            assert response.status_code == 422, (name, response.text)
        too_big = b"a" * (world.app.state.settings.attachment_max_bytes + 1)
        assert alpha.post(url, params=p, files={"file": ("big.txt", too_big)}).status_code == 422
        ok = alpha.post(url, params=p, files={"file": ("../../etc/passwd proof.txt", b"fixed\n")})
        assert ok.status_code == 201, ok.text
        assert ok.json()["filename"] == "passwd proof.txt"
        download = alpha.get(f"/api/v1/attachments/{ok.json()['id']}/download", params=p)
        assert download.status_code == 200
        assert download.content == b"fixed\n"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["x-content-type-options"] == "nosniff"

    # The other client can't download it — not even knowing the id.
    with logged_in(world.app, "beta-client") as beta:
        response = beta.get(
            f"/api/v1/attachments/{ok.json()['id']}/download",
            params={"workspace_id": world.project_b},
        )
        assert response.status_code == 404
        response = beta.get(f"/api/v1/attachments/{ok.json()['id']}/download", params=p)
        assert response.status_code == 404


def test_unauthenticated_access_is_refused(app: FastAPI, world: World) -> None:
    with TestClient(app) as anon:
        p = {"workspace_id": world.project_a}
        assert anon.get("/api/v1/exposures", params=p).status_code == 401
        assert anon.get("/api/v1/attachments/1/download", params=p).status_code == 401
        response = anon.get(f"/portal/projects/{world.project_a}", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/login")
