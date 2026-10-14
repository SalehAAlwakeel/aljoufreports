"""Recent comments on our own posts, via public scraping.

Most platforms need two stages: find the account's recent posts, then pull the
comment threads for those posts. X is the exception -- replies addressed to the
account are themselves searchable, so one `to:handle` search stands in for the
comment thread.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import AppConfig, Entity, PlatformAccount
from ..models import SocialComment, utcnow
from .apify import ApifyRunner
from .extract import first_datetime, first_text, parse_count

log = logging.getLogger(__name__)

# Field paths for reading a comment out of varying actor outputs.
TEXT_PATHS = ("text", "comment", "commentText", "message", "content", "body", "full_text")
AUTHOR_PATHS = (
    "ownerUsername",
    "owner.username",
    "author",
    "authorName",
    "authorDisplayName",
    "username",
    "userName",
    "author.userName",
    "author.name",
)
DATE_PATHS = (
    # Epoch milliseconds first: LinkedIn's sibling `posted_at.date` is a naive
    # local-looking string, while the timestamp is unambiguous.
    "posted_at.timestamp",
    "posted_at.date",
    "timestamp",
    "createdAt",
    "created_at",
    "publishedAt",
    "publishedTimeText",
    "date",
    "time",
)
ID_PATHS = ("id", "commentId", "cid", "comment_id", "url")
LIKE_PATHS = (
    "likesCount",
    "likeCount",
    "voteCount",
    "likes",
    "favorite_count",
    "stats.total_reactions",
)
POST_URL_PATHS = (
    "postUrl",
    "post.url",
    "videoUrl",
    "url",
    "inputUrl",
    "pageUrl",
    "comment_url",
)


class CommentCollector:
    def __init__(self, config: AppConfig, runner: ApifyRunner):
        self.config = config
        self.runner = runner
        self.errors: list[str] = []

    @property
    def _cutoff(self) -> datetime:
        days = self.config.settings.windows.comments_scrape_days
        return datetime.now(timezone.utc) - timedelta(days=days)

    def collect(self) -> list[SocialComment]:
        cfg = self.config.settings.comments
        entities = (
            self.config.accounts.owned if cfg.owned_only else self.config.accounts.entities
        )

        collected: list[SocialComment] = []
        for entity in entities:
            for platform in cfg.platforms:
                account = entity.account(platform)
                if not account:
                    continue
                try:
                    comments = self._collect_for(entity, account, platform)
                except Exception as exc:  # noqa: BLE001 - keep the run alive
                    log.exception("Comment collection failed: %s/%s", entity.key, platform)
                    self.errors.append(f"comments:{entity.key}/{platform}: {exc}")
                    continue
                log.info(
                    "  %s/%s -> %d comments in window",
                    entity.key,
                    platform,
                    len(comments),
                )
                collected.extend(comments)

        return self._dedupe(collected)

    def _collect_for(
        self, entity: Entity, account: PlatformAccount, platform: str
    ) -> list[SocialComment]:
        if platform == "instagram":
            return self._instagram(entity, account)
        if platform == "x":
            return self._x(entity, account)
        if platform == "youtube":
            return self._youtube(entity, account)
        if platform == "linkedin":
            return self._linkedin(entity, account)
        log.warning("No comment collector for platform '%s'.", platform)
        return []

    # LinkedIn company pages post several times a day, so a fixed post cap
    # stops well short of the lookback window. Page until the posts are older
    # than the window instead.
    _LINKEDIN_PAGE_SIZE = 100
    _LINKEDIN_MAX_PAGES = 4

    # -- LinkedIn: company posts, then comments on the ones that have any -- #
    def _linkedin(self, entity: Entity, account: PlatformAccount) -> list[SocialComment]:
        cfg = self.config.settings.comments
        actors = self.config.settings.apify
        post_items = self._linkedin_posts(entity, account, actors)
        post_ids = self.linkedin_comment_targets(post_items, self._cutoff)
        if not post_ids:
            return []

        comment_items = self._linkedin_comments(entity, actors, post_ids, cfg.max_comments_per_post)
        # The comments actor sometimes answers "0 comments" for a post that
        # reported some. One retry recovers most of those without refetching
        # the posts that already came back.
        returned = {str(item.get("post_input")) for item in comment_items if isinstance(item, dict)}
        missing = [post_id for post_id in post_ids if post_id not in returned]
        if missing:
            log.info("  linkedin: retrying %d posts the actor returned empty", len(missing))
            comment_items.extend(
                self._linkedin_comments(entity, actors, missing, cfg.max_comments_per_post)
            )

        return self._to_comments(
            self._flatten_linkedin(comment_items), entity, "linkedin"
        )

    def _linkedin_posts(self, entity: Entity, account: PlatformAccount, actors: Any) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        cutoff = self._cutoff
        for page in range(1, self._LINKEDIN_MAX_PAGES + 1):
            items, error = self.runner.run_safe(
                actors.actor("linkedin_posts"),
                {
                    "company_name": account.handle,
                    "limit": self._LINKEDIN_PAGE_SIZE,
                    "sort": "recent",
                    "page_number": page,
                },
            )
            if error:
                self.errors.append(f"comments:{entity.key}/linkedin posts: {error}")
                break
            page_items = [item for item in items if isinstance(item, dict)]
            if not page_items:
                break
            collected.extend(page_items)
            dates = [d for d in (first_datetime(item, DATE_PATHS) for item in page_items) if d]
            if dates and min(dates) < cutoff:
                break
            if len(page_items) < self._LINKEDIN_PAGE_SIZE:
                break
        return collected

    @staticmethod
    def linkedin_comment_targets(items: list[dict[str, Any]], cutoff: datetime) -> list[str]:
        """Post ids inside the window whose own stats say they have comments."""
        ids: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            published = first_datetime(item, DATE_PATHS)
            if published and published < cutoff:
                continue
            if not parse_count((item.get("stats") or {}).get("comments")):
                continue
            post_id = first_text(item, ("activity_urn", "full_urn", "post_url"))
            if post_id and post_id not in ids:
                ids.append(post_id)
        return ids

    def _linkedin_comments(
        self, entity: Entity, actors: Any, post_ids: list[str], limit: int
    ) -> list[dict[str, Any]]:
        items, error = self.runner.run_safe(
            actors.actor("linkedin_comments"),
            {
                "postIds": post_ids,
                "limit": limit,
                "sortOrder": "most recent",
            },
        )
        if error:
            self.errors.append(f"comments:{entity.key}/linkedin comments: {error}")
            return []
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def _flatten_linkedin(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Lift nested replies to top level so each one gets scored too.

        A reply to a comment is public feedback like any other, and the actor
        returns them nested under their parent rather than as separate items.
        """
        flat: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            flat.append(item)
            for reply in item.get("replies") or []:
                if isinstance(reply, dict):
                    flat.append(reply)
        return flat

    # -- Instagram: posts, then comments on those posts ------------------- #
    def _instagram(self, entity: Entity, account: PlatformAccount) -> list[SocialComment]:
        cfg = self.config.settings.comments
        actors = self.config.settings.apify

        post_items, error = self.runner.run_safe(
            actors.actor("instagram_posts"),
            {
                "directUrls": [account.url],
                "resultsType": "posts",
                "resultsLimit": cfg.max_posts_per_account,
                "addParentData": False,
            },
        )
        if error:
            self.errors.append(f"comments:{entity.key}/instagram posts: {error}")
            return []

        post_urls = self._recent_post_urls(post_items, ("url", "postUrl", "shortCode"))
        if not post_urls:
            return []

        comment_items, error = self.runner.run_safe(
            actors.actor("instagram_comments"),
            {
                "directUrls": post_urls,
                "resultsLimit": cfg.max_comments_per_post,
            },
        )
        if error:
            self.errors.append(f"comments:{entity.key}/instagram comments: {error}")
            return []

        return self._to_comments(comment_items, entity, "instagram")

    # -- X: replies addressed to the account ------------------------------ #
    def _x(self, entity: Entity, account: PlatformAccount) -> list[SocialComment]:
        cfg = self.config.settings.comments
        since = self._cutoff.strftime("%Y-%m-%d")

        items, error = self.runner.run_safe(
            self.config.settings.apify.actor("x_tweets"),
            {
                # Replies to the account are the closest public equivalent of
                # comments, and are reachable without any account access.
                "searchTerms": [f"to:{account.handle} since:{since}"],
                "sort": "Latest",
                "maxItems": cfg.max_posts_per_account * cfg.max_comments_per_post,
                "onlyReply": True,
            },
        )
        if error:
            self.errors.append(f"comments:{entity.key}/x: {error}")
            return []

        return self._to_comments(
            items,
            entity,
            "x",
            text_paths=("text", "full_text", "legacy.full_text"),
            author_paths=("author.userName", "author.name", "username", "userName"),
            post_url_paths=("inReplyToUrl", "conversationUrl", "url"),
        )

    # -- YouTube: videos, then comments on those videos ------------------- #
    def _youtube(self, entity: Entity, account: PlatformAccount) -> list[SocialComment]:
        cfg = self.config.settings.comments
        actors = self.config.settings.apify

        video_items, error = self.runner.run_safe(
            actors.actor("youtube_channel"),
            {
                "startUrls": [{"url": account.url}],
                "maxResults": cfg.max_posts_per_account,
                "maxResultsShorts": 0,
                "maxResultStreams": 0,
            },
        )
        if error:
            self.errors.append(f"comments:{entity.key}/youtube videos: {error}")
            return []

        video_urls = self._recent_post_urls(video_items, ("url", "videoUrl", "id"))
        if not video_urls:
            return []

        comment_items, error = self.runner.run_safe(
            actors.actor("youtube_comments"),
            {
                "startUrls": [{"url": u} for u in video_urls],
                "maxComments": cfg.max_comments_per_post,
            },
        )
        if error:
            self.errors.append(f"comments:{entity.key}/youtube comments: {error}")
            return []

        return self._to_comments(comment_items, entity, "youtube")

    # -- shared helpers --------------------------------------------------- #
    def _recent_post_urls(
        self, items: list[dict[str, Any]], url_paths: tuple[str, ...]
    ) -> list[str]:
        """Post URLs published inside the lookback window.

        Posts with no readable timestamp are kept: dropping them would silently
        lose comment threads whenever an actor omits the date field.
        """
        cutoff = self._cutoff
        urls: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            published = first_datetime(item, DATE_PATHS)
            if published and published < cutoff:
                continue
            url = first_text(item, url_paths)
            if not url:
                continue
            if not url.startswith("http"):
                # Bare shortcode / video id.
                url = (
                    f"https://www.instagram.com/p/{url}/"
                    if len(url) < 20
                    else f"https://www.youtube.com/watch?v={url}"
                )
            urls.append(url)
        max_posts = self.config.settings.comments.max_posts_per_account
        return urls[:max_posts]

    def _to_comments(
        self,
        items: list[dict[str, Any]],
        entity: Entity,
        platform: str,
        text_paths: tuple[str, ...] = TEXT_PATHS,
        author_paths: tuple[str, ...] = AUTHOR_PATHS,
        post_url_paths: tuple[str, ...] = POST_URL_PATHS,
    ) -> list[SocialComment]:
        cutoff = self._cutoff
        max_chars = self.config.settings.sentiment.max_comment_chars
        out: list[SocialComment] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            text = first_text(item, text_paths)
            if not text:
                continue

            published = first_datetime(item, DATE_PATHS)
            if published and published < cutoff:
                continue

            out.append(
                SocialComment(
                    entity_key=entity.key,
                    platform=platform,
                    text=text[:max_chars],
                    author=first_text(item, author_paths),
                    post_url=first_text(item, post_url_paths),
                    comment_url=first_text(item, ("commentUrl", "url")),
                    native_id=first_text(item, ID_PATHS),
                    published_at=published,
                    likes=parse_count(
                        next(
                            (item[p] for p in LIKE_PATHS if p in item),
                            None,
                        )
                    ),
                    fetched_at=utcnow(),
                )
            )
        return out

    @staticmethod
    def _dedupe(comments: list[SocialComment]) -> list[SocialComment]:
        seen: set[str] = set()
        unique = []
        for comment in comments:
            key = comment.comment_hash
            if key in seen:
                continue
            seen.add(key)
            unique.append(comment)
        return unique
