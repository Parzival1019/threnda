"""Admin Local AI settings page and one-click installation controls."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.services.ai.embedded import EmbeddedAIStatus

ROUTES = "threnda.app.web.routes.settings"


def _status(*, installed: bool = False, installing: bool = False) -> EmbeddedAIStatus:
    return EmbeddedAIStatus(
        supported=True,
        installed=installed,
        runtime_installed=installed,
        model_installed=installed,
        running=False,
        installing=installing,
        stage="ready" if installed else "not installed",
        error=None,
    )


def test_local_ai_page_explains_zero_configuration_privacy(client: TestClient) -> None:
    workspace_id = bootstrap_workspace(client)
    with patch(f"{ROUTES}.embedded_ai_status", return_value=_status()):
        response = client.get("/settings/ai", params={"workspace_id": workspace_id})

    assert response.status_code == 200
    assert "Private AI, built into Threnda" in response.text
    assert "No separate AI application" in response.text
    assert "Install Local AI" in response.text
    assert "localhost only" in response.text


def test_admin_can_start_one_click_local_ai_install(client: TestClient) -> None:
    workspace_id = bootstrap_workspace(client)
    with patch(f"{ROUTES}.embedded_ai_status", return_value=_status()):
        page = client.get("/settings/ai", params={"workspace_id": workspace_id})
    token = extract_csrf(page.text)

    with (
        patch(f"{ROUTES}.embedded_ai_status", return_value=_status()),
        patch(f"{ROUTES}.start_embedded_ai_install", return_value=True) as start,
    ):
        response = client.post(
            "/settings/ai/install",
            params={"workspace_id": workspace_id},
            data={"csrf_token": token},
        )

    assert response.status_code == 200
    assert "installation started" in response.text
    start.assert_called_once()


def test_local_ai_install_rejects_missing_csrf(client: TestClient) -> None:
    workspace_id = bootstrap_workspace(client)
    response = client.post("/settings/ai/install", params={"workspace_id": workspace_id})
    assert response.status_code == 422
