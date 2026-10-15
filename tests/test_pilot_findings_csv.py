"""The pentest findings CSV template: validation, error list, re-import rules."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from webhelpers import extract_csrf

from threnda.app.models.exposure import Exposure
from threnda.app.services.findings_csv import TEMPLATE_CSV, parse_template_csv

HEADER = "id,title,severity,affected_system,owner,due_date\n"


@pytest.fixture
def project(client: TestClient) -> int:
    created = client.post("/api/v1/clients", json={"name": "Zeta (fictional)"}).json()
    return int(
        client.post(f"/api/v1/clients/{created['id']}/projects", json={"name": "Zeta"}).json()["id"]
    )


def _import(client: TestClient, project: int, body: str) -> tuple[int, dict[str, Any]]:
    response = client.post(
        "/api/v1/findings-csv",
        params={"workspace_id": project},
        files={"file": ("findings.csv", body.encode(), "text/csv")},
    )
    return response.status_code, response.json()


def _count(db: Session, project: int) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(Exposure).where(Exposure.workspace_id == project)
        ).scalar_one()
    )


def test_template_is_downloadable_and_valid(client: TestClient) -> None:
    response = client.get("/findings-import/template.csv")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    rows, problems = parse_template_csv(TEMPLATE_CSV.encode())
    assert problems == [] and len(rows) == 2


def test_invalid_file_reports_every_problem_and_writes_nothing(
    client: TestClient, project: int, db_session: Session
) -> None:
    body = (
        HEADER
        + "A1,,high,h1.example,,\n"  # line 2: missing title
        + "A2,Thing,urgent,h1.example,,\n"  # line 3: bad severity
        + "A3,Thing 3,low,h1.example,,31/12/2026\n"  # line 4: bad date
        + "A4,Fine,low,h1.example,,\n"  # line 5: ok
        + "A4,Dup id,low,h1.example,,\n"  # line 6: duplicate id
    )
    status, result = _import(client, project, body)
    assert status == 422
    problems = {(p["line"], p["column"]) for p in result["problems"]}
    assert problems == {(2, "title"), (3, "severity"), (4, "due_date"), (6, "id")}
    assert result["ok"] is False
    assert _count(db_session, project) == 0


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("title,sevrity\nX,high\n", "Unknown column 'sevrity'"),
        ("title,owner\nX,someone\n", "Required column 'severity' is missing"),
        ("title,severity,title\nX,high,Y\n", "appears more than once"),
        ("", "empty"),
        ("title,severity\n", "no finding rows"),
        ("title,severity\nX,high,extra\n", "unquoted comma"),
    ],
)
def test_structural_problems(client: TestClient, project: int, body: str, expected: str) -> None:
    status, result = _import(client, project, body)
    assert status == 422
    assert any(expected in p["message"] for p in result["problems"])


def test_non_utf8_file_is_explained(client: TestClient, project: int) -> None:
    response = client.post(
        "/api/v1/findings-csv",
        params={"workspace_id": project},
        files={"file": ("f.csv", "title,severity\nÁrvíz,high\n".encode("latin-1"))},
    )
    assert response.status_code == 422
    assert "UTF-8" in response.json()["problems"][0]["message"]


def test_reimport_does_not_duplicate_and_never_closes(
    client: TestClient, project: int, db_session: Session
) -> None:
    v1 = (
        HEADER
        + "R1,Exposed admin panel,high,adm.zeta.example,Ops,2099-01-01\n"
        + "R2,Outdated jQuery,low,www.zeta.example,,\n"
        + ",No-id finding,medium,www.zeta.example,,\n"
    )
    status, first = _import(client, project, v1)
    assert status == 200 and first["new"] == 3
    status, again = _import(client, project, v1)
    assert status == 200
    assert (again["new"], again["unchanged"]) == (0, 3)
    assert _count(db_session, project) == 3

    ids = {
        i["title"]: i["id"]
        for i in client.get("/api/v1/exposures", params={"workspace_id": project}).json()["items"]
    }
    # Owner edited in Threnda; R2 marked fixed by the team.
    client.patch(
        f"/api/v1/exposures/{ids['Exposed admin panel']}",
        params={"workspace_id": project},
        json={"owner": "Security team"},
    )
    client.post(
        f"/api/v1/exposures/{ids['Outdated jQuery']}/client-status",
        params={"workspace_id": project},
        json={"status": "fix_reported"},
    )

    # v2: R1 retitled + re-severitied (same id), R2 gone, one new finding.
    v2 = (
        HEADER
        + "R1,Exposed admin panel (no MFA),critical,adm.zeta.example,Someone else,2098-01-01\n"
        + ",No-id finding,medium,www.zeta.example,,\n"
        + "R3,Clickjacking,low,www.zeta.example,,\n"
    )
    status, second = _import(client, project, v2)
    assert status == 200, second
    assert (second["new"], second["updated"], second["unchanged"]) == (1, 1, 1)
    assert second["not_in_this_import"] == 1
    assert _count(db_session, project) == 4  # no duplicate for R1

    r1 = client.get(
        f"/api/v1/exposures/{ids['Exposed admin panel']}", params={"workspace_id": project}
    ).json()
    assert r1["title"] == "Exposed admin panel (no MFA)"
    assert r1["severity"] == "critical"
    assert r1["owner"] == "Security team"  # Threnda-side edit kept
    assert r1["remediation_target_date"] == "2099-01-01"  # not overwritten

    # The finding missing from v2 was NOT closed: still awaiting retest.
    r2 = client.get(
        f"/api/v1/exposures/{ids['Outdated jQuery']}", params={"workspace_id": project}
    ).json()
    assert r2["status"] == "fix_reported"
    assert r2["resolved_at"] is None


def test_web_import_page_shows_errors_and_summary(client: TestClient, project: int) -> None:
    page = client.get(f"/findings-import?workspace_id={project}")
    assert page.status_code == 200
    token = extract_csrf(page.text)
    bad = client.post(
        f"/findings-import?workspace_id={project}",
        data={"csrf_token": token},
        files={"file": ("bad.csv", b"title,severity\nX,catastrophic\n", "text/csv")},
    )
    assert bad.status_code == 422
    assert "nothing was imported" in bad.text
    assert "catastrophic" in bad.text
    good = client.post(
        f"/findings-import?workspace_id={project}",
        data={"csrf_token": token},
        files={"file": ("good.csv", TEMPLATE_CSV.encode(), "text/csv")},
    )
    assert good.status_code == 200
    assert "2 new" in good.text


def test_client_and_viewer_cannot_import(client: TestClient, project: int) -> None:
    client.post(
        "/api/v1/members",
        params={"workspace_id": project},
        json={"username": "reader", "password": "Reader-Pass-1234!", "role": "viewer"},
    )
    with TestClient(client.app) as reader:
        reader.post(
            "/api/v1/auth/login", json={"username": "reader", "password": "Reader-Pass-1234!"}
        )
        status = reader.post(
            "/api/v1/findings-csv",
            params={"workspace_id": project},
            files={"file": ("f.csv", TEMPLATE_CSV.encode())},
        ).status_code
        assert status == 403
