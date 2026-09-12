"""Small shared helpers for the web-UI test modules.

Not itself a test module (no ``test_`` prefix) — pytest won't collect it.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def extract_csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match is not None, f"no csrf_token field found in response:\n{html[:500]}"
    return match.group(1)


def bootstrap_workspace(client: TestClient, name: str = "default") -> int:
    """Create a workspace through the real onboarding flow and return its id."""
    response = client.get("/workspaces/new")
    token = extract_csrf(response.text)
    response = client.post(
        "/workspaces/new", data={"name": name, "csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    workspace_id = response.cookies.get("threnda_workspace_id")
    assert workspace_id is not None
    return int(workspace_id)
