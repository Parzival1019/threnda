"""The possible-duplicate review queue.

Every resolution goes through ``threnda.app.services.possible_duplicates``
— the same code ``/api/v1/possible-duplicates/{id}/merge`` and
``.../keep-separate`` use — so a ``POSSIBLE`` match is never auto-merged
from this UI either; see docs/deduplication.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.responses import RedirectResponse, Response

from threnda.app.api.deps import get_db
from threnda.app.models.enums import PossibleDuplicateStatus
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.possible_duplicates import (
    PossibleDuplicateAlreadyResolvedError,
    PossibleDuplicateNotFoundError,
    resolve_keep_separate,
    resolve_merge,
)
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import (
    build_shell_context,
    get_current_membership,
    require_analyst,
    templates,
)

router = APIRouter(prefix="/review", tags=["web-review"])


@router.get("")
def review_queue(
    request: Request,
    membership: WorkspaceMembership = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Response:
    workspace = membership.workspace
    candidates = (
        db.execute(
            select(PossibleDuplicate)
            .where(
                PossibleDuplicate.workspace_id == workspace.id,
                PossibleDuplicate.status == PossibleDuplicateStatus.PENDING,
            )
            .options(
                selectinload(PossibleDuplicate.finding),
                selectinload(PossibleDuplicate.candidate_exposure),
            )
            .order_by(PossibleDuplicate.id)
        )
        .scalars()
        .all()
    )

    token = get_csrf_token(request)
    context = {
        "active_nav": "review",
        "candidates": candidates,
        "csrf_token": token,
        **build_shell_context(request, db, workspace, membership),
    }
    response = templates.TemplateResponse(request, "review/list.html", context)
    apply_csrf_cookie(request, response, token)
    return response


@router.post("/{possible_duplicate_id}/merge")
def merge(
    request: Request,
    possible_duplicate_id: int,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    try:
        resolve_merge(
            db,
            workspace_id=workspace.id,
            possible_duplicate_id=possible_duplicate_id,
            actor=membership.user.username,
        )
    except PossibleDuplicateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Possible duplicate not found") from exc
    except PossibleDuplicateAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/review?workspace_id={workspace.id}", status_code=303)


@router.post("/{possible_duplicate_id}/keep-separate")
def keep_separate(
    request: Request,
    possible_duplicate_id: int,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    try:
        resolve_keep_separate(
            db,
            workspace_id=workspace.id,
            possible_duplicate_id=possible_duplicate_id,
            actor=membership.user.username,
        )
    except PossibleDuplicateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Possible duplicate not found") from exc
    except PossibleDuplicateAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/review?workspace_id={workspace.id}", status_code=303)
