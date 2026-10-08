"""The extraction helpers are the layer that absorbs actor schema drift."""

from datetime import datetime, timezone

import pytest

from aljouf_report.collectors.extract import (
    extract_followers,
    find_follower_count,
    parse_count,
    parse_datetime,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (1234, 1234),
        ("1234", 1234),
        ("12,345", 12345),
        ("12.345", 12345),
        ("1.2M subscribers", 1_200_000),
        ("1,2M", 1_200_000),
        ("45K followers", 45_000),
        ("2B", 2_000_000_000),
        ({"count": 999}, 999),
        (None, None),
        ("", None),
        (True, None),
        ("no digits here", None),
    ],
)
def test_parse_count(value, expected):
    assert parse_count(value) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"followersCount": 5000},
        {"followers": "5,000"},
        {"follower_count": 5000},
        {"edge_followed_by": {"count": 5000}},
        {"legacy": {"followers_count": 5000}},
        {"stats": {"subscriberCount": 5000}},
        {"data": {"profile": {"numberOfSubscribers": "5000"}}},
        {"items": [{"subscribers": 5000}]},
    ],
)
def test_find_follower_count_survives_schema_drift(payload):
    assert find_follower_count(payload) == 5000


def test_extract_followers_prefers_declared_paths():
    item = {"followersCount": 100, "someOtherFollowers": 999}
    assert extract_followers(item, ("followersCount",)) == 100


def test_extract_followers_falls_back_to_search():
    # Preferred path is absent; the recursive search must still find the value.
    item = {"profile": {"subscriberCount": 777}}
    assert extract_followers(item, ("followersCount", "followers")) == 777


def test_extract_followers_returns_none_when_absent():
    assert extract_followers({"name": "x", "bio": "y"}, ("followersCount",)) is None


@pytest.mark.parametrize(
    "value",
    [
        "2026-03-01T12:30:00.000Z",
        "2026-03-01T12:30:00Z",
        "2026-03-01 12:30:00",
        1772368200,
        1772368200000,
        "20260301123000",
    ],
)
def test_parse_datetime_shapes(value):
    parsed = parse_datetime(value)
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_datetime_normalises_to_utc():
    parsed = parse_datetime("2026-03-01T12:00:00+03:00")
    assert parsed == datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def test_parse_datetime_rejects_garbage():
    assert parse_datetime("not a date") is None
    assert parse_datetime(None) is None
