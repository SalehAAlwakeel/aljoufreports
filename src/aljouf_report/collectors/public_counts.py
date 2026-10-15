"""Follower counts published on the WhatsApp and Snapchat pages themselves.

These two are not collected through Apify and are not part of the office
comparison. The number on each card is whatever the public page states.
"""

from __future__ import annotations

import re
import urllib.request

_WHATSAPP_FOLLOWERS = re.compile(r"([\d][\d,]*)\s+followers", re.IGNORECASE)
_SNAPCHAT_SUBSCRIBERS = re.compile(
    r'"publicProfileInfo":\{.*?"subscriberCount":"(\d+)"',
    re.DOTALL,
)


def parse_whatsapp_followers(page: str) -> int | None:
    match = _WHATSAPP_FOLLOWERS.search(page)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def parse_snapchat_subscribers(page: str) -> int | None:
    match = _SNAPCHAT_SUBSCRIBERS.search(page)
    if not match:
        return None
    return int(match.group(1))


def fetch_page(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")
