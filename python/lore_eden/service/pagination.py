"""Page parameters and a paged response.

From `corpocoin`, then reconciled against loremaker's two — because "one shared
implementation" is only true if the alternatives were actually compared, and the
one already here won by being here rather than by being better.

## What the comparison found

The three modules turned out not to be three implementations of one thing:

- **This one** — page/size in, a typed ``Page[T]`` out. Framework-free.
- **loremaker's `models/api/pagination.py`** — the same question asked of a
  query string, plus **ordering** (`order_by`, `order_dir`) and an `abridged`
  flag. Ordering is a real gap here: almost no paged API is useful without it,
  and it has been merged in below.
- **loremaker's `apis/projections/pagination.py`** — next/previous *URL* links
  built with DRF helpers, wrapped in a DRF ``Response``. That is transport, not
  pagination, and it stays where it is: it cannot come here without bringing
  DRF, and a host on a different framework needs a different answer anyway.
  Recorded as a deliberate difference rather than merged.

## Why this one's shape won, on evidence rather than seniority

**Pages start at 1.** loremaker's start at 0, and its own back-navigation is
where that costs: `get_previous_link` special-cases `page_number == 1` and
reaches for `self.page_query_param`, an attribute the class never sets — so
paging back to the first page raises `AttributeError`. Off-by-one at the
boundary is the failure mode of 0-based paging, and that is what it looks like.

**`size` is what the caller asked for, not what came back.** loremaker's
response reports `page_size: len(self.data)`, which on a short final page tells
the client its page size shrank. A client deriving a page count from it gets a
different answer on the last page than on every other.

**A page count that cannot disagree with the total.** `pages` is derived here;
loremaker's `DataPage` carries `page_count` alongside `previous_token`,
`current_token` and `next_token`, none of which anything populates.
"""

from __future__ import annotations

from enum import Enum
from typing import Generic, Sequence, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class SortDirection(str, Enum):
    """Which way a sort runs.

    An enum because it is a closed set of two, and `order_dir="dsec"` typed as
    a string is a filter that silently sorts the other way.
    """

    ASCENDING = "asc"
    DESCENDING = "desc"


class PaginationParams(BaseModel):
    """What the caller asked for, clamped.

    ``le=MAX_PAGE_SIZE`` rather than silently truncating: a caller asking for
    a thousand rows has made a request the API will not serve, and answering
    with a hundred while saying nothing teaches them the limit does not exist.

    ``order_by`` is a bare field name because only the host knows which of its
    columns are sortable — validating it here would mean this module holding a
    list of every host's schema. ``order_dir`` is the opposite case: two values,
    the same everywhere, so it is an enum.
    """

    page: int = Field(default=1, ge=1)
    size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    order_by: str = ""
    order_dir: SortDirection = SortDirection.DESCENDING

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
