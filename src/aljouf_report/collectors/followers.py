"""Follower / subscriber counts for our accounts and the benchmarked set.

Each platform is collected in a single batched actor run (one run for all ten
Instagram handles, etc.) because Apify bills per run start as well as per
result. Results are then matched back to entities by handle.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..config import AppConfig, Entity, PlatformAccount
from ..models import FollowerSnapshot, utcnow
from .apify import ApifyRunner
from .extract import extract_followers, first_text

log = logging.getLogger(__name__)


def _norm_handle(value: str) -> str:
    """Canonical handle form for matching actor output back to our config."""
    text = (value or "").strip().lower().lstrip("@")
    text = text.rstrip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.split("?")[0]


# --------------------------------------------------------------------------- #
# Per-platform actor input + output shape
# --------------------------------------------------------------------------- #
class PlatformSpec:
    """How to ask an actor for follower counts and how to read its answer."""

    def __init__(
        self,
        actor_key: str,
        build_input: Callable[[list[str], list[str]], dict[str, Any]],
        follower_paths: tuple[str, ...],
        identity_paths: tuple[str, ...],
    ):
        self.actor_key = actor_key
        self.build_input = build_input
        self.follower_paths = follower_paths
        self.identity_paths = identity_paths


SPECS: dict[str, PlatformSpec] = {
    "instagram": PlatformSpec(
        actor_key="instagram_profile",
        build_input=lambda handles, urls: {"usernames": handles},
        follower_paths=(
            "followersCount",
            "followers_count",
            "followers",
            "edge_followed_by.count",
        ),
        identity_paths=("username", "userName", "handle", "inputUrl", "url"),
    ),
    "x": PlatformSpec(
        actor_key="x_profile",
        build_input=lambda handles, urls: {
            "twitterHandles": handles,
            "getFollowers": False,
            "getFollowing": False,
            "maxItems": len(handles),
        },
        follower_paths=(
            "followers",
            "followersCount",
            "legacy.followers_count",
            "public_metrics.followers_count",
        ),
        identity_paths=("userName", "username", "screen_name", "legacy.screen_name", "url"),
    ),
    "youtube": PlatformSpec(
        actor_key="youtube_channel",
        build_input=lambda handles, urls: {
            "startUrls": [{"url": u} for u in urls],
            "maxResults": 1,
            "maxResultsShorts": 0,
            "maxResultStreams": 0,
        },
        follower_paths=(
            "numberOfSubscribers",
            "channelTotalSubscribers",
            "subscriberCount",
            "subscribers",
        ),
        identity_paths=(
            "channelUsername",
            "channelHandle",
            "channelUrl",
            "input",
            "url",
        ),
    ),
    "linkedin": PlatformSpec(
        actor_key="linkedin_company",
        build_input=lambda handles, urls: {"identifier": list(handles)},
        follower_paths=(
            "stats.follower_count",
            "follower_count",
            "followerCount",
            "followersCount",
            "data.follower_count",
        ),
        identity_paths=(
            # The actor echoes the requested handle here, which is the most
            # reliable way to match a result back to the account we asked for.
            "input_identifier",
            "basic_info.universal_name",
            "basic_info.linkedin_url",
            "universal_name",
            "universalName",
            "public_identifier",
            "identifier",
            "url",
        ),
    ),
}

# Actors that accept only a single target per run, so the caller must fan out.
SINGLE_TARGET_PLATFORMS: set[str] = set()


class FollowerCollector:
    def __init__(self, config: AppConfig, runner: ApifyRunner):
        self.config = config
        self.runner = runner

    def collect(self) -> list[FollowerSnapshot]:
        snapshots: list[FollowerSnapshot] = []
        for platform in self.config.settings.followers.platforms:
            spec = SPECS.get(platform)
            if not spec:
                log.warning("No follower spec for platform '%s'; skipping.", platform)
                continue
            pairs = self.config.accounts.with_platform(platform)
            if not pairs:
                continue
            log.info("Collecting %s followers for %d accounts", platform, len(pairs))
            if platform in SINGLE_TARGET_PLATFORMS:
                snapshots.extend(self._collect_one_by_one(platform, spec, pairs))
            else:
                snapshots.extend(self._collect_batched(platform, spec, pairs))
        return snapshots

    def collect_one(self, entity: Entity, platform: str) -> FollowerSnapshot:
        """One account, so a new follow can be checked without scraping everyone."""
        spec = SPECS[platform]
        account = entity.account(platform)
        if account is None:
            raise KeyError(platform)
        return self._collect_one_by_one(platform, spec, [(entity, account)])[0]

    # -- strategies ------------------------------------------------------- #
    def _collect_batched(
        self,
        platform: str,
        spec: PlatformSpec,
        pairs: list[tuple[Entity, PlatformAccount]],
    ) -> list[FollowerSnapshot]:
        handles = [a.handle for _, a in pairs]
        urls = [a.url for _, a in pairs]
        actor_id = self.config.settings.apify.actor(spec.actor_key)
        items, error = self.runner.run_safe(actor_id, spec.build_input(handles, urls))

        by_handle = self._index_by_handle(items, spec)
        snapshots = []
        for entity, account in pairs:
            item = by_handle.get(_norm_handle(account.handle))
            snapshots.append(
                self._build_snapshot(entity, account, platform, spec, item, error)
            )
        return snapshots

    def _collect_one_by_one(
        self,
        platform: str,
        spec: PlatformSpec,
        pairs: list[tuple[Entity, PlatformAccount]],
    ) -> list[FollowerSnapshot]:
        actor_id = self.config.settings.apify.actor(spec.actor_key)
        snapshots = []
        for entity, account in pairs:
            items, error = self.runner.run_safe(
                actor_id, spec.build_input([account.handle], [account.url])
            )
            item = items[0] if items else None
            snapshots.append(
                self._build_snapshot(entity, account, platform, spec, item, error)
            )
        return snapshots

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _index_by_handle(
        items: list[dict[str, Any]], spec: PlatformSpec
    ) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            for path in spec.identity_paths:
                identity = first_text(item, (path,))
                if identity:
                    index.setdefault(_norm_handle(identity), item)
        return index

    def _build_snapshot(
        self,
        entity: Entity,
        account: PlatformAccount,
        platform: str,
        spec: PlatformSpec,
        item: dict[str, Any] | None,
        batch_error: str | None,
    ) -> FollowerSnapshot:
        followers: int | None = None
        error = batch_error

        if item is not None:
            followers = extract_followers(item, spec.follower_paths)
            if followers is None:
                error = "Actor returned a record but no follower count field."
            else:
                error = None
        elif error is None:
            error = "No matching record in actor output."

        if followers is not None:
            log.info("  %s/%s -> %s followers", entity.key, platform, f"{followers:,}")
        else:
            log.warning("  %s/%s -> unavailable (%s)", entity.key, platform, error)

        return FollowerSnapshot(
            entity_key=entity.key,
            entity_name_ar=entity.name_ar,
            entity_name_en=entity.name_en,
            owned=entity.owned,
            platform=platform,
            handle=account.handle,
            profile_url=account.url,
            followers=followers,
            captured_at=utcnow(),
            error=error,
            raw=item,
        )
