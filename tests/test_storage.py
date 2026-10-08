from datetime import datetime, timedelta, timezone

import pytest

from aljouf_report.models import (
    FollowerSnapshot,
    NewsArticle,
    SentimentResult,
    SocialComment,
)
from aljouf_report.storage import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


def _snapshot(**overrides) -> FollowerSnapshot:
    defaults = dict(
        entity_key="jsdo",
        entity_name_ar="المكتب الاستراتيجي لتطوير منطقة الجوف",
        entity_name_en="Jouf Strategic Development Office",
        owned=True,
        platform="instagram",
        handle="jouf_strategic_office",
        profile_url="https://www.instagram.com/jouf_strategic_office",
        followers=1000,
    )
    return FollowerSnapshot(**{**defaults, **overrides})


def _comment(text: str = "تعليق تجريبي", **overrides) -> SocialComment:
    defaults = dict(
        entity_key="jsdo",
        platform="instagram",
        text=text,
        author="someone",
        native_id="n1",
        published_at=datetime.now(timezone.utc),
    )
    return SocialComment(**{**defaults, **overrides})


def test_schema_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    Database(path)
    Database(path)  # Re-applying the schema must not raise.


def test_follower_upsert_is_keyed_on_run_entity_platform(db):
    run_id = db.start_run(datetime.now(timezone.utc))
    db.save_followers(run_id, [_snapshot(followers=1000)])
    db.save_followers(run_id, [_snapshot(followers=1500)])

    rows = db.follower_history()
    assert len(rows) == 1
    assert rows[0]["followers"] == 1500


def test_follower_history_is_ordered_oldest_first(db):
    now = datetime.now(timezone.utc)
    for week, followers in enumerate([100, 200, 300]):
        run_id = db.start_run(now - timedelta(days=7 * (2 - week)))
        db.save_followers(run_id, [_snapshot(followers=followers)])
        db.finish_run(run_id, now, "completed")

    assert [r["followers"] for r in db.follower_history()] == [100, 200, 300]


def test_news_dedupes_on_url(db):
    run_id = db.start_run(datetime.now(timezone.utc))
    article = NewsArticle(
        title="خبر",
        url="https://example.com/a",
        published_at=datetime.now(timezone.utc),
        keyword="الجوف",
    )
    db.save_news(run_id, [article])
    # Same URL with tracking params and a trailing slash is the same article.
    db.save_news(
        run_id,
        [article.model_copy(update={"url": "https://example.com/a/?utm_source=x"})],
    )
    assert len(db.recent_news()) == 1


def test_recent_news_respects_the_window(db):
    run_id = db.start_run(datetime.now(timezone.utc))
    now = datetime.now(timezone.utc)
    db.save_news(
        run_id,
        [
            NewsArticle(title="حديث", url="https://e.com/new", published_at=now),
            NewsArticle(
                title="قديم",
                url="https://e.com/old",
                published_at=now - timedelta(days=40),
            ),
        ],
    )
    titles = [row["title"] for row in db.recent_news(days=14)]
    assert titles == ["حديث"]


def test_sentiment_cache_prevents_reanalysis(db):
    run_id = db.start_run(datetime.now(timezone.utc))
    comment = _comment("ما شاء الله شغل مرتب")
    db.save_comments(run_id, [comment])

    assert len(db.comments_needing_sentiment()) == 1

    db.save_sentiments(
        [
            SentimentResult(
                comment_hash=comment.comment_hash,
                label="Positive",
                reason_ar="ثناء صريح على العمل.",
                confidence=0.95,
                model="test",
            )
        ]
    )

    # Already scored, so it must not be sent to the LLM again.
    assert db.comments_needing_sentiment() == []
    assert db.sentiment_counts() == {"Positive": 1}


def test_scored_comments_joins_sentiment(db):
    run_id = db.start_run(datetime.now(timezone.utc))
    comment = _comment("وش ذا الهبال")
    db.save_comments(run_id, [comment])
    db.save_sentiments(
        [
            SentimentResult(
                comment_hash=comment.comment_hash,
                label="Negative",
                reason_ar="انتقاد حاد بعامية سعودية.",
                model="test",
            )
        ]
    )

    rows = db.scored_comments()
    assert len(rows) == 1
    assert rows[0]["label"] == "Negative"
    assert rows[0]["reason_ar"] == "انتقاد حاد بعامية سعودية."
    assert rows[0]["text"] == "وش ذا الهبال"


def test_comment_hash_is_stable_across_instances():
    a = _comment("نفس النص", native_id="abc")
    b = _comment("نفس النص", native_id="abc")
    assert a.comment_hash == b.comment_hash

    c = _comment("نص مختلف", native_id="xyz")
    assert c.comment_hash != a.comment_hash


def test_table_rows_rejects_unknown_tables(db):
    with pytest.raises(ValueError):
        db.table_rows("sqlite_master")
