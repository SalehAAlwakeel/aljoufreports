"""Synthetic data generator.

Lets you exercise storage, the dashboard and the email template before any
paid credential exists. Writes into the same tables the real pipeline uses, so
whatever renders here renders identically on live data.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

from .config import AppConfig, load_config
from .models import (
    DEMO_MODEL_TAG,
    FollowerSnapshot,
    NewsArticle,
    SentimentResult,
    SocialComment,
)
from .storage import Database

log = logging.getLogger(__name__)

# Realistic starting points and weekly growth rates per platform.
BASE_FOLLOWERS = {
    "instagram": (18_000, 90_000),
    "x": (12_000, 140_000),
    "youtube": (900, 14_000),
    "linkedin": (4_000, 60_000),
}

DEMO_COMMENTS: list[tuple[str, str, str]] = [
    # (text, label, reason_ar)
    (
        "ما شاء الله تبارك الله، مشروع الواجهة طلع تحفة والجوف تستحق كل خير 🌹",
        "Positive",
        "إعجاب صادق بالمشروع مع اعتزاز بالمنطقة.",
    ),
    (
        "يعطيكم العافية على الشفافية، أول مرة نشوف تقارير بهذا الوضوح",
        "Positive",
        "ثناء على الشفافية ووضوح التقارير.",
    ),
    (
        "يا سلام عليكم 👏👏 نفس الكلام من ثلاث سنوات وما تغير شي على الأرض",
        "Negative",
        "سخرية واضحة؛ المدح والتصفيق يخفيان شكوى من تكرار الوعود.",
    ),
    (
        "متى يفتح التقديم على الوظائف في المكتب؟",
        "Neutral",
        "استفسار محض عن موعد التقديم دون رأي.",
    ),
    (
        "وش ذا الهبال، اتصلت عشر مرات وما حد يرد 🤬",
        "Negative",
        "انتقاد حاد بعامية سعودية لضعف الخدمة وعدم الرد.",
    ),
    (
        "الله يصلح الحال، شوارعنا في سكاكا ما شافت سفلتة من زمان",
        "Negative",
        "شكوى من إهمال سفلتة الشوارع، والدعاء تعبير عن استياء.",
    ),
    (
        "تمام جبتوها، الفعالية كانت منظمة والتنظيم ممتاز",
        "Positive",
        "استحسان لتنظيم الفعالية بعامية سعودية.",
    ),
    (
        "وين نلقى تفاصيل أكثر عن الخطة؟ فيه رابط؟",
        "Neutral",
        "سؤال عن مصدر التفاصيل دون موقف.",
    ),
    (
        "ما شاء الله على الإنجازات 😏 بس وين المستشفى اللي وعدتونا فيه؟",
        "Negative",
        "سخرية؛ المدح الظاهري يسبق شكوى من وعد لم يُنفذ.",
    ),
    (
        "فخورين بالجوف وبالتطور اللي يصير، وفقكم الله",
        "Positive",
        "اعتزاز بالمنطقة ودعاء بالتوفيق.",
    ),
    (
        "فشلة والله، التطبيق ما يفتح عندي من أسبوع",
        "Negative",
        "شكوى من عطل مستمر في التطبيق بلهجة ساخطة.",
    ),
    (
        "هل الفعاليات مفتوحة للعائلات ولا بس للأفراد؟",
        "Neutral",
        "استفسار تنظيمي عن شروط الحضور.",
    ),
    (
        "الله يعطيكم العافية، شغل مرتب وواضح ومستمرين بإذن الله",
        "Positive",
        "ثناء صريح على جودة العمل مع دعاء بالشكر.",
    ),
    (
        "صراحة التعامل مع الموظفين كان راقي وسريع، شكراً لكم",
        "Positive",
        "إشادة بتعامل الموظفين وسرعة الخدمة.",
    ),
    (
        "كل سنة نفس الحكي وما شفنا ولا مشروع خلص في وقته",
        "Negative",
        "انتقاد لتكرار الوعود وتأخر إنجاز المشاريع.",
    ),
    (
        "تم التسجيل، شكراً على سهولة الإجراءات 👍",
        "Positive",
        "شكر على سهولة إجراءات التسجيل.",
    ),
    (
        "ممكن تنزلون التقرير بصيغة PDF؟",
        "Neutral",
        "طلب تقني لتوفير التقرير بصيغة أخرى.",
    ),
    (
        "حسبي الله على كل مسؤول ما يسمع صوت المواطن",
        "Negative",
        "تعبير عن استياء شديد من عدم الاستجابة للمواطنين.",
    ),
    (
        "من جد الجوف تغيرت في السنتين الأخيرة، الله يتمم على خير",
        "Positive",
        "ملاحظة تحسن ملموس في المنطقة مع دعاء بالتمام.",
    ),
    (
        "تسلم ايديكم على التنظيم، الفعالية كانت ممتعة للعائلة",
        "Positive",
        "شكر على التنظيم وإشادة بملاءمة الفعالية للعائلات.",
    ),
    (
        "ما شاء الله، الزيتون عندنا صار له اسم عالمي 🫒",
        "Positive",
        "اعتزاز بوصول منتج المنطقة إلى شهرة عالمية.",
    ),
    (
        "أخيراً صار فيه حدائق للأطفال، شكراً من القلب",
        "Positive",
        "امتنان صادق لتوفير حدائق للأطفال.",
    ),
    (
        "بالتوفيق للمكتب، نتابعكم ونشوف الجهود",
        "Positive",
        "تشجيع ودعم مع متابعة إيجابية للجهود.",
    ),
    (
        "الموقع الإلكتروني زحمة وما يفتح، صلحوه الله يهديكم",
        "Negative",
        "شكوى من بطء الموقع وتعذر الوصول إليه.",
    ),
    (
        "من سنة أرفع شكوى وما حد تحرك ساكن",
        "Negative",
        "استياء من تجاهل الشكاوى لمدة عام كامل.",
    ),
    (
        "ليه كل المشاريع في سكاكا وباقي المحافظات منسية؟",
        "Negative",
        "انتقاد لتركيز المشاريع في مدينة واحدة وإغفال غيرها.",
    ),
    (
        "ما شاء الله شكل الصور حلو 🙄 بس الواقع شي ثاني تماماً",
        "Negative",
        "سخرية؛ المدح الظاهري للصور يقابله نفي للواقع.",
    ),
    (
        "خدمة العملاء صفر، الواتساب ما يرد والهاتف مشغول دايم",
        "Negative",
        "شكوى من تعذر التواصل عبر جميع القنوات.",
    ),
    (
        "سفلتة الطريق للمخرج الشمالي متى؟ صار لها سنتين وعد",
        "Negative",
        "مطالبة بتنفيذ وعد سفلتة متأخر منذ سنتين.",
    ),
    (
        "يا أخي ردوا على الناس، احترموا وقتنا",
        "Negative",
        "مطالبة غاضبة بالرد واحترام وقت المتعاملين.",
    ),
    (
        "وعود وبس، تعبنا والله 💔",
        "Negative",
        "إحباط من تكرار الوعود دون تنفيذ.",
    ),
    (
        "فيه وظائف للخريجين الجدد؟",
        "Neutral",
        "استفسار عن توفر وظائف للخريجين.",
    ),
    (
        "وين مقر المكتب بالضبط؟",
        "Neutral",
        "سؤال عن الموقع الجغرافي للمقر.",
    ),
    (
        "هل التسجيل متاح للمقيمين ولا للسعوديين بس؟",
        "Neutral",
        "استفسار عن شروط الأهلية للتسجيل.",
    ),
    (
        "متى ينزل التقرير السنوي؟",
        "Neutral",
        "سؤال عن موعد نشر التقرير السنوي.",
    ),
    (
        "ايش شروط الاستثمار الزراعي في المنطقة؟",
        "Neutral",
        "استفسار عن شروط الاستثمار الزراعي.",
    ),
    (
        "هل فيه مواصلات توصل للموقع؟",
        "Neutral",
        "سؤال تنظيمي عن توفر المواصلات.",
    ),
    (
        "ممكن رقم تواصل مباشر؟",
        "Neutral",
        "طلب وسيلة تواصل مباشرة.",
    ),
]

DEMO_NEWS: list[tuple[str, str, str, str]] = [
    # (title, domain, keyword, language)
    (
        "أمير منطقة الجوف يفتتح المرحلة الأولى من مشروع تطوير وسط سكاكا",
        "spa.gov.sa",
        "الجوف",
        "ar",
    ),
    (
        "Al Jouf region reports record olive harvest as agri-investment expands",
        "arabnews.com",
        "Al Jouf",
        "en",
    ),
    (
        "إطلاق حزمة فرص استثمارية جديدة في منطقة الجوف بقيمة مليار ريال",
        "argaam.com",
        "الجوف",
        "ar",
    ),
    (
        "Saudi Arabia's regional development offices publish 2026 quarterly scorecards",
        "saudigazette.com.sa",
        "Saudi Arabia",
        "en",
    ),
    (
        "بلدية سكاكا تنهي أعمال السفلتة في عدة أحياء شمال المدينة",
        "sabq.org",
        "سكاكا",
        "ar",
    ),
    (
        "مهرجان الزيتون في الجوف يجذب أكثر من 120 ألف زائر هذا العام",
        "okaz.com.sa",
        "الجوف",
        "ar",
    ),
    (
        "Sakaka airport expansion study moves to design phase",
        "zawya.com",
        "Sakaka",
        "en",
    ),
    (
        "السعودية تعلن عن خطة لربط مناطق الشمال بشبكة سكك حديدية",
        "aleqt.com",
        "السعودية",
        "ar",
    ),
    (
        "Vision 2030 midpoint: how Saudi regions compare on tourism growth",
        "reuters.com",
        "Saudi Arabia",
        "en",
    ),
    (
        "ورشة عمل في الجوف لتأهيل الكوادر المحلية في قطاع السياحة",
        "alyaum.com",
        "الجوف",
        "ar",
    ),
    (
        "توسعة شبكة المياه في محافظات منطقة الجوف تدخل حيز التنفيذ",
        "spa.gov.sa",
        "الجوف",
        "ar",
    ),
    (
        "Northern Saudi provinces attract new logistics investment",
        "thenationalnews.com",
        "Saudi Arabia",
        "en",
    ),
]


def seed(config: AppConfig | None = None, weeks: int = 8, seed_value: int = 20260101) -> int:
    """Populate `weeks` historical runs. Returns the latest run id."""
    config = config or load_config()
    db = Database(config.db_path)
    rng = random.Random(seed_value)

    entities = config.accounts.entities
    platforms = config.settings.followers.platforms

    # Per entity/platform starting follower count and weekly growth rate.
    baseline: dict[tuple[str, str], tuple[int, float]] = {}
    for entity in entities:
        for platform in platforms:
            if not entity.account(platform):
                continue
            low, high = BASE_FOLLOWERS[platform]
            start = rng.randint(low, high)
            # Our own account grows a little faster than the benchmark set,
            # which makes the comparison chart meaningful in the demo.
            growth = rng.uniform(0.012, 0.028) if entity.owned else rng.uniform(0.002, 0.016)
            baseline[(entity.key, platform)] = (start, growth)

    latest_run_id = 0
    now = datetime.now(timezone.utc)

    for week in range(weeks):
        week_offset = weeks - 1 - week
        run_time = now - timedelta(days=7 * week_offset, hours=rng.randint(0, 5))
        run_id = db.start_run(run_time)
        latest_run_id = run_id

        # -- followers --
        snapshots = []
        for entity in entities:
            for platform in platforms:
                account = entity.account(platform)
                if not account:
                    continue
                start, growth = baseline[(entity.key, platform)]
                followers = int(start * ((1 + growth) ** week) * rng.uniform(0.997, 1.003))
                snapshots.append(
                    FollowerSnapshot(
                        entity_key=entity.key,
                        entity_name_ar=entity.name_ar,
                        entity_name_en=entity.name_en,
                        owned=entity.owned,
                        platform=platform,
                        handle=account.handle,
                        profile_url=account.url,
                        followers=followers,
                        captured_at=run_time,
                    )
                )
        db.save_followers(run_id, snapshots)

        # Only the most recent run gets news and comments, matching how the
        # real pipeline keeps a rolling window rather than full history.
        is_latest = week == weeks - 1
        if is_latest:
            db.save_news(run_id, _demo_news(config, now, rng))
            comments, sentiments = _demo_comments(config, now, rng)
            db.save_comments(run_id, comments)
            db.save_sentiments(sentiments)

        db.finish_run(run_id, run_time + timedelta(minutes=4), "completed", notes="demo seed")
        log.info("Seeded demo run %d (week -%d)", run_id, week_offset)

    log.info(
        "Demo data ready in %s. Launch the dashboard to view it.", config.db_path
    )
    return latest_run_id


def _demo_news(config: AppConfig, now: datetime, rng: random.Random) -> list[NewsArticle]:
    window = config.settings.windows.news_lookback_days
    articles = []
    for index, (title, domain, keyword, language) in enumerate(DEMO_NEWS):
        published = now - timedelta(
            days=rng.uniform(0, window - 0.5), hours=rng.randint(0, 23)
        )
        articles.append(
            NewsArticle(
                title=title,
                url=f"https://{domain}/demo/article-{index + 1}",
                published_at=published,
                source=domain,
                domain=domain,
                keyword=keyword,
                language=language,
                collector=rng.choice(["google_news", "gdelt"]),
                snippet="بيانات تجريبية لعرض لوحة المعلومات." if language == "ar" else "Demo data.",
                fetched_at=now,
            )
        )
    return articles


def _demo_comments(
    config: AppConfig, now: datetime, rng: random.Random
) -> tuple[list[SocialComment], list[SentimentResult]]:
    window = config.settings.windows.comments_lookback_days
    owned = config.accounts.owned or config.accounts.entities[:1]
    platforms = config.settings.comments.platforms

    comments: list[SocialComment] = []
    sentiments: list[SentimentResult] = []

    # Each sample text is used exactly once. Repeating them to inflate volume
    # made the dashboard look like it had a data-duplication bug.
    for index, (text, label, reason) in enumerate(DEMO_COMMENTS):
        entity = rng.choice(owned)
        platform = rng.choice([p for p in platforms if entity.account(p)] or platforms)
        published = now - timedelta(
            days=rng.uniform(0, window - 0.2), hours=rng.randint(0, 23)
        )
        comment = SocialComment(
            entity_key=entity.key,
            platform=platform,
            text=text,
            # "demo_" prefixes make it obvious at a glance that this is not
            # production data.
            author=f"demo_user_{index:02d}",
            post_url=f"https://example.com/{platform}/post/{index:02d}",
            comment_url=f"https://example.com/{platform}/comment/{index:02d}",
            native_id=f"demo-{index}",
            published_at=published,
            likes=rng.randint(0, 180),
            fetched_at=now,
        )
        comments.append(comment)
        sentiments.append(
            SentimentResult(
                comment_hash=comment.comment_hash,
                label=label,  # type: ignore[arg-type]
                reason_ar=reason,
                confidence=round(rng.uniform(0.86, 0.99), 2),
                model=DEMO_MODEL_TAG,
                analyzed_at=now,
            )
        )
    return comments, sentiments
