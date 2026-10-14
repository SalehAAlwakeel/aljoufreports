"""SQLite persistence. The pipeline writes here first; Sheets is a mirror."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..models import (
    FollowerSnapshot,
    NewsArticle,
    SentimentResult,
    SocialComment,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class Database:
    """Thin repository over sqlite3 with upsert semantics everywhere."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # -- connection management ------------------------------------------- #
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    # -- runs ------------------------------------------------------------- #
    def start_run(self, started_at: datetime) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, status) VALUES (?, 'running')",
                (_iso(started_at),),
            )
            return int(cur.lastrowid)

    def finish_run(
        self, run_id: int, finished_at: datetime, status: str, notes: str = ""
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, notes = ? WHERE id = ?",
                (_iso(finished_at), status, notes, run_id),
            )

    def latest_run_id(self) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE status != 'running' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return int(row["id"]) if row else None

    # -- followers -------------------------------------------------------- #
    def save_followers(self, run_id: int, snapshots: Iterable[FollowerSnapshot]) -> int:
        rows = [
            (
                run_id,
                _iso(s.captured_at),
                s.entity_key,
                s.entity_name_ar,
                s.entity_name_en,
                int(s.owned),
                s.platform,
                s.handle,
                s.profile_url,
                s.followers,
                s.error,
                json.dumps(s.raw, ensure_ascii=False) if s.raw else None,
            )
            for s in snapshots
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO follower_snapshots (
                    run_id, captured_at, entity_key, entity_name_ar, entity_name_en,
                    owned, platform, handle, profile_url, followers, error, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (run_id, entity_key, platform) DO UPDATE SET
                    captured_at = excluded.captured_at,
                    followers   = excluded.followers,
                    error       = excluded.error,
                    raw_json    = excluded.raw_json
                """,
                rows,
            )
        return len(rows)

    def follower_history(self, limit_runs: int = 26) -> list[dict[str, Any]]:
        """Snapshots for the most recent `limit_runs` runs, oldest first."""
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT f.*, r.started_at AS run_started_at
                    FROM follower_snapshots f
                    JOIN runs r ON r.id = f.run_id
                    WHERE f.run_id IN (
                        SELECT id FROM runs ORDER BY id DESC LIMIT ?
                    )
                    ORDER BY r.started_at ASC, f.entity_key, f.platform
                    """,
                    (limit_runs,),
                ).fetchall()
            ]

    # -- news ------------------------------------------------------------- #
    def save_news(self, run_id: int, articles: Iterable[NewsArticle]) -> int:
        rows = [
            (
                run_id,
                a.url_hash,
                a.title,
                a.url,
                _iso(a.published_at),
                a.source,
                a.domain,
                a.keyword,
                a.language,
                a.collector,
                a.snippet,
                _iso(a.fetched_at),
            )
            for a in articles
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO news_articles (
                    run_id, url_hash, title, url, published_at, source, domain,
                    keyword, language, collector, snippet, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (url_hash) DO UPDATE SET
                    run_id   = excluded.run_id,
                    keyword  = excluded.keyword,
                    snippet  = COALESCE(NULLIF(excluded.snippet, ''), news_articles.snippet)
                """,
                rows,
            )
        return len(rows)

    def recent_news(self, days: int = 14) -> list[dict[str, Any]]:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM news_articles
                    WHERE published_at IS NULL OR published_at >= ?
                    ORDER BY COALESCE(published_at, fetched_at) DESC
                    """,
                    (cutoff,),
                ).fetchall()
            ]

    # -- comments --------------------------------------------------------- #
    def save_comments(self, run_id: int, comments: Iterable[SocialComment]) -> int:
        rows = [
            (
                run_id,
                c.comment_hash,
                c.entity_key,
                c.platform,
                c.native_id,
                c.author,
                c.text,
                c.post_url,
                c.comment_url,
                _iso(c.published_at),
                c.likes,
                _iso(c.fetched_at),
            )
            for c in comments
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO comments (
                    run_id, comment_hash, entity_key, platform, native_id, author,
                    text, post_url, comment_url, published_at, likes, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (comment_hash) DO UPDATE SET
                    run_id = excluded.run_id,
                    likes  = excluded.likes
                """,
                rows,
            )
        return len(rows)

    def comments_needing_sentiment(
        self, days: int = 7, *, include_scored: bool = False
    ) -> list[dict[str, Any]]:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        scored_filter = "" if include_scored else "AND s.comment_hash IS NULL"
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    f"""
                    SELECT c.comment_hash, c.text, c.platform, c.author
                    FROM comments c
                    LEFT JOIN sentiments s ON s.comment_hash = c.comment_hash
                    WHERE (c.published_at IS NULL OR c.published_at >= ?)
                      AND TRIM(c.text) != ''
                      {scored_filter}
                    ORDER BY c.published_at DESC
                    """,
                    (cutoff,),
                ).fetchall()
            ]

    def save_sentiments(self, results: Sequence[SentimentResult]) -> int:
        if not results:
            return 0
        rows = [
            (
                r.comment_hash,
                r.label,
                r.reason_ar,
                r.confidence,
                r.model,
                _iso(r.analyzed_at),
            )
            for r in results
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO sentiments (
                    comment_hash, label, reason_ar, confidence, model, analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (comment_hash) DO UPDATE SET
                    label       = excluded.label,
                    reason_ar   = excluded.reason_ar,
                    confidence  = excluded.confidence,
                    model       = excluded.model,
                    analyzed_at = excluded.analyzed_at
                """,
                rows,
            )
        return len(rows)

    def scored_comments(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM v_comments_scored
                    WHERE published_at IS NULL OR published_at >= ?
                    ORDER BY COALESCE(published_at, fetched_at) DESC
                    """,
                    (cutoff,),
                ).fetchall()
            ]

    def sentiment_counts(self, days: int = 7) -> dict[str, int]:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT label, COUNT(*) AS n FROM v_comments_scored
                WHERE label IS NOT NULL
                  AND (published_at IS NULL OR published_at >= ?)
                GROUP BY label
                """,
                (cutoff,),
            ).fetchall()
        return {r["label"]: int(r["n"]) for r in rows}

    # -- generic ---------------------------------------------------------- #
    def table_rows(self, table: str) -> list[dict[str, Any]]:
        """Full dump of a table/view. Used by the Google Sheets mirror."""
        allowed = {
            "runs",
            "follower_snapshots",
            "news_articles",
            "comments",
            "sentiments",
            "v_comments_scored",
        }
        if table not in allowed:
            raise ValueError(f"Refusing to dump unknown table: {table}")
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
