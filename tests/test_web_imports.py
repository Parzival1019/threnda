"""Tests for the import history list and the new-import wizard."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.models.finding import Finding
from threnda.app.models.source import Source

DEMO_DATA = Path(__file__).resolve().parents[1] / "demo-data"


def _create_source(client: TestClient, workspace_id: int, name: str, source_type: str) -> int:
    response = client.get("/sources")
    token = extract_csrf(response.text)
    response = client.post(
        "/sources",
        data={"csrf_token": token, "name": name, "source_type": source_type},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    match = re.search(rf"<option value=\"(\d+)\">{name}", client.get("/imports/new").text)
    assert match is not None
    return int(match.group(1))


def test_imports_list_shows_empty_state(client: TestClient) -> None:
    bootstrap_workspace(client)
    response = client.get("/imports")
    assert response.status_code == 200
    assert "No imports yet" in response.text


def test_new_import_form_prompts_to_add_a_source_when_none_exist(client: TestClient) -> None:
    bootstrap_workspace(client)
    response = client.get("/imports/new")
    assert response.status_code == 200
    assert "No compatible sources yet" in response.text


def test_json_upload_happy_path_lands_findings_in_the_queue(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    source_id = _create_source(client, workspace_id, "web-json", "json")

    form = client.get("/imports/new")
    token = extract_csrf(form.text)
    content = (DEMO_DATA / "json" / "synthetic-findings.json").read_bytes()

    response = client.post(
        "/imports/new",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("synthetic-findings.json", content, "application/json")},
    )
    assert response.status_code == 200
    assert "Import complete" in response.text

    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace_id)).all()
    assert len(findings) == 3

    queue = client.get("/exposures")
    assert queue.status_code == 200


def test_upload_result_carries_oob_updates_for_stale_sidebar_counts(
    client: TestClient, db_session: Session
) -> None:
    """Regression test: a real-browser smoke test found that after a
    successful htmx-driven import, the sidebar's "Imports" nav count and
    the "last import" footer line kept showing their pre-upload values
    (0 / "no imports yet") until the visitor navigated away and back —
    because the result fragment only ever replaced `#import-wizard`, never
    the sidebar/footer that live outside it. The result response must
    carry out-of-band swaps for those elements so the shell is immediately
    consistent with what it just did.
    """
    workspace_id = bootstrap_workspace(client)
    source_id = _create_source(client, workspace_id, "web-json-oob", "json")

    form = client.get("/imports/new")
    token = extract_csrf(form.text)
    content = (DEMO_DATA / "json" / "synthetic-findings.json").read_bytes()

    # Confirm the pre-upload state actually is "0 imports / no imports yet",
    # so this test would fail loudly if the seeded state ever changed.
    assert 'id="nav-import-count">0<' in form.text
    assert "no imports yet" in form.text

    response = client.post(
        "/imports/new",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("synthetic-findings.json", content, "application/json")},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.text

    assert 'id="nav-open-count" class="count" hx-swap-oob="true">3<' in body
    assert 'id="nav-import-count" class="count" hx-swap-oob="true">1<' in body
    assert 'id="nav-source-count" class="count" hx-swap-oob="true">1<' in body
    assert 'id="nav-review-count" class="count" hx-swap-oob="true">0<' in body

    footer_match = re.search(
        r'<div id="footer-meta" class="meta" hx-swap-oob="true">(.*?)</div>', body, re.DOTALL
    )
    assert footer_match is not None
    assert "no imports yet" not in footer_match.group(1)
    assert "last import" in footer_match.group(1)


def test_malformed_upload_shows_failure_without_writing_findings(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    source_id = _create_source(client, workspace_id, "web-broken", "json")

    form = client.get("/imports/new")
    token = extract_csrf(form.text)

    response = client.post(
        "/imports/new",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("broken.json", b"{not valid json", "application/json")},
    )
    assert response.status_code == 200
    assert "Import failed" in response.text

    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace_id)).all()
    assert findings == []


def test_csv_sniff_prefills_a_mapping_guess(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    source_id = _create_source(client, workspace_id, "web-csv", "csv")
    content = (DEMO_DATA / "csv" / "synthetic-vulnerabilities.csv").read_bytes()

    response = client.post(
        "/imports/sniff",
        data={"source_id": str(source_id)},
        files={"file": ("report.csv", content, "text/csv")},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Vulnerability" in response.text
    assert 'value="title" selected' in response.text


def test_scanner_csv_is_detected_and_imported_without_manual_mapping(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    source_id = _create_source(client, workspace_id, "web-tenable", "csv")
    content = (DEMO_DATA / "csv" / "scanners" / "tenable-vm.csv").read_bytes()

    sniff = client.post(
        "/imports/sniff",
        data={"source_id": str(source_id)},
        files={"file": ("tenable.csv", content, "text/csv")},
        headers={"HX-Request": "true"},
    )
    assert sniff.status_code == 200
    assert "Tenable Vulnerability Management" in sniff.text
    assert "mapping_column_" not in sniff.text

    form = client.get("/imports/new")
    token = extract_csrf(form.text)
    response = client.post(
        "/imports/new",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("tenable.csv", content, "text/csv")},
    )

    assert response.status_code == 200, response.text
    assert "Tenable Vulnerability Management" in response.text
    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace_id)).all()
    assert len(findings) == 1


def test_csv_upload_without_mapping_is_rejected(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    source_id = _create_source(client, workspace_id, "web-csv-2", "csv")
    content = (DEMO_DATA / "csv" / "synthetic-vulnerabilities.csv").read_bytes()

    form = client.get("/imports/new")
    token = extract_csrf(form.text)

    response = client.post(
        "/imports/new",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("report.csv", content, "text/csv")},
    )
    assert response.status_code == 422


def test_csv_upload_with_mapping_fields_persists_findings(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    source_id = _create_source(client, workspace_id, "web-csv-3", "csv")
    content = (DEMO_DATA / "csv" / "synthetic-vulnerabilities.csv").read_bytes()

    sniff = client.post(
        "/imports/sniff",
        data={"source_id": str(source_id)},
        files={"file": ("report.csv", content, "text/csv")},
        headers={"HX-Request": "true"},
    )
    columns = re.findall(r'name="mapping_column_(\d+)" value="([^"]+)"', sniff.text)

    form = client.get("/imports/new")
    token = extract_csrf(form.text)
    data = {"csrf_token": token, "source_id": str(source_id)}
    for index, name in columns:
        data[f"mapping_column_{index}"] = name
        block = re.search(rf'name="mapping_target_{index}".*?</select>', sniff.text, re.DOTALL)
        assert block is not None
        selected = re.search(r'<option value="([a-z]*)" selected', block.group(0))
        data[f"mapping_target_{index}"] = selected.group(1) if selected else ""

    response = client.post(
        "/imports/new",
        data=data,
        files={"file": ("report.csv", content, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert "Import complete" in response.text

    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace_id)).all()
    assert len(findings) == 5

    source = db_session.get(Source, source_id)
    assert source is not None
    assert source.meta["csv_mapping"]["Vulnerability"] == "title"


def test_import_detail_page_shows_counts(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    source_id = _create_source(client, workspace_id, "web-json-2", "json")

    form = client.get("/imports/new")
    token = extract_csrf(form.text)
    content = (DEMO_DATA / "json" / "synthetic-findings.json").read_bytes()
    client.post(
        "/imports/new",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("f.json", content, "application/json")},
    )

    listing = client.get("/imports")
    match = re.search(r"/imports/(\d+)\?", listing.text)
    assert match is not None
    run_id = match.group(1)

    response = client.get(f"/imports/{run_id}")
    assert response.status_code == 200
    assert "f.json" in response.text


def test_import_detail_404s_for_another_workspace(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client, name="ws-a")
    source_id = _create_source(client, workspace_id, "web-json-3", "json")
    form = client.get("/imports/new")
    token = extract_csrf(form.text)
    content = (DEMO_DATA / "json" / "synthetic-findings.json").read_bytes()
    client.post(
        "/imports/new",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("f.json", content, "application/json")},
    )
    listing = client.get("/imports")
    run_id = re.search(r"/imports/(\d+)\?", listing.text).group(1)  # type: ignore[union-attr]

    other_workspace_id = bootstrap_workspace(client, name="ws-b")
    response = client.get(f"/imports/{run_id}?workspace_id={other_workspace_id}")
    assert response.status_code == 404
