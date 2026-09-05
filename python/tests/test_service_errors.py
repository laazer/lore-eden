"""Domain errors, their mapping, and pagination."""

from __future__ import annotations

# Pydantic's ValidationError is aliased throughout this file. `lore_eden.service`
# exports its own `ValidationError` — a DomainError mapped to 422 — and an
# unqualified import of pydantic's silently shadows it, which is how a test ends
# up asserting on a class the code under test never raises.
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lore_eden.service import (
    ConflictError,
    DomainError,
    NotFoundError,
    Page,
    PaginationParams,
    PermissionError_,
    RateLimitedError,
    UnauthenticatedError,
    UnavailableError,
    ValidationError,
    install_domain_error_handlers,
    paginate,
    register_status,
    status_for,
)
from pydantic import ValidationError as PydanticValidationError


class TestTheHierarchyKnowsNoHttp:
    def test_raising_one_needs_no_web_framework(self) -> None:
        # The whole reason bridgepath's design won over lllm-charge's: these
        # are raised from services, workers, CLIs and tests, none of which
        # should import a web framework to describe a refusal.
        import subprocess
        import sys

        script = (
            "from lore_eden.service.errors import NotFoundError, ValidationError;"
            "import sys;"
            "raised = [];"
            "\nfor kind in (NotFoundError, ValidationError):\n"
            "    try:\n"
            "        raise kind('no')\n"
            "    except kind as exc:\n"
            "        raised.append(exc.message)\n"
            "leaked = sorted(m for m in sys.modules if m.split('.')[0] in {'fastapi', 'starlette'});"
            "print(','.join(leaked) or 'clean')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        assert result.stdout.strip() == "clean", result.stdout

    def test_no_error_carries_a_status_code_attribute(self) -> None:
        # lllm-charge's version did. An exception with an HTTP status has an
        # opinion about a transport it should not know exists.
        for kind in (NotFoundError, ValidationError, ConflictError, DomainError):
            assert not hasattr(kind("x"), "status_code")

    def test_details_are_structured_rather_than_formatted_into_the_message(self) -> None:
        error = ValidationError("Bad field", {"field": "email"})
        assert error.message == "Bad field"
        assert error.details == {"field": "email"}

    def test_a_message_defaults_to_the_class_name(self) -> None:
        assert NotFoundError().message == "NotFoundError"


class TestStatusMapping:
    @pytest.mark.parametrize(
        ("kind", "status"),
        [
            (UnauthenticatedError, 401),
            (PermissionError_, 403),
            (NotFoundError, 404),
            (ConflictError, 409),
            (ValidationError, 422),
            (RateLimitedError, 429),
            (UnavailableError, 503),
            (DomainError, 500),
        ],
    )
    def test_each_type_maps(self, kind: type, status: int) -> None:
        assert status_for(kind("x")) == status

    def test_a_hosts_own_subclass_inherits_its_base_status(self) -> None:
        # The reason `status_for` walks the MRO. A flat dict of exact types
        # would send this to 500, which is the whole point of a hierarchy
        # silently failing.
        class DealNotFound(NotFoundError):
            pass

        assert status_for(DealNotFound("gone")) == 404

    def test_a_host_can_register_its_own(self) -> None:
        class Teapot(DomainError):
            pass

        register_status(Teapot, 418)
        assert status_for(Teapot("short and stout")) == 418

    def test_something_that_is_not_a_domain_error_is_a_500(self) -> None:
        assert status_for(RuntimeError("boom")) == 500


class TestTheHandlerNeedsNoTryExcept:
    @staticmethod
    def app() -> FastAPI:
        app = FastAPI()
        install_domain_error_handlers(app)

        @app.get("/missing")
        def missing() -> dict:
            # No try/except. That is the payoff.
            raise NotFoundError("No deal with that id", {"id": "d-1"})

        @app.get("/invalid")
        def invalid() -> dict:
            raise ValidationError("Bad field")

        @app.get("/subclass")
        def subclass() -> dict:
            class DealNotFound(NotFoundError):
                pass

            raise DealNotFound("gone")

        return app

    def test_a_domain_error_becomes_its_status(self) -> None:
        response = TestClient(self.app()).get("/missing")
        assert response.status_code == 404
        assert response.json() == {
            "detail": "No deal with that id",
            "details": {"id": "d-1"},
        }

    def test_details_are_omitted_when_empty(self) -> None:
        response = TestClient(self.app()).get("/invalid")
        assert response.status_code == 422
        assert response.json() == {"detail": "Bad field"}

    def test_a_hosts_subclass_is_handled_by_the_base_registration(self) -> None:
        # One handler on DomainError rather than one per subclass, so a type
        # this package never saw is still mapped.
        assert TestClient(self.app()).get("/subclass").status_code == 404


class TestPagination:
    def test_offset_and_limit_come_from_page_and_size(self) -> None:
        params = PaginationParams(page=3, size=20)
        assert (params.offset, params.limit) == (40, 20)

    def test_the_first_page_starts_at_zero(self) -> None:
        assert PaginationParams().offset == 0

    def test_a_page_below_one_is_refused_rather_than_clamped(self) -> None:
        with pytest.raises(PydanticValidationError):
            PaginationParams(page=0)

    def test_a_size_over_the_maximum_is_refused_rather_than_truncated(self) -> None:
        # Answering a request for a thousand rows with a hundred, silently,
        # teaches the caller the limit does not exist.
        with pytest.raises(PydanticValidationError):
            PaginationParams(size=1000)

    def test_pages_is_derived_so_it_cannot_disagree(self) -> None:
        assert paginate([1, 2], total=45, params=PaginationParams(size=20)).pages == 3
        assert paginate([], total=0, params=PaginationParams(size=20)).pages == 0
        assert paginate([1], total=20, params=PaginationParams(size=20)).pages == 1

    def test_total_is_the_collection_not_the_slice(self) -> None:
        # Inferring it from len(items) would report the page size as the
        # collection size, which is the one number a caller renders.
        page = paginate([1, 2, 3], total=99, params=PaginationParams(size=3))
        assert page.total == 99
        assert len(page.items) == 3

    def test_navigation_flags(self) -> None:
        middle = paginate([1], total=100, params=PaginationParams(page=3, size=20))
        assert middle.has_next and middle.has_previous
        last = paginate([1], total=100, params=PaginationParams(page=5, size=20))
        assert not last.has_next and last.has_previous
        only = paginate([1], total=5, params=PaginationParams(page=1, size=20))
        assert not only.has_next and not only.has_previous

    def test_it_is_generic(self) -> None:
        page: Page[str] = paginate(["a"], total=1, params=PaginationParams())
        assert page.items == ["a"]


class TestWhatTheReconciliationChanged:
    """The pagination modules loremaker carries were compared against this one,
    and these are the properties that decided it.

    Each test pins a behaviour the alternative got wrong, so "ours won" is a
    claim with something behind it rather than a preference for the incumbent.
    """

    def test_ordering_came_across_because_it_was_missing_here(self) -> None:
        """loremaker's query params carry `order_by`/`order_dir` and this module
        had neither. Almost no paged API is useful without them."""
        from lore_eden.service.pagination import SortDirection

        params = PaginationParams(order_by="updated_at", order_dir=SortDirection.ASCENDING)

        assert params.order_by == "updated_at"
        assert params.order_dir is SortDirection.ASCENDING

    def test_the_direction_default_matches_what_a_list_view_wants(self) -> None:
        assert PaginationParams().order_dir.value == "desc"

    def test_a_misspelled_direction_is_refused_rather_than_sorted_backwards(self) -> None:
        """Why the direction is an enum and the field name is not: two values,
        the same in every host, and `dsec` typed as a string sorts the other
        way in silence."""
        with pytest.raises(PydanticValidationError):
            PaginationParams(order_dir="dsec")

    def test_size_is_what_was_asked_for_not_what_came_back(self) -> None:
        """loremaker's response reports `page_size: len(self.data)`, so a short
        final page tells the client its page size shrank — and a client deriving
        a page count from it gets a different answer on the last page."""
        final = paginate(["only-one-left"], total=41, params=PaginationParams(page=3, size=20))

        assert final.size == 20
        assert len(final.items) == 1
        assert final.pages == 3

    def test_page_one_has_no_previous(self) -> None:
        """The boundary 0-based paging gets wrong. loremaker's own
        `get_previous_link` special-cases `page_number == 1` and reaches for an
        attribute the class never sets, so paging back to the first page raises
        AttributeError."""
        first = paginate([1], total=100, params=PaginationParams(page=1, size=20))

        assert first.has_previous is False
        assert first.page == 1
        assert PaginationParams(page=1).offset == 0

    def test_the_page_count_cannot_disagree_with_the_total(self) -> None:
        """Derived rather than carried. loremaker's `DataPage` carries
        `page_count` beside three token fields nothing populates."""
        page = paginate([], total=45, params=PaginationParams(page=1, size=20))

        assert page.pages == 3
