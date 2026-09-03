"""Page parameters and a paged response.

From `corpocoin`, the only live implementation. Generic and typed already; what
it gained here is bounds that are enforced rather than documented, and a total
that cannot disagree with the page it describes.
"""

from __future__ import annotations

from typing import Generic, Sequence, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class PaginationParams(BaseModel):
    """What the caller asked for, clamped.

    ``le=MAX_PAGE_SIZE`` rather than silently truncating: a caller asking for
    a thousand rows has made a request the API will not serve, and answering
    with a hundred while saying nothing teaches them the limit does not exist.
    """

    page: int = Field(default=1, ge=1)
    size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    @property
    def limit(self) -> int:
        return self.size


class Page(BaseModel, Generic[T]):
    """One page, and enough to ask for the next.

    ``pages`` is derived rather than supplied, so it cannot disagree with
    ``total`` and ``size`` — which is the field a caller renders and therefore
    the one worth not letting drift.
    """

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1)

    @property
    def pages(self) -> int:
        if self.total == 0:
            return 0
        return -(-self.total // self.size)

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def has_previous(self) -> bool:
        return self.page > 1


def paginate(items: Sequence[T], total: int, params: PaginationParams) -> Page[T]:
    """Wrap a page of already-fetched rows.

    ``total`` is passed rather than taken from ``len(items)``: the point of a
    page is that it is shorter than the whole, so inferring the total from the
    slice would report the page size as the collection size.
    """
    return Page[T](items=list(items), total=total, page=params.page, size=params.size)
