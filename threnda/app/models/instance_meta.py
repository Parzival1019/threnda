"""InstanceMeta — small key/value facts about this database itself.

Currently used for exactly one thing: the ``instance_kind = demo`` marker
written by ``threnda demo reset``. The demo reset refuses to touch any
database that does not carry this marker, so a misconfigured
``THRENDA_DEMO_MODE`` can never wipe a real customer database — see
docs/demo.md.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from threnda.app.db.base import Base


class InstanceMeta(Base):
    __tablename__ = "instance_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
