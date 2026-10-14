"""The weekly briefing stays short and leads with the three local stories."""

from datetime import datetime, timezone

from aljouf_report.config import load_config
from aljouf_report.models import RunSummary
from aljouf_report.notify.mailer import (
    WeeklyMailer,
    _change_percent,
    _comparison_window,
    _overall_change,
    _select_news,
)


def _summary() -> RunSummary:
    now = datetime.now(timezone.utc)
    return RunSummary(
        run_id=7,
        started_at=now,
        finished_at=now,
        sentiment_counts={"Positive": 1, "Negative": 1, "Neutral": 2},
    )


def test_change_percent_ignores_a_first_count():
    assert _change_percent({"followers": 6101, "delta": None}) is None
    assert _change_percent({"followers": 100, "delta": 0}) == 0
    assert _change_percent({"followers": 110, "delta": 10}) == 10


def test_overall_change_skips_platforms_without_a_baseline():
    rows = [
        {"followers": 10, "delta": 0},
        {"followers": 6101, "delta": None},
        {"followers": 200, "delta": 20},
    ]
    assert _overall_change(rows) == 20 / 190 * 100


def test_news_is_two_local_one_saudi_and_one_international():
    rows = [
        {"title": "Sakaka", "keyword": "سكاكا", "language": "ar", "source": "سبق", "published_at": "2026-10-13"},
        {"title": "Qurayyat", "keyword": "القريات", "language": "ar", "source": "عكاظ", "published_at": "2026-10-12"},
        {"title": "Older local", "keyword": "الجوف", "language": "ar", "source": "واس", "published_at": "2026-10-01"},
        {"title": "احتجاز أميركيين في السعودية", "keyword": "ترند السعودية", "language": "ar", "source": "العربية", "published_at": "2026-10-14"},
        {"title": "السعودية تنجح في فصل التوأم", "keyword": "ترند السعودية", "language": "ar", "source": "العربية", "published_at": "2026-10-13"},
        {"title": "Houthi attack on Saudi Arabia", "keyword": "Saudi Arabia", "language": "en", "source": "CNN", "domain": "cnn.com", "published_at": "2026-10-15"},
        {"title": "رسالة تهنئة إلى السعودية بعد الإنجاز", "keyword": "Saudi Arabia", "language": "ar", "source": "CNN", "domain": "cnn.com", "published_at": "2026-10-14"},
        {"title": "Saudi Arabia wins the Gulf Cup", "keyword": "Saudi Arabia", "language": "en", "source": "BBC", "domain": "bbc.com", "published_at": "2026-10-12"},
        {"title": "Reuters story", "keyword": "Saudi Arabia", "language": "en", "source": "Reuters", "domain": "reuters.com", "published_at": "2026-10-16"},
    ]
    picked = [row["title"] for row in _select_news(rows)]
    assert picked == [
        "Sakaka",
        "Qurayyat",
        "السعودية تنجح في فصل التوأم",
        "رسالة تهنئة إلى السعودية بعد الإنجاز",
    ]


def test_comparison_keeps_two_above_and_one_below():
    board = [
        {"name": "الأكبر", "owned": False, "followers": 100},
        {"name": "فوق ٢", "owned": False, "followers": 80},
        {"name": "فوق ١", "owned": False, "followers": 60},
        {"name": "الجوف", "owned": True, "followers": 40},
        {"name": "تحت", "owned": False, "followers": 20},
        {"name": "الأصغر", "owned": False, "followers": 10},
    ]
    assert [row["name"] for row in _comparison_window(board)] == ["فوق ٢", "فوق ١", "الجوف", "تحت"]


def test_briefing_shows_gain_comments_and_neighbors():
    mailer = WeeklyMailer(load_config())
    html, images = mailer.render_html(
        _summary(),
        owned_followers=[
            {"platform": "instagram", "followers": 10, "delta": 0},
            {"platform": "x", "followers": 110, "delta": 10},
            {"platform": "linkedin", "followers": 6101, "delta": None},
        ],
        top_negative=[],
        week_comments=[
            {"platform": "x", "label": "Positive", "text": "مبادرة رائعة من سمو أمير منطقة الجوف"},
            {"platform": "x", "label": "Positive", "text": "جهودكم تذكر فتشكر"},
            {"platform": "linkedin", "label": "Neutral", "text": "هل سيتم عقد اللقاء في غرفة الجوف قريباً"},
        ],
        week_news=[
            {"title": "خبر سكاكا", "keyword": "سكاكا", "language": "ar", "url": "https://example.com/1", "source": "سبق", "published_at": "2026-10-14"},
            {"title": "خبر القريات", "keyword": "القريات", "language": "ar", "url": "https://example.com/2", "source": "عكاظ", "published_at": "2026-10-13"},
            {"title": "السعودية تنجح في استثمار جديد", "keyword": "ترند السعودية", "language": "ar", "url": "https://example.com/3", "source": "العربية", "published_at": "2026-10-14"},
            {"title": "السعودية تفوز بكأس الخليج", "keyword": "Saudi Arabia", "language": "ar", "url": "https://example.com/4", "source": "BBC", "domain": "bbc.com", "published_at": "2026-10-14"},
        ],
        ranking=[
            {"name": "هيئة حائل", "owned": False, "followers": 27000},
            {"name": "مكتب جازان", "owned": False, "followers": 16000},
            {"name": "الجوف", "owned": True, "followers": 9991},
            {"name": "الحدود الشمالية", "owned": False, "followers": 2188},
        ],
    )
    assert html.index("خبر سكاكا") < html.index("ملخص الأسبوع")
    assert "السعودية تنجح في استثمار جديد" in html and "السعودية تفوز بكأس الخليج" in html
    assert "نسبة التعليقات الإيجابية" in html
    assert "متابعون إضافيون" in html
    assert "+10" in html
    assert "نسبة الزيادة" not in html
    assert "هيئة حائل" in html and "الحدود الشمالية" in html
    assert "مبادرة رائعة" in html
    assert ">التعليقات المحايدة<" in html
    assert ">التعليقات السلبية<" not in html
    assert "جديد" in html
    assert "cid:sentiment" not in html
    assert "cid:followers" not in html
    assert images == {}


def test_brief_stays_short_and_links_the_download():
    mailer = WeeklyMailer(load_config())
    html = mailer.render_brief(
        _summary(),
        owned_followers=[
            {"platform": "instagram", "followers": 10, "delta": 0},
            {"platform": "x", "followers": 110, "delta": 10},
        ],
        top_negative=[],
        week_comments=[
            {"platform": "x", "label": "Positive", "text": "مبادرة رائعة من سمو أمير منطقة الجوف"},
        ],
        week_news=[
            {"title": "خبر سكاكا", "keyword": "سكاكا", "language": "ar", "url": "https://example.com/1", "source": "سبق", "published_at": "2026-10-14"},
        ],
        ranking=[
            {"name": "هيئة حائل", "owned": False, "followers": 27000},
            {"name": "الجوف", "owned": True, "followers": 9991},
        ],
        download_href="aljouf-weekly-report.pdf",
    )
    assert len(html.encode("utf-8")) < 12000
    assert "تحميل التقرير" in html
    assert "aljouf-weekly-report.pdf" in html
    assert "خبر سكاكا" in html
    message = mailer.build_message(
        _summary(),
        html,
        report_name="aljouf-weekly-report.pdf",
        report_bytes=b"%PDF-1.4",
        report_subtype="pdf",
    )
    names = [part.get_filename() for part in message.iter_attachments()]
    assert "aljouf-weekly-report.pdf" in names
