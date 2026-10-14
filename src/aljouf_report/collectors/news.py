"""News and trend monitoring.

Both sources are keyless public endpoints, so news collection works before any
paid credential is configured:

* Google News RSS -- broad coverage, native Arabic and English editions.
* GDELT 2.0 Doc API -- global media monitoring, good for trend/tone context.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import httpx

from ..config import AppConfig, NewsKeyword
from ..models import NewsArticle, utcnow
from .extract import parse_datetime

log = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT throttles anonymous clients and answers 429 beyond that. Pace requests
# rather than burning retries. The documented limit is one query per five
# seconds, but in practice that still gets throttled on about half the keywords,
# so pace well clear of it: a handful of extra seconds is free next to an
# eleven-minute run, and GDELT is the only source that finds non-headline items.
GDELT_MIN_INTERVAL_SECS = 10.0
GDELT_MAX_ATTEMPTS = 4

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Google News locale per keyword language; both target Saudi Arabia.
LOCALES = {
    "ar": {"hl": "ar", "gl": "SA", "ceid": "SA:ar"},
    "en": {"hl": "en-SA", "gl": "SA", "ceid": "SA:en"},
}

# GDELT reports languages as English names ("Arabic"), Google News as ISO
# codes ("ar"). Normalise so the dashboard language filter has one vocabulary.
LANGUAGE_CODES = {
    "arabic": "ar",
    "english": "en",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "russian": "ru",
    "turkish": "tr",
    "urdu": "ur",
    "hindi": "hi",
    "persian": "fa",
    "chinese": "zh",
    "korean": "ko",
    "japanese": "ja",
}


def _language_code(value: str | None, fallback: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return fallback
    if len(text) == 2:
        return text
    return LANGUAGE_CODES.get(text, text[:2])


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


# "اليوم" must match the Saudi paper exactly. A substring match also accepts
# Egypt's اليوم السابع, which is how foreign stories were getting in.
_EXACT_PUBLISHERS = {"اليوم"}
_NATIONAL_MARKERS = ("السعود", "saudi")
_REGION_MARKERS = ("الجوف", "al jouf", "al-jouf", "aljouf")
_FOREIGN_JOUF = ("اليمن", "yemen", "ليبيا", "libya")


def search_query(keyword: NewsKeyword, days: int) -> str:
    """Google News query. Generic town names are tied to الجوف."""
    text = keyword.query.strip()
    if " " in text and not (text.startswith('"') and text.endswith('"')):
        text = f'"{text}"'
    folded = text.casefold()
    if keyword.require_region and "الجوف" not in text and "al jouf" not in folded:
        text = f"{text} الجوف"
    return f"{text} when:{days}d"


def _normalize(text: str) -> str:
    """Fold case, Arabic letter variants, and hyphens so Al-Jouf matches Al Jouf."""
    text = (text or "").casefold()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه").replace("ى", "ي")
    text = re.sub(r"[\u064b-\u0652\u0670\u0640]", "", text)
    text = text.replace("-", " ").replace("–", " ").replace("—", " ")
    return re.sub(r"\s+", " ", text).strip()


def _has(text: str, needle: str) -> bool:
    folded = _normalize(needle)
    return bool(folded) and folded in _normalize(text)


def _publisher_hit(source: str, domain: str, needle: str) -> bool:
    source_f = _normalize(source)
    domain_f = _normalize(domain)
    needle_f = _normalize(needle)
    if not needle_f:
        return False
    if needle_f in domain_f or source_f == needle_f:
        return True
    if needle_f in _EXACT_PUBLISHERS:
        return False
    if len(needle_f) <= 3:
        return source_f.startswith(needle_f + " ") or source_f.endswith(" " + needle_f)
    return needle_f in f"{source_f} {domain_f}"


def matches_list(source: str, domain: str, names: list[str]) -> bool:
    return any(_publisher_hit(source, domain, name) for name in names)


def article_allowed(
    *,
    title: str,
    source: str,
    domain: str,
    scope: str,
    term: str,
    require_region: bool,
    trusted: list[str],
    blocked: list[str],
    local_terms: list[str] | None = None,
) -> bool:
    """Keep a headline only when a reputable outlet actually wrote about the place.

    Local stories must name the town in the title. National stories must name
    Saudi Arabia. Blocked outlets (لحظات نيوز) are dropped even when the title
    matches, because they attach Al Jouf town names to unrelated world news.
    """
    if matches_list(source, domain, blocked):
        return False
    if not matches_list(source, domain, trusted):
        return False

    if scope == "national":
        return any(_has(title, marker) for marker in _NATIONAL_MARKERS)

    if not term or not _has(title, term):
        return False
    if require_region and not any(
        _has(title, marker) for marker in (*_REGION_MARKERS, *_NATIONAL_MARKERS)
    ):
        return False
    # الجوف is also a governorate in Yemen and a region in Libya.
    if any(_has(title, marker) for marker in _FOREIGN_JOUF):
        towns = [
            name
            for name in (local_terms or [])
            if _normalize(name) not in {"الجوف", "al jouf"}
        ]
        if not any(_has(title, name) for name in towns):
            return False
    return True


class NewsCollector:
    def __init__(self, config: AppConfig):
        self.config = config
        self.errors: list[str] = []
        self._last_gdelt_call = 0.0

    @property
    def _lookback_days(self) -> int:
        return self.config.settings.windows.news_lookback_days

    @property
    def _cutoff(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=self._lookback_days)

    def collect(self) -> list[NewsArticle]:
        sources = self.config.settings.news.sources
        articles: list[NewsArticle] = []

        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for keyword in self.config.settings.news.keywords:
                if "google_news" in sources:
                    articles.extend(self._google_news(client, keyword))
                # GDELT is paced at 10s per query. Town names are covered by
                # Google News; GDELT is only used for national trending news.
                if "gdelt" in sources and keyword.scope == "national":
                    articles.extend(self._gdelt(client, keyword))

        deduped = self._dedupe(articles)
        log.info(
            "News: %d articles after dedupe (last %d days)",
            len(deduped),
            self._lookback_days,
        )
        return deduped

    # -- Google News RSS -------------------------------------------------- #
    def _google_news(
        self, client: httpx.Client, keyword: NewsKeyword
    ) -> list[NewsArticle]:
        import feedparser

        locale = LOCALES.get(keyword.language, LOCALES["en"])
        query = search_query(keyword, self._lookback_days)
        url = (
            f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}"
            f"&hl={locale['hl']}&gl={locale['gl']}&ceid={locale['ceid']}"
        )

        try:
            response = client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"news:google_news:{keyword.label}: {exc}"
            log.warning(msg)
            self.errors.append(msg)
            return []

        feed = feedparser.parse(response.content)
        articles: list[NewsArticle] = []
        for entry in feed.entries[: self.config.settings.news.max_per_keyword]:
            link = (entry.get("link") or "").strip()
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue

            published = parse_datetime(
                entry.get("published") or entry.get("updated")
            )
            if published and published < self._cutoff:
                continue

            # Google appends " - Publisher" to RSS titles; the publisher is also
            # available structurally, so strip the suffix for a clean title.
            source = ""
            if isinstance(entry.get("source"), dict):
                source = (entry["source"].get("title") or "").strip()
            if source and title.endswith(f" - {source}"):
                title = title[: -len(f" - {source}")]

            if not self._allowed(keyword, title, source, _domain(link)):
                continue

            articles.append(
                NewsArticle(
                    title=title,
                    url=link,
                    published_at=published,
                    source=source or _domain(link),
                    domain=_domain(link),
                    keyword=keyword.label,
                    language=keyword.language,
                    collector="google_news",
                    snippet=(entry.get("summary") or "")[:500],
                    fetched_at=utcnow(),
                )
            )

        log.info("News: google_news '%s' -> %d", keyword.label, len(articles))
        return articles

    # -- GDELT ------------------------------------------------------------ #
    def _gdelt(self, client: httpx.Client, keyword: NewsKeyword) -> list[NewsArticle]:
        # GDELT needs quoting for multi-word phrases. Without a sourcelang
        # filter, "Saudi Arabia" matches Bengali/Korean/Telugu coverage that is
        # unusable for this report, so pin each query to its keyword language.
        query = keyword.query if " " not in keyword.query else f'"{keyword.query}"'
        sourcelang = {"ar": "arabic", "en": "english"}.get(keyword.language)
        if sourcelang:
            query += f" sourcelang:{sourcelang}"

        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(min(self.config.settings.news.max_per_keyword, 250)),
            "timespan": f"{self._lookback_days}d",
            "sort": "DateDesc",
        }

        payload = self._gdelt_request(client, params, keyword.label)
        if payload is None:
            return []

        articles: list[NewsArticle] = []
        for entry in payload.get("articles") or []:
            link = (entry.get("url") or "").strip()
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue

            published = parse_datetime(entry.get("seendate"))
            if published and published < self._cutoff:
                continue

            source = entry.get("domain") or _domain(link)
            domain = entry.get("domain") or _domain(link)
            if not self._allowed(keyword, title, source, domain):
                continue

            articles.append(
                NewsArticle(
                    title=title,
                    url=link,
                    published_at=published,
                    # `sourcecountry` is the publisher's country, not its name;
                    # using it here labelled articles "India" or "Zimbabwe".
                    source=source,
                    domain=domain,
                    keyword=keyword.label,
                    language=_language_code(entry.get("language"), keyword.language),
                    collector="gdelt",
                    snippet="",
                    fetched_at=utcnow(),
                )
            )

        log.info("News: gdelt '%s' -> %d", keyword.label, len(articles))
        return articles

    def _gdelt_request(
        self, client: httpx.Client, params: dict[str, str], label: str
    ) -> dict | None:
        """GET with rate-limit pacing and 429 backoff. None means give up."""
        for attempt in range(1, GDELT_MAX_ATTEMPTS + 1):
            elapsed = time.monotonic() - self._last_gdelt_call
            if elapsed < GDELT_MIN_INTERVAL_SECS:
                time.sleep(GDELT_MIN_INTERVAL_SECS - elapsed)

            try:
                response = client.get(GDELT_DOC_API, params=params)
                self._last_gdelt_call = time.monotonic()

                if response.status_code == 429:
                    if attempt < GDELT_MAX_ATTEMPTS:
                        backoff = GDELT_MIN_INTERVAL_SECS * (attempt + 1)
                        log.info(
                            "News: gdelt '%s' rate-limited, retrying in %.0fs "
                            "(attempt %d/%d)",
                            label,
                            backoff,
                            attempt,
                            GDELT_MAX_ATTEMPTS,
                        )
                        time.sleep(backoff)
                        continue
                    raise httpx.HTTPStatusError(
                        "rate limited after retries",
                        request=response.request,
                        response=response,
                    )

                response.raise_for_status()
                return response.json()

            except (httpx.HTTPError, ValueError) as exc:
                # GDELT also returns HTML error pages instead of JSON at times.
                # Google News already covers these keywords, so a GDELT miss
                # degrades coverage rather than failing the run.
                self._last_gdelt_call = time.monotonic()
                msg = f"news:gdelt:{label}: {exc}"
                log.warning(msg)
                self.errors.append(msg)
                return None
        return None

    def _allowed(self, keyword: NewsKeyword, title: str, source: str, domain: str) -> bool:
        news = self.config.settings.news
        return article_allowed(
            title=title,
            source=source,
            domain=domain,
            scope=keyword.scope,
            term=keyword.query,
            require_region=keyword.require_region,
            trusted=news.trusted_sources,
            blocked=news.blocked_sources,
            local_terms=[item.query for item in news.keywords if item.scope != "national"],
        )

    # -- dedupe ----------------------------------------------------------- #
    @staticmethod
    def _dedupe(articles: list[NewsArticle]) -> list[NewsArticle]:
        """Collapse the same URL arriving from several keywords or sources."""
        by_hash: dict[str, NewsArticle] = {}
        for article in articles:
            existing = by_hash.get(article.url_hash)
            if existing is None:
                by_hash[article.url_hash] = article
                continue
            # Keep the richer record, but remember every matching keyword.
            keywords = {k.strip() for k in existing.keyword.split(",") if k.strip()}
            keywords.add(article.keyword)
            existing.keyword = ", ".join(sorted(keywords))
            if not existing.snippet and article.snippet:
                existing.snippet = article.snippet
            if existing.published_at is None and article.published_at:
                existing.published_at = article.published_at
        return sorted(
            by_hash.values(),
            key=lambda a: a.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
