"""Report helpers must stay correct when a collection run fails."""

from datetime import datetime, timedelta, timezone

import pytest

from aljouf_report.config import load_config
from aljouf_report.models import FollowerSnapshot
from aljouf_report.pipeline import Pipeline


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "pipeline.db"))
    load_config.cache_clear()
    config = load_config()
    load_config.cache_clear()
    return Pipeline(config)


def _snapshot(followers, platform="instagram") -> FollowerSnapshot:
    return FollowerSnapshot(
        entity_key="jsdo",
        entity_name_ar="المكتب الاستراتيجي لتطوير منطقة الجوف",
        entity_name_en="Jouf Strategic Development Office",
        owned=True,
        platform=platform,
        handle="jouf_strategic_office",
        profile_url="https://www.instagram.com/jouf_strategic_office",
        followers=followers,
        error=None if followers is not None else "APIFY_TOKEN is not set.",
    )


def test_deltas_compare_the_two_most_recent_successful_runs(pipeline):
    now = datetime.now(timezone.utc)
    for offset, followers in ((14, 1000), (7, 1200)):
        run_id = pipeline.db.start_run(now - timedelta(days=offset))
        pipeline.db.save_followers(run_id, [_snapshot(followers)])
        pipeline.db.finish_run(run_id, now, "completed")

    rows = pipeline.owned_follower_deltas()
    counted = [row for row in rows if row.get("followers") is not None]
    assert len(counted) == 1
    assert counted[0]["followers"] == 1200
    assert counted[0]["delta"] == 200
    listed = {row["platform"] for row in rows if row.get("followers") is None}
    assert listed == {"whatsapp", "snapchat"}


def test_failed_run_does_not_become_the_latest(pipeline):
    """A run with no counts must not zero out the report."""
    now = datetime.now(timezone.utc)
    for offset, followers in ((14, 1000), (7, 1200)):
        run_id = pipeline.db.start_run(now - timedelta(days=offset))
        pipeline.db.save_followers(run_id, [_snapshot(followers)])
        pipeline.db.finish_run(run_id, now, "completed")

    # A run where Apify was unavailable: rows exist, counts are NULL.
    failed = pipeline.db.start_run(now)
    pipeline.db.save_followers(failed, [_snapshot(None)])
    pipeline.db.finish_run(failed, now, "completed_with_warnings")

    rows = pipeline.owned_follower_deltas()
    counted = [row for row in rows if row.get("followers") is not None]
    assert len(counted) == 1
    assert counted[0]["followers"] == 1200, "should fall back to the last good run"
    assert counted[0]["delta"] == 200


def test_deltas_are_none_on_the_first_run(pipeline):
    run_id = pipeline.db.start_run(datetime.now(timezone.utc))
    pipeline.db.save_followers(run_id, [_snapshot(1000)])

    rows = pipeline.owned_follower_deltas()
    assert rows[0]["delta"] is None


def test_no_successful_runs_yields_no_rows(pipeline):
    run_id = pipeline.db.start_run(datetime.now(timezone.utc))
    pipeline.db.save_followers(run_id, [_snapshot(None)])
    assert pipeline.owned_follower_deltas() == []


def test_news_counts_split_multi_keyword_articles(pipeline):
    from aljouf_report.models import NewsArticle

    run_id = pipeline.db.start_run(datetime.now(timezone.utc))
    pipeline.db.save_news(
        run_id,
        [
            NewsArticle(
                title="خبر عن الجوف وسكاكا",
                url="https://example.com/1",
                published_at=datetime.now(timezone.utc),
                keyword="الجوف, سكاكا",
            ),
            NewsArticle(
                title="خبر عن الجوف",
                url="https://example.com/2",
                published_at=datetime.now(timezone.utc),
                keyword="الجوف",
            ),
        ],
    )

    assert pipeline.news_counts_by_keyword() == {"الجوف": 2, "سكاكا": 1}


def test_public_pages_publish_follower_counts():
    from aljouf_report.collectors.public_counts import (
        parse_snapchat_subscribers,
        parse_whatsapp_followers,
    )

    assert parse_whatsapp_followers("Channel • 354 followers") == 354
    page = '"publicProfileInfo":{"username":"jsdo_ksa","subscriberCount":"0","bio":""}'
    assert parse_snapchat_subscribers(page) == 0
