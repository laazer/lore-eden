"""Tests for the UTC boundary helpers.

The bug these prevent is silent — every timestamp reads plausibly and only a
viewer outside UTC sees the error — so the tests assert on the offset, not just
on the shape of the string.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from lore_eden.timestamps import as_utc, iso_utc, utcnow

EASTERN = timezone(timedelta(hours=-5))


class TestUtcnow:
    def test_it_is_aware(self) -> None:
        assert utcnow().tzinfo is not None

    def test_it_is_utc(self) -> None:
        assert utcnow().utcoffset() == timedelta(0)


class TestAsUtc:
    def test_a_naive_value_is_tagged_as_utc_without_shifting_the_clock(self) -> None:
        """Tagging, not converting: the wall-clock reading must not move."""
        naive = datetime(2026, 8, 8, 14, 19, 57, 465660)

        tagged = as_utc(naive)

        assert tagged.utcoffset() == timedelta(0)
        assert tagged.replace(tzinfo=None) == naive

    def test_an_offset_value_is_converted_and_the_instant_is_preserved(self) -> None:
        local = datetime(2026, 8, 8, 9, 19, 57, tzinfo=EASTERN)

        converted = as_utc(local)

        assert converted.utcoffset() == timedelta(0)
        assert converted == local
        assert converted.hour == 14

    def test_a_value_already_utc_is_unchanged(self) -> None:
        moment = datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)

        assert as_utc(moment) == moment
        assert as_utc(moment).replace(tzinfo=None) == moment.replace(tzinfo=None)

    def test_it_is_idempotent(self) -> None:
        naive = datetime(2026, 8, 8, 14, 19, 57)

        assert as_utc(as_utc(naive)) == as_utc(naive)


class TestIsoUtc:
    def test_a_naive_value_serializes_with_an_explicit_offset(self) -> None:
        """This is the whole point: no offset means a JS Date reads it as local."""
        rendered = iso_utc(datetime(2026, 8, 8, 14, 19, 57, 465660))

        assert rendered == "2026-08-08T14:19:57.465660+00:00"

    def test_the_rendering_is_never_offset_less(self) -> None:
        rendered = iso_utc(datetime(2026, 8, 8, 14, 19, 57))

        assert rendered is not None
        assert rendered.endswith("+00:00")

    def test_it_round_trips_back_to_the_same_instant(self) -> None:
        moment = datetime(2026, 8, 8, 14, 19, 57, tzinfo=timezone.utc)

        rendered = iso_utc(moment)

        assert rendered is not None
        assert datetime.fromisoformat(rendered) == moment

    def test_an_offset_value_round_trips_to_the_same_instant_too(self) -> None:
        local = datetime(2026, 8, 8, 9, 19, 57, tzinfo=EASTERN)

        rendered = iso_utc(local)

        assert rendered is not None
        assert datetime.fromisoformat(rendered) == local

    def test_none_stays_none_rather_than_becoming_a_string(self) -> None:
        """A missing timestamp must serialize as null, not as the epoch or "None"."""
        assert iso_utc(None) is None


def test_a_naive_and_an_offset_spelling_of_one_instant_serialize_identically() -> None:
    """The regression itself: these two must not disagree.

    A row read back from SQLite is naive; the same instant built in memory is
    aware. Before this boundary existed they rendered five hours apart.
    """
    naive_from_storage = datetime(2026, 8, 8, 14, 19, 57)
    aware_in_memory = datetime(2026, 8, 8, 9, 19, 57, tzinfo=EASTERN)

    assert iso_utc(naive_from_storage) == iso_utc(aware_in_memory)


@pytest.mark.parametrize("offset_hours", [-11, -5, 0, 5.5, 14])
def test_any_source_zone_lands_on_utc(offset_hours: float) -> None:
    source = datetime(2026, 8, 8, 12, 0, tzinfo=timezone(timedelta(hours=offset_hours)))

    assert as_utc(source).utcoffset() == timedelta(0)
