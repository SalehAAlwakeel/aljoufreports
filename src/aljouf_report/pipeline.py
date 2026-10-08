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
from .models import RunSummary, utcnow
from .notify import WeeklyMailer
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

        summary.sentiment_counts = self.db.sentiment_counts(
            self.config.settings.windows.comments_lookback_days
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
        summary.followers_collected = sum(1 for s in snapshots if s.followers is not None)
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
            body = mailer.render_html(
                summary,
                owned_followers=self.owned_follower_deltas(),
                top_negative=self.top_negative_comments(),
                news_count_by_keyword=self.news_counts_by_keyword(),
            )
            mailer.send(summary, body)
        except Exception as exc:  # noqa: BLE001 - email must not fail the run
            log.warning("Email failed: %s", exc)
            summary.errors.append(f"email: {exc}")

    # -- report helpers (shared with the CLI and dashboard) --------------- #
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

        run_ids = sorted({row["run_id"] for row in owned})
        latest_run = run_ids[-1]
        previous_run = run_ids[-2] if len(run_ids) > 1 else None

        previous = {
            (r["entity_key"], r["platform"]): r["followers"]
            for r in owned
            if r["run_id"] == previous_run
        }

        rows = []
        for row in owned:
            if row["run_id"] != latest_run:
                continue
            before = previous.get((row["entity_key"], row["platform"]))
            delta = (
                row["followers"] - before
                if row["followers"] is not None and before is not None
                else None
            )
            rows.append({**row, "delta": delta})
        return sorted(rows, key=lambda r: r["platform"])

    def top_negative_comments(self, limit: int = 10) -> list[dict[str, Any]]:
        window = self.config.settings.windows.comments_lookback_days
        negative = [
            row for row in self.db.scored_comments(window) if row.get("label") == "Negative"
        ]
        return negative[:limit]

    def news_counts_by_keyword(self) -> dict[str, int]:
        window = self.config.settings.windows.news_lookback_days
        counts: dict[str, int] = {}
        for row in self.db.recent_news(window):
            # A deduped article can carry several comma-joined keywords.
            for keyword in str(row.get("keyword") or "").split(","):
                key = keyword.strip()
                if key:
                    counts[key] = counts.get(key, 0) + 1
        return counts
