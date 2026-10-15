"""End-to-end pilot workflow: project -> import -> assign -> client fix -> retest -> close.

Everything goes through the real HTTP surface (JSON API, web forms, client
portal) as the three personas — consultancy admin, pentester, client user.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pilot_helpers import (
    FINDINGS_CSV,
    build_two_client_world,
    exposure_ids,
    logged_in,
)
from webhelpers import extract_csrf


def test_full_core_workflow(app: FastAPI, client: TestClient) -> None:
    world = build_two_client_world(app, client)
    project = world.project_a

    # 3. Owners and due dates (pentester, via the API).
    with logged_in(app, "pentester") as pentester:
        ids = exposure_ids(pentester, project)
        sqli = ids["SQL injection in search"]
        hsts = ids["Missing HSTS"]
        response = pentester.patch(
            f"/api/v1/exposures/{hsts}",
            params={"workspace_id": project},
            json={"owner": "Web team", "remediation_target_date": "2099-06-30"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["owner"] == "Web team"
        # The CSV owner/due date were applied on import.
        detail = pentester.get(f"/api/v1/exposures/{sqli}", params={"workspace_id": project})
        assert detail.json()["owner"] == "Dev team"
        assert detail.json()["remediation_target_date"] == "2020-01-01"
        assert detail.json()["affected_system"] == "app.alpha.example"
        pentester.post(
            f"/api/v1/exposures/{sqli}/comments",
            params={"workspace_id": project},
            json={"body": "Internal: payload in our notes.", "internal": True},
        )

    # 4. The client marks the fix and uploads evidence in the portal.
    with logged_in(app, "alpha-client") as alpha:
        home = alpha.get("/", follow_redirects=True)
        assert home.url.path == f"/portal/projects/{project}"
        assert "SQL injection in search" in home.text
        page = alpha.get(f"/portal/projects/{project}/findings/{sqli}")
        assert page.status_code == 200
        assert "Internal: payload" not in page.text
        token = extract_csrf(page.text)
        fixed = alpha.post(
            f"/portal/projects/{project}/findings/{sqli}/status",
            data={"csrf_token": token, "status": "fix_reported", "note": "Bound parameters now."},
        )
        assert fixed.status_code == 200, fixed.text
        assert "Fixed — awaiting retest" in fixed.text
        uploaded = alpha.post(
            f"/portal/projects/{project}/findings/{sqli}/attachments",
            data={"csrf_token": token},
            files={"file": ("proof.txt", b"query now uses ? placeholders\n", "text/plain")},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert "proof.txt" in uploaded.text

    # 5./6. The pentester retests: fail first (reopen), then pass (verified close).
    with logged_in(app, "pentester") as pentester:
        awaiting = pentester.get(
            "/api/v1/exposures", params={"workspace_id": project, "status": "fix_reported"}
        )
        assert [i["id"] for i in awaiting.json()["items"]] == [sqli]
        failed = pentester.post(
            f"/api/v1/exposures/{sqli}/retest",
            params={"workspace_id": project},
            json={"outcome": "failed", "notes": "Still injectable via the sort parameter."},
        )
        assert failed.status_code == 201
        state = pentester.get(f"/api/v1/exposures/{sqli}", params={"workspace_id": project})
        assert state.json()["status"] == "open"

    with logged_in(app, "alpha-client") as alpha:
        page = alpha.get(f"/portal/projects/{project}/findings/{sqli}")
        assert "Still injectable via the sort parameter." in page.text  # retest notes visible
        alpha.post(
            f"/api/v1/exposures/{sqli}/client-status",
            params={"workspace_id": project},
            json={"status": "fix_reported", "note": "Sort parameter fixed too."},
        )

    with logged_in(app, "pentester") as pentester:
        panel = pentester.get(f"/exposures/{sqli}/panel?workspace_id={project}&tab=retest")
        passed = pentester.post(
            f"/exposures/{sqli}/retest?workspace_id={project}",
            data={"csrf_token": extract_csrf(panel.text), "outcome": "passed", "notes": "Fixed."},
        )
        assert passed.status_code == 200
        assert "verified closed" in passed.text
        history = pentester.get(
            f"/api/v1/exposures/{sqli}/history", params={"workspace_id": project}
        ).json()
        events = [h["event_type"] for h in history]
        assert events.count("fix_reported") == 2
        assert "retest_failed" in events and "retest_passed" in events
        assert history[-1]["actor"] == "pentester"

    # 7. Overview and report agree.
    overview = client.get("/overview")
    assert overview.status_code == 200
    report = client.get(f"/report?workspace_id={project}")
    assert report.status_code == 200
    assert re.search(r"Verified closed</div><div class=\"value\">1<", report.text)
    with logged_in(app, "alpha-client") as alpha:
        client_report = alpha.get(f"/portal/projects/{project}/report")
        assert client_report.status_code == 200
        assert re.search(r"Verified closed</div><div class=\"value\">1<", client_report.text)


def test_manual_finding_entry_and_edit(client: TestClient) -> None:
    created_client = client.post("/api/v1/clients", json={"name": "Gamma (fictional)"}).json()
    project = client.post(
        f"/api/v1/clients/{created_client['id']}/projects", json={"name": "Gamma test"}
    ).json()["id"]
    page = client.get(f"/exposures/new?workspace_id={project}")
    response = client.post(
        f"/exposures/new?workspace_id={project}",
        data={
            "csrf_token": extract_csrf(page.text),
            "title": "Open redirect on /logout",
            "severity": "medium",
            "affected_system": "sso.gamma.example",
            "description": "next= accepts any URL",
            "remediation": "Allow-list redirect targets",
            "owner": "Identity team",
            "due_date": "2099-01-31",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    exposure_id = int(response.headers["location"].split("open=")[1])
    edited = client.patch(
        f"/api/v1/exposures/{exposure_id}",
        params={"workspace_id": project},
        json={"severity": "high", "title": "Open redirect on /logout and /login"},
    )
    assert edited.status_code == 200
    assert edited.json()["severity"] == "high"
    history = client.get(
        f"/api/v1/exposures/{exposure_id}/history", params={"workspace_id": project}
    ).json()
    details = [h for h in history if h["event_type"] == "details_changed"]
    assert {h["new_value"].split(":")[0] for h in details} == {"severity", "title"}
    assert history[0]["event_type"] == "created"

    bad = client.post(
        f"/exposures/new?workspace_id={project}",
        data={"csrf_token": extract_csrf(page.text), "title": "x", "severity": "nope"},
    )
    assert bad.status_code == 422


def test_queue_search_and_filters(client: TestClient) -> None:
    created_client = client.post("/api/v1/clients", json={"name": "Delta (fictional)"}).json()
    project = client.post(
        f"/api/v1/clients/{created_client['id']}/projects", json={"name": "Delta test"}
    ).json()["id"]
    client.post(
        "/api/v1/findings-csv",
        params={"workspace_id": project},
        files={"file": ("r.csv", FINDINGS_CSV.encode(), "text/csv")},
    )
    by_system = client.get(
        "/api/v1/exposures", params={"workspace_id": project, "q": "api.alpha"}
    ).json()
    assert [i["title"] for i in by_system["items"]] == ["Weak TLS ciphers"]
    overdue = client.get(
        "/api/v1/exposures", params={"workspace_id": project, "overdue": "true"}
    ).json()
    assert [i["title"] for i in overdue["items"]] == ["SQL injection in search"]
    web = client.get(f"/exposures?workspace_id={project}&overdue=true&q=SQL")
    assert "SQL injection in search" in web.text
    assert "Weak TLS ciphers" not in web.text


def test_panel_forms_target_their_own_project(client: TestClient) -> None:
    """Regression: with several projects, the detail-panel forms must act on
    the project being viewed, not whichever project a cookie last named."""
    ids = []
    for name in ("Proj One", "Proj Two"):
        created = client.post("/api/v1/clients", json={"name": f"{name} client"}).json()
        project = client.post(
            f"/api/v1/clients/{created['id']}/projects", json={"name": name}
        ).json()["id"]
        client.post(
            "/api/v1/findings-csv",
            params={"workspace_id": project},
            files={"file": ("r.csv", FINDINGS_CSV.encode(), "text/csv")},
        )
        ids.append(project)
    first, second = ids
    queue = client.get(f"/exposures?workspace_id={first}")
    assert client.cookies.get("threnda_workspace_id") == str(first)
    exposure = exposure_ids(client, second)["Missing HSTS"]
    panel = client.get(f"/exposures/{exposure}/panel?workspace_id={second}&tab=remediation")
    action = re.search(r'action="(/exposures/\d+\?workspace_id=\d+)"', panel.text)
    assert action is not None and action.group(1).endswith(f"workspace_id={second}")
    saved = client.post(
        action.group(1),
        data={"csrf_token": extract_csrf(queue.text), "owner": "Team Two", "tab": "remediation"},
    )
    assert saved.status_code == 200
    detail = client.get(f"/api/v1/exposures/{exposure}", params={"workspace_id": second})
    assert detail.json()["owner"] == "Team Two"


def test_portal_actions_redirect_so_reload_does_not_resubmit(
    app: FastAPI, client: TestClient
) -> None:
    world = build_two_client_world(app, client)
    project = world.project_a
    with logged_in(app, "alpha-client") as alpha:
        exposure = exposure_ids(alpha, project)["Missing HSTS"]
        page = alpha.get(f"/portal/projects/{project}/findings/{exposure}")
        response = alpha.post(
            f"/portal/projects/{project}/findings/{exposure}/attachments",
            data={"csrf_token": extract_csrf(page.text)},
            files={"file": ("proof.txt", b"done\n", "text/plain")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        landed = alpha.get(response.headers["location"])
        assert "Evidence uploaded." in landed.text
        assert (
            "?notice=<script>"
            not in alpha.get(f"/portal/projects/{project}/findings/{exposure}?notice=<script>").text
        )
