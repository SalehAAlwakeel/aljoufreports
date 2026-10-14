"""LinkedIn comment targeting must follow the lookback window, not a post cap."""

from datetime import datetime, timedelta, timezone

from aljouf_report.collectors.comments import CommentCollector
from aljouf_report.config import load_config


def _post(urn: str, when: str, comments: int) -> dict:
    return {
        "activity_urn": urn,
        "posted_at": {"date": when},
        "stats": {"comments": comments, "like": 3},
    }


def test_weekly_scrape_stops_at_seven_days_and_keeps_the_archive(monkeypatch, tmp_path):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "comments.db"))
    load_config.cache_clear()
    config = load_config()
    load_config.cache_clear()

    collector = CommentCollector(config, runner=None)
    age = datetime.now(timezone.utc) - collector._cutoff

    assert config.settings.windows.comments_lookback_days == 90
    assert config.settings.windows.comments_scrape_days == 7
    assert timedelta(days=6) <= age <= timedelta(days=8)


def test_targets_keep_every_post_with_comments_inside_the_window():
    cutoff = datetime(2026, 7, 13, tzinfo=timezone.utc)
    items = [
        _post("new", "2026-10-10 08:00:00", 1),
        _post("quiet", "2026-10-09 08:00:00", 0),
        _post("older", "2026-08-18 08:16:43", 2),
        _post("before-window", "2026-06-01 08:00:00", 5),
    ]
    # The quiet post and the one before the window must not be fetched.
    assert CommentCollector.linkedin_comment_targets(items, cutoff) == ["new", "older"]


def test_targets_skip_a_post_with_no_readable_date_only_when_it_has_no_comments():
    cutoff = datetime(2026, 7, 13, tzinfo=timezone.utc)
    items = [{"activity_urn": "undated", "stats": {"comments": 3}}]
    # No timestamp is kept: dropping it would lose the thread.
    assert CommentCollector.linkedin_comment_targets(items, cutoff) == ["undated"]
