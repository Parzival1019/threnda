"""Shared response envelopes."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

ItemT = TypeVar("ItemT")


# PEP 695 type-parameter syntax is intentionally not used here: the
# TypeVar + Generic form is the widely-supported pattern for Pydantic v2
# generic models across current pydantic/mypy versions.
class Page(BaseModel, Generic[ItemT]):  # noqa: UP046
    """A single page of a limit/offset-paginated collection."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int
