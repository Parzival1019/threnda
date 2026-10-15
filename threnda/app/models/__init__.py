"""SQLAlchemy ORM models for the Threnda domain.

Importing this module registers every mapped class on ``Base``'s registry.
Anything that configures mappers or runs Alembic autogeneration against
``Base.metadata`` (see ``migrations/env.py``) must import this module first,
otherwise relationships expressed as string/forward references will fail to
resolve.
"""

from __future__ import annotations

from threnda.app.models.asset import Asset
from threnda.app.models.attachment import Attachment
from threnda.app.models.audit_log import AuditLogEntry
from threnda.app.models.candidate_finding import CandidateFinding
from threnda.app.models.client import Client
from threnda.app.models.document import Document
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_comment import ExposureComment
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.finding import Finding
from threnda.app.models.import_run import ImportRun
from threnda.app.models.instance_meta import InstanceMeta
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.retest import Retest
from threnda.app.models.source import Source
from threnda.app.models.user import User
from threnda.app.models.user_invitation import UserInvitation
from threnda.app.models.user_session import UserSession
from threnda.app.models.webhook_endpoint import WebhookEndpoint
from threnda.app.models.workspace import Workspace
from threnda.app.models.workspace_membership import WorkspaceMembership

__all__ = [
    "Asset",
    "Attachment",
    "AuditLogEntry",
    "CandidateFinding",
    "Client",
    "Document",
    "Exposure",
    "ExposureComment",
    "ExposureHistory",
    "Finding",
    "ImportRun",
    "InstanceMeta",
    "PossibleDuplicate",
    "Retest",
    "Source",
    "User",
    "UserInvitation",
    "UserSession",
    "WebhookEndpoint",
    "Workspace",
    "WorkspaceMembership",
]
