"""Headlines must come from a reputable outlet and actually be about the place."""

from aljouf_report.collectors.news import article_allowed, search_query
from aljouf_report.config import NewsKeyword, load_config

TRUSTED = ["سبق", "عكاظ", "أرقام", "reuters", "اليوم", "صحيفة اليوم"]
BLOCKED = ["لحظات", "lahzat"]
TOWNS = ["سكاكا", "الجوف", "القريات"]


def allow(**overrides):
    fields = {
        "title": "أمطار على سكاكا",
        "source": "سبق",
        "domain": "news.google.com",
        "scope": "local",
        "term": "سكاكا",
        "require_region": False,
        "trusted": TRUSTED,
        "blocked": BLOCKED,
        "local_terms": TOWNS,
    }
    fields.update(overrides)
    return article_allowed(**fields)


def test_lahzat_hong_kong_and_egypt_stories_are_dropped():
    hong_kong = "شرطة هونغ كونغ تستعيد 30 مليون دولار من الذهب المسروق"
    egypt = "الداخلية المصرية تضبط مزوري أختام"
    assert allow(title=hong_kong, source="لحظات نيوز") is False
    assert allow(title=egypt, source="لحظات نيوز") is False
    # Even a weather post that really names Sakaka is dropped: the outlet is blocked.
    assert allow(title="أمطار غزيرة على سكاكا", source="لحظات نيوز") is False


def test_local_story_from_a_major_outlet_is_kept():
    assert allow(title="أمير الجوف يزور سكاكا", source="عكاظ") is True
    assert (
        allow(
            title="Al-Jouf Region Exceeds Saudi Green Initiative Target",
            source="وكالة الأنباء السعودية",
            term="Al Jouf",
            trusted=[*TRUSTED, "وكالة الأنباء السعودية"],
        )
        is True
    )


def test_local_story_must_name_the_town_in_the_title():
    assert allow(title="شرطة هونغ كونغ تستعيد ذهباً مسروقاً", source="عكاظ") is False


def test_generic_town_must_also_mention_the_region():
    assert (
        allow(
            title="أعمال في الحديثة",
            source="سبق",
            term="الحديثة",
            require_region=True,
        )
        is False
    )
    assert (
        allow(
            title="أعمال في الحديثة بالجوف",
            source="سبق",
            term="الحديثة",
            require_region=True,
        )
        is True
    )


def test_yemen_jouf_is_not_al_jouf():
    assert (
        allow(title="اشتباكات في الجوف اليمنية", source="عكاظ", term="الجوف") is False
    )
    assert allow(title="شركة الجوف الزراعية توزع أرباحاً", source="أرقام", term="الجوف") is True


def test_national_trend_must_name_saudi_arabia_and_a_known_outlet():
    assert (
        allow(
            title="Cabinet approves new housing plan in Saudi Arabia",
            source="reuters.com",
            domain="reuters.com",
            scope="national",
            term="Saudi Arabia",
        )
        is True
    )
    assert (
        allow(
            title="Hong Kong police recover stolen gold",
            source="reuters.com",
            domain="reuters.com",
            scope="national",
            term="Saudi Arabia",
        )
        is False
    )
    assert (
        allow(
            title="Saudi Arabia signs a trade deal",
            source="wishtv.com",
            domain="wishtv.com",
            scope="national",
            term="Saudi Arabia",
        )
        is False
    )


def test_al_youm_does_not_match_the_egyptian_paper():
    assert allow(title="خبر عن سكاكا", source="اليوم") is True
    assert allow(title="خبر عن سكاكا", source="اليوم السابع") is False
    assert allow(title="خبر عن سكاكا", source="صحيفة اليوم") is True


def test_empty_publisher_is_rejected():
    assert allow(title="أمير الجوف في سكاكا", source="", domain="news.google.com") is False


def test_search_query_ties_generic_towns_to_the_region():
    generic = NewsKeyword(query="الوادي", label="الوادي", language="ar", require_region=True)
    assert search_query(generic, 14) == "الوادي الجوف when:14d"
    named = NewsKeyword(query="دومة الجندل", label="دومة الجندل", language="ar")
    assert search_query(named, 14) == '"دومة الجندل" when:14d'


def test_settings_list_the_governorates_and_block_lahzat():
    news = load_config().settings.news
    labels = {item.label for item in news.keywords}
    for town in ("سكاكا", "القريات", "دومة الجندل", "طبرجل", "صوير", "أبو عجرم"):
        assert town in labels
    assert any(item.scope == "national" and item.query == "السعودية" for item in news.keywords)
    assert any("لحظات" in name for name in news.blocked_sources)
    assert any("سبق" in name for name in news.trusted_sources)
