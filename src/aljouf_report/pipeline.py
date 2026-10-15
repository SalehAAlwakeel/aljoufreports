"""Weekly pipeline orchestrator.

Stages run independently and record failures into the run summary rather than
raising, so a single broken scraper or missing credential degrades the report
instead of cancelling it.
"""

from __future__ import annotations

import logging
from typing import Any

from .analysis import SentimentAnalyzer
from .collectors import ApifyRunner, CommentCollector, FollowerCollector, NewsCollector
from .config import AppConfig, load_config
from .models import OWNED_ONLY_PLATFORMS, FollowerSnapshot, RunSummary, utcnow
from .notify import WeeklyMailer
from .notify.mailer import embed_images, report_file
from .storage import Database, sync_all

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: AppConfig | None = None):
        self.config = config or load_config()
        self.db = Database(self.config.db_path)

    def run(
        self,
        *,
        skip_followers: bool = False,
        skip_news: bool = False,
        skip_comments: bool = False,
        skip_sentiment: bool = False,
        skip_sheets: bool = False,
        skip_email: bool = False,
    ) -> RunSummary:
        started = utcnow()
        run_id = self.db.start_run(started)
        summary = RunSummary(run_id=run_id, started_at=started)

        log.info("=" * 68)
        log.info("Run %d started at %s", run_id, started.isoformat())
        log.info("Database: %s", self.config.db_path)
        log.info("=" * 68)

        runner = ApifyRunner(self.config.secrets.apify_token, self.config.settings.apify)
        if not runner.available and not (skip_followers and skip_comments):
            summary.errors.append(
                "APIFY_TOKEN not set: follower and comment collection were skipped."
            )

        if not skip_followers:
            self._stage_followers(run_id, summary, runner)
        if not skip_news:
            self._stage_news(run_id, summary)
        if not skip_comments:
            self._stage_comments(run_id, summary, runner)
        if not skip_sentiment:
            self._stage_sentiment(summary)

        # The email reports this week's comments only. The dashboard keeps the
        # full archive via comments_lookback_days.
        summary.sentiment_counts = self.db.sentiment_counts(
            self.config.settings.windows.comments_scrape_days
        )

        if not skip_sheets:
            self._stage_sheets(summary)

        summary.finished_at = utcnow()
        status = "completed_with_warnings" if summary.errors else "completed"
        self.db.finish_run(
            run_id,
            summary.finished_at,
            status,
            notes="; ".join(summary.errors[:20]),
        )

        if not skip_email:
            self._stage_email(summary)

        log.info("=" * 68)
        log.info(
            "Run %d %s in %.1fs | followers=%d news=%d comments=%d analysed=%d warnings=%d",
            run_id,
            status,
            summary.duration_secs,
            summary.followers_collected,
            summary.news_collected,
            summary.comments_collected,
            summary.comments_analyzed,
            len(summary.errors),
        )
        log.info("=" * 68)
        return summary

    # -- stages ----------------------------------------------------------- #
    def _stage_followers(
        self, run_id: int, summary: RunSummary, runner: ApifyRunner
    ) -> None:
        log.info("[1/5] Follower counts")
        try:
            snapshots = FollowerCollector(self.config, runner).collect()
        except Exception as exc:  # noqa: BLE001
            log.exception("Follower stage failed")
            summary.errors.append(f"followers: {exc}")
            return

        self.db.save_followers(run_id, snapshots)
        public = self._save_public_counts(run_id, summary)
        summary.followers_collected = sum(
            1 for s in (*snapshots, *public) if s.followers is not None
        )
        summary.followers_failed = sum(1 for s in snapshots if s.followers is None)

        # Keep only distinct error messages; a missing token produces one per account.
        for message in sorted({s.error for s in snapshots if s.error}):
            summary.errors.append(f"followers: {message}")

    def _stage_news(self, run_id: int, summary: RunSummary) -> None:
        log.info("[2/5] News and trends")
        collector = NewsCollector(self.config)
        try:
            articles = collector.collect()
        except Exception as exc:  # noqa: BLE001
            log.exception("News stage failed")
            summary.errors.append(f"news: {exc}")
            return

        self.db.save_news(run_id, articles)
        summary.news_collected = len(articles)
        summary.errors.extend(collector.errors)

    def _stage_comments(
        self, run_id: int, summary: RunSummary, runner: ApifyRunner
    ) -> None:
        log.info("[3/5] Comments")
        collector = CommentCollector(self.config, runner)
        try:
            comments = collector.collect()
        except Exception as exc:  # noqa: BLE001
            log.exception("Comment stage failed")
            summary.errors.append(f"comments: {exc}")
            return

        self.db.save_comments(run_id, comments)
        summary.comments_collected = len(comments)
        summary.errors.extend(collector.errors)

    def _stage_sentiment(self, summary: RunSummary) -> None:
        log.info("[4/5] Sentiment analysis")
        window = self.config.settings.windows.comments_lookback_days
        pending = self.db.comments_needing_sentiment(window)

        if not pending:
            log.info("Sentiment: nothing new to analyse (cache covers the window).")
            return

        analyzer = SentimentAnalyzer(self.config)
        results = analyzer.analyze(pending)
        summary.comments_analyzed = self.db.save_sentiments(results)
        summary.errors.extend(analyzer.errors)

    def _stage_sheets(self, summary: RunSummary) -> None:
        log.info("[5/5] Google Sheets mirror")
        try:
            tabs = sync_all(self.config, self.db)
            if tabs:
                log.info("Sheets: synced tabs %s", ", ".join(tabs))
        except Exception as exc:  # noqa: BLE001
            log.exception("Sheets stage failed")
            summary.errors.append(f"sheets: {exc}")

    def _stage_email(self, summary: RunSummary) -> None:
        log.info("Weekly email")
        mailer = WeeklyMailer(self.config)
        if not mailer.enabled:
            log.info("Email skipped (SMTP or recipients not configured).")
            return
        try:
            days = self.config.settings.windows.comments_scrape_days
            outside_days = max(days, self.config.settings.windows.news_lookback_days)
            owned = self.owned_follower_deltas()
            follower_total = sum(
                int(row["followers"])
                for row in owned
                if row.get("followers") is not None
                and row.get("platform") not in OWNED_ONLY_PLATFORMS
            )
            week_comments = self.db.scored_comments(days)
            week_news = self.db.recent_news(days)
            outside_news = self.db.recent_news(outside_days)
            ranking = self.follower_ranking()
            top_negative = self.top_negative_comments()
            body, images = mailer.render_html(
                summary,
                owned_followers=owned,
                top_negative=top_negative,
                week_comments=week_comments,
                week_news=week_news,
                ranking=ranking,
                outside_news=outside_news,
                post_likes=self.db.week_post_likes(days),
            )
            report_name, report_bytes, report_subtype = report_file(embed_images(body, images))
            mailer.send(
                summary,
                body,
                follower_total,
                images,
                report_name=report_name,
                report_bytes=report_bytes,
                report_subtype=report_subtype,
            )
        except Exception as exc:  # noqa: BLE001 - email must not fail the run
            log.warning("Email failed: %s", exc)
            summary.errors.append(f"email: {exc}")

    # -- report helpers (shared with the CLI and dashboard) --------------- #
    def _save_public_counts(self, run_id: int, summary: RunSummary | None = None) -> list[FollowerSnapshot]:
        """Read the WhatsApp and Snapchat pages and store those follower counts."""
        from .collectors.public_counts import (
            fetch_page,
            parse_snapchat_subscribers,
            parse_whatsapp_followers,
        )

        entity = next((item for item in self.config.accounts.entities if item.owned), None)
        if entity is None:
            return []
        readers = (
            ("whatsapp", parse_whatsapp_followers),
            ("snapchat", parse_snapchat_subscribers),
        )
        snapshots: list[FollowerSnapshot] = []
        for platform, parser in readers:
            account = entity.account(platform)
            if account is None or not account.url:
                continue
            try:
                count = parser(fetch_page(account.url))
            except Exception as exc:  # noqa: BLE001 - one page must not cancel the run
                log.warning("Public follower count for %s failed: %s", platform, exc)
                if summary is not None:
                    summary.errors.append(f"followers:{platform}: {exc}")
                continue
            if platform == "snapchat" and not count and account.followers:
                count = account.followers
            if count is None:
                if summary is not None:
                    summary.errors.append(f"followers:{platform}: page had no follower count")
                continue
            log.info("  %s/%s -> %s followers", entity.key, platform, f"{count:,}")
            snapshots.append(
                FollowerSnapshot(
                    entity_key=entity.key,
                    entity_name_ar=entity.name_ar,
                    entity_name_en=entity.name_en,
                    owned=True,
                    platform=platform,
                    handle=account.handle,
                    profile_url=account.url,
                    followers=count,
                )
            )
        if snapshots:
            self.db.save_followers(run_id, snapshots)
        return snapshots

    def record_public_counts(self) -> list[FollowerSnapshot]:
        """Store the latest WhatsApp and Snapchat counts on the current run."""
        run_id = self.db.latest_run_id()
        if run_id is None:
            run_id = self.db.start_run(utcnow())
            self.db.finish_run(run_id, utcnow(), "completed")
        return self._save_public_counts(run_id)

    def refresh_owned_week(self) -> dict[str, Any]:
        """Recount our Instagram followers and the likes on this week's posts.

        Other platforms keep their last known follower count in the same run,
        so the office total does not drop to Instagram alone.
        """
        from .collectors.engagement import PostLikeCollector
        from .collectors.followers import FollowerCollector

        entity = next(item for item in self.config.accounts.entities if item.owned)
        runner = ApifyRunner(self.config.secrets.apify_token, self.config.settings.apify)
        started = utcnow()
        run_id = self.db.start_run(started)
        notes: list[str] = []

        fresh = FollowerCollector(self.config, runner).collect_one(entity, "instagram")
        if fresh.error:
            notes.append(fresh.error)

        history = self.db.follower_history(limit_runs=8)
        latest: dict[str, dict[str, Any]] = {}
        for row in sorted(
            (
                item
                for item in history
                if item["entity_key"] == entity.key and item["followers"] is not None
            ),
            key=lambda item: int(item["run_id"]),
        ):
            latest[str(row["platform"])] = row

        snapshots: list[FollowerSnapshot] = []
        if fresh.followers is not None:
            snapshots.append(fresh)
        for platform, row in latest.items():
            if platform == "instagram" and fresh.followers is not None:
                continue
            snapshots.append(
                FollowerSnapshot(
                    entity_key=str(row["entity_key"]),
                    entity_name_ar=str(row["entity_name_ar"]),
                    entity_name_en=str(row["entity_name_en"]),
                    owned=bool(row["owned"]),
                    platform=platform,
                    handle=str(row["handle"]),
                    profile_url=str(row["profile_url"]),
                    followers=int(row["followers"]),
                    captured_at=utcnow(),
                )
            )
        self.db.save_followers(run_id, snapshots)

        likes_collector = PostLikeCollector(self.config, runner)
        likes = likes_collector.collect(entity)
        notes.extend(likes_collector.errors)
        self.db.save_post_likes(run_id, likes)
        self.db.finish_run(
            run_id,
            utcnow(),
            "completed_with_warnings" if notes else "completed",
            notes="; ".join(notes[:20]),
        )
        days = self.config.settings.windows.comments_scrape_days
        instagram = next((row for row in snapshots if row.platform == "instagram"), None)
        previous = latest.get("instagram", {}).get("followers")
        delta = (
            int(instagram.followers) - int(previous)
            if instagram and instagram.followers is not None and previous is not None
            else None
        )
        return {
            "instagram_followers": None if instagram is None else instagram.followers,
            "instagram_delta": delta,
            "week_likes": self.db.week_post_likes(days),
            "posts": len(likes),
            "notes": notes,
        }

    def owned_follower_deltas(self) -> list[dict[str, Any]]:
        """Latest follower count per owned platform, with week-over-week change.

        Only runs that actually captured counts are considered. A run where
        collection failed would otherwise become the "latest" and report every
        account as empty with a large negative delta.
        """
        # Look back further than two runs so a failed run does not hide the
        # last good comparison.
        history = self.db.follower_history(limit_runs=6)
        owned = [
            row for row in history if row["owned"] and row["followers"] is not None
        ]
        if not owned:
            return []

        series: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in sorted(owned, key=lambda item: (int(item["run_id"]), str(item["captured_at"]))):
            series.setdefault((str(row["entity_key"]), str(row["platform"])), []).append(row)

        rows = []
        for group in series.values():
            latest = group[-1]
            before = group[-2]["followers"] if len(group) > 1 else None
            delta = (
                int(latest["followers"]) - int(before)
                if before is not None
                else None
            )
            rows.append({**latest, "delta": delta})
        present = {row["platform"] for row in rows}
        owned_entity = next((item for item in self.config.accounts.entities if item.owned), None)
        for platform in OWNED_ONLY_PLATFORMS:
            if platform not in present:
                account = owned_entity.account(platform) if owned_entity else None
                rows.append(
                    {
                        "platform": platform,
                        "followers": None,
                        "delta": None,
                        "owned": True,
                        "handle": account.handle if account else "",
                        "profile_url": account.url if account else "",
                    }
                )
        return sorted(rows, key=lambda r: r["platform"])

    def follower_ranking(self) -> list[dict[str, Any]]:
        """Every authority's latest follower total, largest first."""
        counted = self.db.latest_counted_snapshots()
        latest_by_entity: dict[str, int] = {}
        for row in counted:
            key = str(row["entity_key"])
            latest_by_entity[key] = max(latest_by_entity.get(key, 0), int(row["run_id"]))
        board = []
        for key, run_id in latest_by_entity.items():
            rows = [
                row for row in counted
                if str(row["entity_key"]) == key
                and int(row["run_id"]) == run_id
                and row["platform"] not in OWNED_ONLY_PLATFORMS
            ]
            if not rows:
                continue
            entity = next(
                (item for item in self.config.accounts.entities if item.key == key),
                None,
            )
            board.append(
                {
                    "entity_key": key,
                    "name": rows[0]["entity_name_ar"],
                    "short_name": entity.short_name if entity else "",
                    "owned": bool(rows[0]["owned"]),
                    "followers": sum(int(row["followers"]) for row in rows),
                }
            )
        return sorted(board, key=lambda row: int(row["followers"]), reverse=True)

    def top_negative_comments(self, limit: int = 10) -> list[dict[str, Any]]:
        window = self.config.settings.windows.comments_scrape_days
        negative = [
            row for row in self.db.scored_comments(window) if row.get("label") == "Negative"
        ]
        return negative[:limit]

    def news_counts_by_keyword(self) -> dict[str, int]:
        window = self.config.settings.windows.comments_scrape_days
        counts: dict[str, int] = {}
        for row in self.db.recent_news(window):
            # A deduped article can carry several comma-joined keywords.
            for keyword in str(row.get("keyword") or "").split(","):
                key = keyword.strip()
                if key:
                    counts[key] = counts.get(key, 0) + 1
        return counts
