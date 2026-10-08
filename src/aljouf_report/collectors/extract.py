"""Field extraction helpers for third-party scraper output.

Apify actors are community-maintained and their output schemas drift: a
follower count may arrive as `followersCount`, `followers`, `follower_count`,
`stats.followerCount`, or `legacy.followers_count` depending on the actor and
its version. Rather than hard-coding one shape per platform (which breaks
silently whenever an actor is updated or swapped), these helpers look up a list
of likely paths and then fall back to a recursive key-name search.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

# Keys whose name alone identifies a follower/subscriber total.
_FOLLOWER_KEY_RE = re.compile(
    r"^(followers?|followers?_?count|subscribers?|subscribers?_?count|"
    r"numberofsubscribers|follower_?total|edge_followed_by)$",
    re.IGNORECASE,
)

# "1.2M subscribers", "12,345 followers", "1,2 ألف"
_SUFFIX_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_count(value: Any) -> int | None:
    """Coerce a scraped follower value into an int.

    Handles raw ints, numeric strings with separators, and abbreviated forms
    such as "1.2M subscribers".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, dict):
        # Instagram's GraphQL shape: {"count": 1234}
        for key in ("count", "value", "total"):
            if key in value:
                return parse_count(value[key])
        return None
    if not isinstance(value, str):
        return None

    text = value.strip().replace("\u00a0", " ")
    if not text:
        return None

    match = re.search(r"(\d[\d.,\s]*)\s*([kKmMbB])?", text)
    if not match:
        return None

    number_text, suffix = match.group(1), match.group(2)
    number_text = number_text.strip().replace(" ", "")

    if suffix:
        # Abbreviated: the separator is a decimal point ("1.2M", "1,2M").
        normalised = number_text.replace(",", ".")
        try:
            return int(float(normalised) * _SUFFIX_MULTIPLIERS[suffix.lower()])
        except ValueError:
            return None

    # Not abbreviated: separators are thousands groupings.
    digits = re.sub(r"[.,]", "", number_text)
    return int(digits) if digits.isdigit() else None


def dig(data: Any, path: str) -> Any:
    """Read a dotted path, tolerating missing keys and list indices."""
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                return None
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def first_value(data: Any, paths: Sequence[str]) -> Any:
    """First non-empty value among `paths`."""
    for path in paths:
        value = dig(data, path)
        if value not in (None, "", [], {}):
            return value
    return None


def find_follower_count(data: Any, max_depth: int = 6) -> int | None:
    """Recursively search for any follower/subscriber-shaped key.

    This is the safety net that keeps the collector working when an actor
    renames or nests its output fields.
    """
    if max_depth < 0:
        return None
    if isinstance(data, dict):
        for key, value in data.items():
            if _FOLLOWER_KEY_RE.match(str(key)):
                parsed = parse_count(value)
                if parsed is not None:
                    return parsed
        for value in data.values():
            found = find_follower_count(value, max_depth - 1)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data[:20]:
            found = find_follower_count(item, max_depth - 1)
            if found is not None:
                return found
    return None


def extract_followers(item: dict[str, Any], preferred: Sequence[str] = ()) -> int | None:
    """Preferred paths first, then the generic recursive search."""
    value = first_value(item, preferred)
    parsed = parse_count(value)
    if parsed is not None:
        return parsed
    return find_follower_count(item)


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%a %b %d %H:%M:%S %z %Y",   # Twitter's legacy created_at
    "%a, %d %b %Y %H:%M:%S %Z",  # RSS
    "%a, %d %b %Y %H:%M:%S %z",
    "%Y%m%d%H%M%S",              # GDELT seendate
)


def parse_datetime(value: Any) -> datetime | None:
    """Parse the many timestamp shapes scrapers emit; always returns UTC."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    # Epoch seconds or milliseconds.
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    if text.isdigit() and len(text) in (10, 13):
        return parse_datetime(int(text))

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def first_datetime(data: Any, paths: Iterable[str]) -> datetime | None:
    for path in paths:
        parsed = parse_datetime(dig(data, path))
        if parsed:
            return parsed
    return None


def first_text(data: Any, paths: Sequence[str], default: str = "") -> str:
    value = first_value(data, paths)
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return default
    return str(value).strip()
