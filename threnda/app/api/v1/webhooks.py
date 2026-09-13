"""Outbound webhook configuration and manual delivery — admin only.

See ``threnda.app.services.webhooks`` for the delivery/SSRF-defense design
and docs/webhooks.md for the operator-facing contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db, require_admin
from threnda.app.models.webhook_endpoint import WebhookEndpoint
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.webhook import (
    WebhookCreate,
    WebhookCreated,
    WebhookEnabledUpdate,
    WebhookRead,
    WebhookTriggerResult,
)
from threnda.app.services.webhooks import (
    InvalidWebhookUrlError,
    create_webhook,
    delete_webhook,
    deliver_webhook,
    set_webhook_enabled,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _get_endpoint_or_404(db: Session, workspace_id: int, endpoint_id: int) -> WebhookEndpoint:
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found.")
    return endpoint


@router.get("", response_model=list[WebhookRead])
def list_webhooks(
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[WebhookRead]:
    rows = db.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.workspace_id == membership.workspace_id)
        .order_by(WebhookEndpoint.id)
    ).scalars()
    return [WebhookRead.model_validate(row) for row in rows]


@router.post("", response_model=WebhookCreated, status_code=201)
def create_webhook_endpoint(
    payload: WebhookCreate,
    request: Request,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> WebhookCreated:
    try:
        endpoint, secret = create_webhook(
            db,
            membership.workspace,
            url=payload.url,
            description=payload.description,
            settings=request.app.state.settings,
            actor=membership.user,
        )
    except InvalidWebhookUrlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return WebhookCreated(endpoint=WebhookRead.model_validate(endpoint), secret=secret)


@router.patch("/{endpoint_id}", response_model=WebhookRead)
def update_webhook_endpoint(
    endpoint_id: int,
    payload: WebhookEnabledUpdate,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> WebhookRead:
    endpoint = _get_endpoint_or_404(db, membership.workspace_id, endpoint_id)
    set_webhook_enabled(db, endpoint, payload.enabled, actor=membership.user)
    return WebhookRead.model_validate(endpoint)


@router.delete("/{endpoint_id}", status_code=204)
def delete_webhook_endpoint(
    endpoint_id: int,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    endpoint = _get_endpoint_or_404(db, membership.workspace_id, endpoint_id)
    delete_webhook(db, endpoint, actor=membership.user)


@router.post("/{endpoint_id}/trigger", response_model=WebhookTriggerResult)
def trigger_webhook_endpoint(
    endpoint_id: int,
    request: Request,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> WebhookTriggerResult:
    endpoint = _get_endpoint_or_404(db, membership.workspace_id, endpoint_id)
    if not endpoint.enabled:
        raise HTTPException(status_code=409, detail="Webhook endpoint is disabled.")
    try:
        result = deliver_webhook(
            db, endpoint, settings=request.app.state.settings, actor=membership.user
        )
    except InvalidWebhookUrlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return WebhookTriggerResult(ok=result.ok, status_code=result.status_code, error=result.error)
