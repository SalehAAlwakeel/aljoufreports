"""Normalised records that flow from collectors into storage."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

SentimentLabel = Literal["Positive", "Negative", "Neutral"]

SENTIMENT_LABELS: tuple[str, ...] = ("Positive", "Negative", "Neutral")

# Arabic display labels for the dashboard and email.
SENTIMENT_AR: dict[str, str] = {
    "Positive": "إيجابي",
    "Negative": "سلبي",
    "Neutral": "محايد",
}

PLATFORM_AR: dict[str, str] = {
    "instagram": "إنستغرام",
    "x": "إكس",
    "youtube": "يوتيوب",
    "linkedin": "لينكد إن",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stable_hash(*parts: Any) -> str:
    """Deterministic short hash used for dedupe keys."""
    joined = "\u241f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:40]


class FollowerSnapshot(BaseModel):
    entity_key: str
    entity_name_ar: str
    entity_name_en: str
    owned: bool
    platform: str
    handle: str
    profile_url: str
    followers: int | None = None
    captured_at: datetime = Field(default_factory=utcnow)
    error: str | None = None
    raw: dict[str, Any] | None = None


class NewsArticle(BaseModel):
    title: str
    url: str
    published_at: datetime | None = None
    source: str = ""
    domain: str = ""
    keyword: str = ""
    language: str = ""
    collector: str = ""
    snippet: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)

    @property
    def url_hash(self) -> str:
        return stable_hash(self.url.split("?")[0].rstrip("/").lower())


class SocialComment(BaseModel):
    entity_key: str
    platform: str
    text: str
    author: str = ""
    post_url: str = ""
    comment_url: str = ""
    native_id: str = ""
    published_at: datetime | None = None
    likes: int | None = None
    fetched_at: datetime = Field(default_factory=utcnow)

    @property
    def comment_hash(self) -> str:
        # native_id is absent from some actors' output, so fall back to the
        # text + author + post triple, which is stable across runs.
        return stable_hash(
            self.platform,
            self.native_id or f"{self.post_url}|{self.author}|{self.text[:200]}",
        )


class SentimentResult(BaseModel):
    comment_hash: str
    label: SentimentLabel
    reason_ar: str
    confidence: float | None = None
    model: str = ""
    analyzed_at: datetime = Field(default_factory=utcnow)


class RunSummary(BaseModel):
    """What the pipeline accomplished; drives the email body."""

    run_id: int
    started_at: datetime
    finished_at: datetime | None = None
    followers_collected: int = 0
    followers_failed: int = 0
    news_collected: int = 0
    comments_collected: int = 0
    comments_analyzed: int = 0
    sentiment_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    @property
    def duration_secs(self) -> float:
        end = self.finished_at or utcnow()
        return (end - self.started_at).total_seconds()
