"""The Risk tab's per-asset edit action.

There is no standalone assets page in the web UI (Milestone 4 scope is the
scoring/lifecycle work, not asset management) — this exists purely so the
exposure detail panel's Risk tab can set the two manual scoring inputs
(``business_criticality``, ``internet_facing``) on an asset linked through
one of the exposure's findings, then show the recomputed score immediately.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.responses import Response

from threnda.app.api.deps import get_db
from threnda.app.models.asset import Asset
from threnda.app.models.enums import AssetCriticality
from threnda.app.models.workspace import Workspace
from threnda.app.services.assets import apply_asset_update
from threnda.app.web.csrf import verify_csrf
from threnda.app.web.deps import get_current_workspace
from threnda.app.web.routes.exposures import DetailTab, _get_exposure_or_404, _render_detail_panel

router = APIRouter(prefix="/assets", tags=["web-assets"])
_VALID_TABS = ("evidence", "identity", "timeline", "remediation", "risk")


@router.post("/{asset_id}")
def update_asset(
    request: Request,
    asset_id: int,
    exposure_id: int = Form(...),
    csrf_token: str = Form(...),
    internet_facing: str | None = Form(default=None),
    business_criticality: str | None = Form(default=None),
    tab: str = Form(default="risk"),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    asset = db.get(Asset, asset_id)
    if asset is None or asset.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Asset not found")
    exposure = _get_exposure_or_404(db, workspace.id, exposure_id)

    resolved_criticality: AssetCriticality | None = None
    if business_criticality:
        try:
            resolved_criticality = AssetCriticality(business_criticality)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"Unknown business_criticality {business_criticality!r}"
            ) from exc

    # The tri-state <select> (unknown / yes / no) posts "" for "leave
    # unknown" and "true"/"false" otherwise — both are real, explicit
    # choices, so both do set the field (unlike a plain checkbox, where
    # "absent" would be ambiguous with "explicitly unknown").
    resolved_internet_facing = internet_facing == "true" if internet_facing else None

    apply_asset_update(
        db,
        asset,
        internet_facing=resolved_internet_facing,
        internet_facing_set=internet_facing is not None,
        business_criticality=resolved_criticality,
        business_criticality_set=bool(business_criticality),
    )

    tab_value = cast(DetailTab, tab) if tab in _VALID_TABS else "risk"
    return _render_detail_panel(request, db, workspace, exposure, tab=tab_value, sync_row=True)
