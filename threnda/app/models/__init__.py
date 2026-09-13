"""SQLAlchemy ORM models for the Threnda domain.

Importing this module registers every mapped class on ``Base``'s registry.
Anything that configures mappers or runs Alembic autogeneration against
``Base.metadata`` (see ``migrations/env.py``) must import this module first,
otherwise relationships expressed as string/forward references will fail to
resolve.
"""

from __future__ import annotations

from threnda.app.models.asset import Asset
from threnda.app.models.candidate_finding import CandidateFinding
from threnda.app.models.document import Document
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.finding import Finding
from threnda.app.models.import_run import ImportRun
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace

__all__ = [
    "Asset",
    "CandidateFinding",
    "Document",
    "Exposure",
    "ExposureHistory",
    "Finding",
    "ImportRun",
    "PossibleDuplicate",
    "Source",
    "Workspace",
]
