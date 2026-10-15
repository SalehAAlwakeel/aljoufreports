"""Likes on our own posts from the last week.

Comment likes are a different number (how many people liked a reply). This
collector reads the like count on the posts themselves.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..config import AppConfig, Entity
from .apify import ApifyRunner
from .comments import DATE_PATHS
from .extract import dig, first_datetime, first_text, parse_count

log = logging.getLogger(__name__)

LIKE_PATHS = (
    "likesCount",
    "likes",
    "likeCount",
    "like_count",
    "favorite_count",
    "favoriteCount",
    "edge_liked_by.count",
    "stats.likes",
    "stats.like",
    "stats.reactions",
    "stats.total_reactions",
    "numLikes",
    "numberOfLikes",
    "reactionCount",
)
URL_PATHS = (
    "url",
    "postUrl",
    "post_url",
    "twitterUrl",
    "permalink",
    "shortCode",
    "id",
)


def _likes(item: dict[str, Any]) -> int | None:
    for path in LIKE_PATHS:
        value = parse_count(dig(item, path))
        if value is not None:
            return value
    return None


def _post_url(item: dict[str, Any], platform: str) -> str:
    url = first_text(item, URL_PATHS)
    if not url:
        return ""
    if url.startswith("http"):
        return url
    if platform == "instagram":
        return f"https://www.instagram.com/p/{url}/"
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={url}"
    return url


class PostLikeCollector:
    """Recent posts for the owned account only. Competitors are not included."""

    def __init__(self, config: AppConfig, runner: ApifyRunner):
        self.config = config
        self.runner = runner
        self.errors: list[str] = []

    def collect(self, entity: Entity) -> list[dict[str, Any]]:
        days = self.config.settings.windows.comments_scrape_days
        now = datetime.now(ZoneInfo("Asia/Riyadh"))
        cutoff = (now - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cutoff = cutoff.astimezone(timezone.utc)
        since = cutoff.astimezone(ZoneInfo("Asia/Riyadh")).strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []
        account = entity.account("instagram")
        if account is not None:
            rows.extend(self._instagram(entity, account.url, cutoff))
        account = entity.account("x")
        if account is not None:
            rows.extend(self._x(entity, account.handle, since, cutoff))
        account = entity.account("youtube")
        if account is not None:
            rows.extend(self._youtube(entity, account.url, cutoff))
        account = entity.account("linkedin")
        if account is not None:
            rows.extend(self._linkedin(entity, account.handle, cutoff))
        return rows

    def _keep(
        self,
        entity: Entity,
        platform: str,
        items: list[dict[str, Any]],
        cutoff: datetime,
    ) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            published = first_datetime(item, DATE_PATHS)
            if published is not None and published < cutoff:
                continue
            likes = _likes(item)
            url = _post_url(item, platform)
            if likes is None or not url:
                continue
            kept.append(
                {
                    "entity_key": entity.key,
                    "platform": platform,
                    "post_url": url,
                    "published_at": published,
                    "likes": likes,
                }
            )
        log.info("  %s post likes: %d posts", platform, len(kept))
        return kept

    def _instagram(self, entity: Entity, url: str, cutoff: datetime) -> list[dict[str, Any]]:
        items, error = self.runner.run_safe(
            self.config.settings.apify.actor("instagram_posts"),
            {
                "directUrls": [url],
                "resultsType": "posts",
                "resultsLimit": 20,
                "addParentData": False,
            },
        )
        if error:
            self.errors.append(f"likes:{entity.key}/instagram: {error}")
            return []
        return self._keep(entity, "instagram", items, cutoff)

    def _x(self, entity: Entity, handle: str, since: str, cutoff: datetime) -> list[dict[str, Any]]:
        items, error = self.runner.run_safe(
            self.config.settings.apify.actor("x_tweets"),
            {
                "searchTerms": [f"from:{handle} since:{since}"],
                "sort": "Latest",
                "maxItems": 20,
            },
        )
        if error:
            self.errors.append(f"likes:{entity.key}/x: {error}")
            return []
        return self._keep(entity, "x", items, cutoff)

    def _youtube(self, entity: Entity, url: str, cutoff: datetime) -> list[dict[str, Any]]:
        items, error = self.runner.run_safe(
            self.config.settings.apify.actor("youtube_channel"),
            {
                "startUrls": [{"url": url}],
                "maxResults": 15,
                "maxResultsShorts": 0,
                "maxResultStreams": 0,
            },
        )
        if error:
            self.errors.append(f"likes:{entity.key}/youtube: {error}")
            return []
        return self._keep(entity, "youtube", items, cutoff)

    def _linkedin(self, entity: Entity, handle: str, cutoff: datetime) -> list[dict[str, Any]]:
        items, error = self.runner.run_safe(
            self.config.settings.apify.actor("linkedin_posts"),
            {
                "company_name": handle,
                "limit": 20,
                "sort": "recent",
                "page_number": 1,
            },
        )
        if error:
            self.errors.append(f"likes:{entity.key}/linkedin: {error}")
            return []
        return self._keep(entity, "linkedin", items, cutoff)
