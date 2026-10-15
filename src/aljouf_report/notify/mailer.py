"""Weekly email, laid out like a one-page briefing.

The structure follows the ministry media-summary sheet: a title band, the
top stories, then large percentage graphics. Colors come from the Al Jouf
2026 identity (olive, lavender, sand, gold, and poppy red).
"""

from __future__ import annotations

import html
import logging
import shutil
from functools import lru_cache
import smtplib
import subprocess
import tempfile
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate
from io import BytesIO
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from ..config import AppConfig
from ..models import OWNED_ONLY_PLATFORMS, PLATFORM_AR, SENTIMENT_AR, RunSummary

log = logging.getLogger(__name__)

# Al Jouf 2026 identity palette.
OLIVE = "#5d7e4e"
OLIVE_DARK = "#3f5a36"
LAVENDER = "#8867b9"
SAND = "#f4a460"
CREAM = "#fff8dc"
GOLD = "#d4af37"
STONE = "#8b7355"
INK = "#3d3428"
RED = "#c00000"
PAPER = "#f6f1e4"
WHITE = "#ffffff"
MOON = "#bac3b5"

_RGB = {
    "olive": (93, 126, 78),
    "lavender": (136, 103, 185),
    "sand": (240, 217, 177),
    "gold": (212, 175, 55),
    "red": (192, 0, 0),
    "moon": (186, 195, 181),
    "ink": (61, 52, 40),
    "white": (255, 255, 255),
}

_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]
_RIYADH = ZoneInfo("Asia/Riyadh")
_PLATFORM_ORDER = ("instagram", "x", "youtube", "linkedin", "whatsapp", "snapchat")


class EmailNotConfigured(RuntimeError):
    pass


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def _is_national_news(keyword: str) -> bool:
    parts = [part.strip() for part in keyword.split(",") if part.strip()]
    if not parts:
        return False
    return all(
        "saudi" in part.casefold() or "السعودية" in part or "ترند" in part
        for part in parts
    )


def _change_percent(row: dict[str, Any]) -> float | None:
    """Percent change versus the previous count. None when there is no baseline."""
    followers = row.get("followers")
    delta = row.get("delta")
    if followers is None or delta is None:
        return None
    previous = int(followers) - int(delta)
    if previous <= 0:
        return None
    return int(delta) / previous * 100


def _overall_change(rows: Sequence[dict[str, Any]]) -> float | None:
    previous = 0
    delta = 0
    comparable = False
    for row in rows:
        if row.get("followers") is None or row.get("delta") is None:
            continue
        before = int(row["followers"]) - int(row["delta"])
        if before <= 0:
            continue
        comparable = True
        previous += before
        delta += int(row["delta"])
    if not comparable or previous <= 0:
        return None
    return delta / previous * 100


def _fmt_percent(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    if abs(value) < 0.05:
        return "0%"
    if signed:
        return f"{value:+.0f}%"
    return f"{value:.0f}%"


def _share(count: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{count / total * 100:.0f}%"


def _ltr(value: str) -> str:
    """Keep percentages and dates from reversing inside Arabic text."""
    return f'<span dir="ltr" style="unicode-bidi:embed">{value}</span>'


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    names = ("tahomabd.ttf", "segoeuib.ttf", "arialbd.ttf") if bold else (
        "tahoma.ttf",
        "segoeui.ttf",
        "arial.ttf",
    )
    windows = Path(r"C:\Windows\Fonts")
    for name in names:
        path = windows / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "jsdo-logo.png"
_PIE_GREEN = (33, 166, 90)
_PIE_RED = (211, 47, 47)
_PIE_NEUTRAL = (176, 184, 168)


def _pie(parts: list[tuple[float, tuple[int, int, int]]]) -> bytes:
    """A solid pie on white, so mail clients do not drop it."""
    from PIL import Image, ImageDraw

    size = 320
    image = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    pad = 6
    box = [pad, pad, size - pad, size - pad]
    visible = [(value, color) for value, color in parts if value > 0]
    if not visible:
        visible = [(1, _PIE_NEUTRAL)]
    if len(visible) == 1:
        draw.ellipse(box, fill=visible[0][1])
    else:
        total = sum(value for value, _ in visible)
        start = -90.0
        for value, color in visible:
            sweep = 360.0 * value / total
            draw.pieslice(box, start, start + sweep, fill=color)
            start += sweep
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _logo_png() -> bytes | None:
    if not _LOGO_PATH.exists():
        return None
    from PIL import Image

    image = Image.open(_LOGO_PATH).convert("RGB")
    image.thumbnail((560, 280), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _donut(slices: list[tuple[float, str]], center: str, center_color: str) -> bytes:
    """A ring chart. A single slice is drawn as a full ring."""
    from PIL import Image, ImageDraw

    size = 420
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = 16
    box = [pad, pad, size - pad, size - pad]
    visible = [(value, _RGB.get(name, _RGB["moon"])) for value, name in slices if value > 0]
    if not visible:
        visible = [(1, _RGB["moon"])]
    if len(visible) == 1:
        draw.ellipse(box, fill=visible[0][1])
    else:
        total = sum(value for value, _ in visible)
        start = -90.0
        for value, color in visible:
            sweep = 360.0 * value / total
            draw.pieslice(box, start, start + sweep, fill=color)
            start += sweep
    hole = int(size * 0.31)
    draw.ellipse([hole, hole, size - hole, size - hole], fill=_RGB["white"])
    size_px = 72 if len(center) <= 2 else 52 if len(center) <= 4 else 36
    font = _font(size_px, bold=True)
    draw.text((size / 2, size / 2), center, fill=_RGB.get(center_color, _RGB["ink"]), font=font, anchor="mm")
    image = image.resize((200, 200), Image.Resampling.LANCZOS)
    # A transparent PNG is dropped by some mail clients, which left these
    # two charts looking empty.
    flat = Image.new("RGB", image.size, _RGB["white"])
    flat.paste(image, mask=image.split()[-1])
    buffer = BytesIO()
    flat.save(buffer, format="PNG")
    return buffer.getvalue()


_INTERNATIONAL = ("bbc", "cnn", "fox")
_OTHER_INTERNATIONAL = ("reuters", "bloomberg", "apnews", "associated press")
_SAUDI_OUTLETS = (
    "وكالة الأنباء",
    "واس",
    "spa.gov",
    "سبق",
    "sabq",
    "عكاظ",
    "okaz",
    "العربية",
    "alarabiya",
    "الشرق الأوسط",
    "aawsat",
    "الإخبارية",
    "الاخبارية",
    "alekhbariya",
    "أخبار 24",
    "اخبار 24",
    "akhbaar24",
    "أرقام",
    "ارقام",
    "argaam",
    "صحيفة اليوم",
    "alyaum",
    "الاقتصادية",
    "aleqt",
    "الوطن",
    "alwatan",
    "المدينة",
    "al-madina",
    "arab news",
    "arabnews",
    "saudi gazette",
    "saudigazette",
    "الحدث",
    "alhadath",
)


def _outlet_blob(row: dict[str, Any]) -> str:
    return f"{row.get('source') or ''} {row.get('domain') or ''}".casefold()


def _matches_outlet(row: dict[str, Any], names: tuple[str, ...]) -> bool:
    blob = _outlet_blob(row)
    return any(name in blob for name in names)


def _prefer_arabic(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: str(row.get("published_at") or ""), reverse=True)
    ordered.sort(key=lambda row: str(row.get("language") or "").lower() == "en")
    return ordered


_NEGATIVE_HEADLINES = (
    "حرب", "هجوم", "هجمات", "مقتل", "وفاة", "قتلى", "قتيل", "ضحايا", "حوثي",
    "اعتداء", "صاروخ", "انفجار", "إعدام", "اعدام", "قصف", "غارة", "إصابة",
    "اصابة", "اشتباك", "دمار", "احتجاز", "احتجز", "اعتقال", "اعتقل", "فتن",
    "تحذ", "استهداف", "استهدف", "أزمة", "ازمة", "عسكر", "مقاتل", "قوات",
    "إنذار", "انذار", "ضربة", "ضربات", "دامي", "إدان", "توتر", "عقوب", "سجن",
    "killed", "attack", "attacks", "war", "houthi", "strike", "strikes",
    "missile", "death", "dead", "detained", "arrest",
)
_POSITIVE_HEADLINES = (
    "فوز", "تفوز", "لقب", "كأس", "كاس", "إنجاز", "انجاز", "استثمار", "اتفاقية",
    "اتفاقي", "نجاح", "نجح", "تطوير", "افتتاح", "جائزة", "نمو", "توقيع", "شراكة",
    "تدشين", "إطلاق", "اطلاق", "تكريم", "بطولة", "تهنئ", "سياح", "مشروع",
    "win", "wins", "title", "invest", "agreement", "growth", "award", "success",
)
# A cup win is fine, but a congratulations or an investment is the story to lead with.
_WARM_HEADLINES = (
    "تهنئ", "نجح", "نجاح", "استثمار", "سياح", "اتفاقي", "افتتاح", "جائز",
    "تكريم", "إنجاز", "انجاز", "تدشين", "شراك", "مشروع", "success", "invest",
)


def _headline(row: dict[str, Any]) -> str:
    return str(row.get("title") or "").casefold()


def _is_negative_headline(row: dict[str, Any]) -> bool:
    title = _headline(row)
    return any(word.casefold() in title for word in _NEGATIVE_HEADLINES)


def _is_positive_headline(row: dict[str, Any]) -> bool:
    title = _headline(row)
    return any(word.casefold() in title for word in _POSITIVE_HEADLINES)


def _is_warm_headline(row: dict[str, Any]) -> bool:
    title = _headline(row)
    return any(word.casefold() in title for word in _WARM_HEADLINES)


def _positive_stories(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Good news about Saudi Arabia: a success, a welcome, or an agreement."""
    upbeat = [
        row for row in rows
        if _is_positive_headline(row) and not _is_negative_headline(row)
    ]
    ordered = sorted(upbeat, key=lambda row: str(row.get("published_at") or ""), reverse=True)
    ordered.sort(
        key=lambda row: (
            0 if _is_warm_headline(row) else 1,
            str(row.get("language") or "").lower() == "en",
        )
    )
    return ordered


def _select_news(
    rows: Sequence[dict[str, Any]],
    outside_rows: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Two Al Jouf stories, one Saudi outlet, and one positive BBC, CNN, or Fox story."""
    local = [row for row in rows if not _is_national_news(str(row.get("keyword") or ""))]
    national = [row for row in rows if _is_national_news(str(row.get("keyword") or ""))]
    wider = list(outside_rows) if outside_rows is not None else list(rows)
    abroad_pool = [row for row in wider if _is_national_news(str(row.get("keyword") or ""))]
    abroad = [row for row in abroad_pool if _matches_outlet(row, _INTERNATIONAL)]
    if not _positive_stories(abroad):
        abroad = [row for row in abroad_pool if _matches_outlet(row, _OTHER_INTERNATIONAL)]
    domestic = [row for row in national if _matches_outlet(row, _SAUDI_OUTLETS)]
    if not domestic:
        skipped = {id(row) for row in abroad}
        domestic = [row for row in national if id(row) not in skipped]
    return _prefer_arabic(local)[:2] + _positive_stories(domestic)[:1] + _positive_stories(abroad)[:1]


def _news_badge(row: dict[str, Any]) -> str:
    if not _is_national_news(str(row.get("keyword") or "")):
        return "الجوف"
    if _matches_outlet(row, _INTERNATIONAL + _OTHER_INTERNATIONAL):
        return "السعودية · دولي"
    return "السعودية"


def _extra_followers(rows: Sequence[dict[str, Any]]) -> int | None:
    """New followers since the previous count. A first-time platform is not growth."""
    found = False
    total = 0
    for row in rows:
        if row.get("delta") is None or row.get("followers") is None:
            continue
        found = True
        total += int(row["delta"])
    return total if found else None


def _fmt_signed_int(value: int | None) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"+{value:,}"
    if value < 0:
        return f"−{abs(value):,}"
    return "0"


def _office_label(row: dict[str, Any]) -> str:
    """مكتب plus the place, so the chart is not the full official title."""
    short = str(row.get("short_name") or "").strip()
    if not short:
        name = str(row.get("name") or "").strip()
        for prefix in (
            "المكتب الاستراتيجي لتطوير منطقة ",
            "المكتب الاستراتيجي لتطوير ",
            "هيئة تطوير منطقة ",
            "هيئة تطوير ",
            "هيئة ",
        ):
            if name.startswith(prefix):
                name = name[len(prefix) :].strip()
                break
        short = name
    if short.startswith("مكتب "):
        return short
    return f"مكتب {short}"


def _shape_ar(text: str) -> str:
    """Join Arabic letters and put them in visual order for a drawn chart."""
    import arabic_reshaper
    from bidi.algorithm import get_display

    return get_display(arabic_reshaper.reshape(text))


def _comparison_chart(window: Sequence[dict[str, Any]]) -> bytes | None:
    """Horizontal bars for the offices around us. Ours is olive; the others are gold."""
    if not window:
        return None
    from PIL import Image, ImageDraw

    width = 1280
    row_h = 118
    height = 24 + len(window) * row_h + 20
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    highest = max(int(row.get("followers") or 0) for row in window) or 1
    name_font = _font(34, bold=True)
    small_font = _font(22, bold=True)
    num_font = _font(26, bold=True)
    ink = (61, 52, 40)
    olive = (93, 126, 78)
    gold = (184, 146, 74)
    track = (244, 239, 228)
    labels = [_shape_ar(_office_label(row)) for row in window]
    name_right = width - 36
    longest = max(draw.textlength(label, font=name_font) for label in labels)
    track_right = int(name_right - longest - 36)
    track_left = 48
    track_span = max(180, track_right - track_left)
    for index, row in enumerate(window):
        top = 16 + index * row_h
        owned = bool(row.get("owned"))
        followers = int(row.get("followers") or 0)
        if owned:
            draw.rounded_rectangle(
                [20, top, width - 20, top + row_h - 14],
                radius=22,
                fill=(238, 243, 234),
            )
        mid = top + (row_h - 14) / 2
        name = labels[index]
        draw.text(
            (name_right, mid - (16 if owned else 0)),
            name,
            font=name_font,
            fill=olive if owned else ink,
            anchor="rm",
        )
        if owned:
            tag = _shape_ar("حساباتنا")
            draw.text((name_right, mid + 22), tag, font=small_font, fill=gold, anchor="rm")
        bar_h = 34
        bar_top = mid - bar_h / 2 + (8 if owned else 0)
        draw.rounded_rectangle(
            [track_left, bar_top, track_right, bar_top + bar_h],
            radius=17,
            fill=track,
        )
        fill_w = max(36, round(followers / highest * track_span))
        fill_left = track_right - fill_w
        draw.rounded_rectangle(
            [fill_left, bar_top, track_right, bar_top + bar_h],
            radius=17,
            fill=olive if owned else gold,
        )
        number = f"{followers:,}"
        number_w = draw.textlength(number, font=num_font)
        if fill_w > number_w + 36:
            draw.text(
                (fill_left + 16, bar_top + bar_h / 2),
                number,
                font=num_font,
                fill=(255, 255, 255),
                anchor="lm",
            )
        else:
            draw.text(
                (fill_left - 12, bar_top + bar_h / 2),
                number,
                font=num_font,
                fill=ink,
                anchor="rm",
            )
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _comparison_window(
    board: Sequence[dict[str, Any]], above: int = 2, below: int = 1
) -> list[dict[str, Any]]:
    """The accounts immediately above us, us, and the account immediately below."""
    if not board:
        return []
    index = next((i for i, row in enumerate(board) if row.get("owned")), None)
    if index is None:
        return list(board[: above + below + 1])
    start = max(0, index - above)
    end = min(len(board), index + below + 1)
    return list(board[start:end])


def _top_comments(rows: Sequence[dict[str, Any]], label: str, limit: int = 3) -> list[dict[str, Any]]:
    chosen = []
    for row in rows:
        text = str(row.get("text") or "").strip()
        if row.get("label") != label or len(text) < 8 or text.startswith("http"):
            continue
        chosen.append(row)
    chosen.sort(
        key=lambda row: (
            len(str(row.get("text") or "").strip()),
            float(row.get("confidence") or 0),
            str(row.get("published_at") or ""),
        ),
        reverse=True,
    )
    return chosen[:limit]


def _news_lines(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return f'<div style="color:{STONE}">لا توجد أخبار خلال هذه الفترة.</div>'
    lines = []
    for index, row in enumerate(rows, start=1):
        title = _esc(row.get("title"))
        url = _esc(row.get("url"))
        source = _esc(row.get("source") or row.get("domain") or "")
        link = f'<a href="{url}" style="color:{INK};text-decoration:none">{title}</a>' if url else title
        lines.append(
            f'<div style="margin:0 0 6px;font-size:14px;line-height:1.45">'
            f'<b>{_ltr(str(index))}.</b> {link}'
            f' <span style="color:{STONE}">· {_esc(_news_badge(row))} · {source}</span></div>'
        )
    return "".join(lines)


def _platform_line(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return f'<span style="color:{STONE}">لا توجد بيانات متابعين.</span>'
    by_platform = {str(row.get("platform")): row for row in rows}
    ordered = [by_platform[name] for name in _PLATFORM_ORDER if name in by_platform]
    ordered += [row for row in rows if row.get("platform") not in _PLATFORM_ORDER]
    parts = []
    for row in ordered:
        name = _esc(PLATFORM_AR.get(str(row.get("platform")), str(row.get("platform"))))
        delta = row.get("delta")
        if delta is None or row.get("followers") is None:
            change = "جديد"
        else:
            change = _fmt_signed_int(int(delta))
        parts.append(f"{name} {_ltr(_fmt_int(row.get('followers')))} ({_ltr(change)})")
    return " · ".join(parts)


def _standing_line(board: Sequence[dict[str, Any]]) -> str:
    window = _comparison_window(board)
    if not window:
        return f'<span style="color:{STONE}">لا توجد مقارنة.</span>'
    ours = next((row for row in window if row.get("owned")), None)
    above = [row for row in window if ours and int(row.get("followers") or 0) > int(ours.get("followers") or 0)]
    below = [row for row in window if ours and int(row.get("followers") or 0) < int(ours.get("followers") or 0)]
    names = " · ".join(
        f"{_esc(row.get('name'))} {_ltr(_fmt_int(row.get('followers')))}"
        + (" (حساباتنا)" if row.get("owned") else "")
        for row in window
    )
    note = ""
    if ours and above:
        gap = int(above[-1]["followers"]) - int(ours["followers"])
        note = f"يفصلنا عن {_esc(above[-1].get('name'))} {_ltr(_fmt_int(gap))} متابعاً"
    if ours and below:
        lead = int(ours["followers"]) - int(below[0]["followers"])
        lead_text = f"ونتقدم على {_esc(below[0].get('name'))} بـ {_ltr(_fmt_int(lead))}"
        note = f"{note}، {lead_text}" if note else lead_text
    extra = f'<div style="color:{STONE};margin-top:4px">{note}.</div>' if note else ""
    return f"<div>{names}</div>{extra}"


def _comment_lines(comments: Sequence[dict[str, Any]], top_negative: Sequence[dict[str, Any]]) -> str:
    positive = _top_comments(comments, "Positive", limit=1)
    negative = _top_comments(comments, "Negative", limit=1) or _top_comments(top_negative, "Negative", limit=1)
    if negative:
        second_title, second_rows = "سلبي", negative
    else:
        second_title, second_rows = "محايد", _top_comments(comments, "Neutral", limit=1)
    blocks = [("إيجابي", positive), (second_title, second_rows)]
    lines = []
    for title, rows in blocks:
        text = _esc(str(rows[0].get("text") or "")[:120]) if rows else "لا يوجد"
        lines.append(f'<div style="margin:0 0 4px;font-size:14px"><b>{title}:</b> {text}</div>')
    return (
        f'<div style="font-size:16px;font-weight:700;color:{OLIVE};margin:14px 0 8px">التعليقات</div>'
        + "".join(lines)
    )


def _edge_path() -> Path | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    for name in (
        "msedge",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    return next((path for path in candidates if path.exists()), None)


def html_to_pdf(page: str) -> bytes | None:
    """Print the full briefing to a PDF the inbox can download. None if that fails."""
    edge = _edge_path()
    if edge is None:
        return None
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        html_path = root / "report.html"
        pdf_path = root / "report.pdf"
        html_path.write_text(page, encoding="utf-8")
        try:
            subprocess.run(
                [
                    str(edge),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}",
                    html_path.as_uri(),
                ],
                check=True,
                timeout=60,
                capture_output=True,
            )
        except (subprocess.SubprocessError, OSError):
            log.exception("Could not build the PDF report")
            return None
        if not pdf_path.exists() or pdf_path.stat().st_size < 1000:
            return None
        return pdf_path.read_bytes()


def embed_images(page: str, images: dict[str, bytes]) -> str:
    """Swap cid: pictures for data URIs so a saved HTML or PDF can show them."""
    import base64

    html_page = page
    for cid, payload in images.items():
        encoded = base64.b64encode(payload).decode("ascii")
        html_page = html_page.replace(f"cid:{cid}", f"data:image/png;base64,{encoded}")
    return html_page


def report_file(page: str) -> tuple[str, bytes, str]:
    """The downloadable report: a PDF when Edge can print one, otherwise the HTML."""
    pdf = html_to_pdf(page)
    if pdf:
        return "aljouf-weekly-report.pdf", pdf, "pdf"
    return "aljouf-weekly-report.html", page.encode("utf-8"), "html"


def _section(title: str) -> str:
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:26px 0 12px">
      <tr>
        <td width="22%"><div style="border-top:2px solid {GOLD};line-height:0;font-size:0">&nbsp;</div></td>
        <td style="white-space:nowrap;padding:0 14px;color:{OLIVE};font-size:18px;font-weight:700;text-align:center">{_esc(title)}</td>
        <td width="22%"><div style="border-top:2px solid {GOLD};line-height:0;font-size:0">&nbsp;</div></td>
      </tr>
    </table>"""


def _arrow(color: tuple[int, int, int], up: bool):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (96, 96), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    if up:
        points = [(48, 8), (84, 48), (66, 48), (66, 88), (30, 88), (30, 48), (12, 48)]
    else:
        points = [(48, 88), (84, 48), (66, 48), (66, 8), (30, 8), (30, 48), (12, 48)]
    draw.polygon(points, fill=color)
    return image


def _draw_icon(kind: str) -> bytes:
    """A small solid icon. Mail clients drop transparent pictures."""
    from PIL import Image, ImageDraw

    olive = (93, 126, 78)
    green = (33, 166, 90)
    red = (192, 0, 0)
    stone = (139, 115, 85)
    if kind == "up":
        image = _arrow(green, up=True)
    elif kind == "down":
        image = _arrow(red, up=False)
    else:
        image = Image.new("RGB", (96, 96), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        if kind == "neutral":
            draw.rounded_rectangle([14, 40, 82, 56], radius=8, fill=stone)
        elif kind == "followers":
            draw.ellipse([34, 12, 62, 40], outline=olive, width=6)
            draw.arc([18, 36, 78, 92], 200, 340, fill=olive, width=6)
        elif kind == "extra":
            draw.polygon(
                [(48, 12), (76, 44), (60, 44), (60, 84), (36, 84), (36, 44), (20, 44)],
                fill=olive,
            )
        elif kind == "likes":
            draw.ellipse([16, 22, 50, 56], fill=olive)
            draw.ellipse([46, 22, 80, 56], fill=olive)
            draw.polygon([(18, 42), (78, 42), (48, 82)], fill=olive)
        elif kind == "comments":
            draw.rounded_rectangle([14, 14, 82, 64], radius=14, outline=olive, width=6)
            draw.polygon([(26, 60), (46, 60), (24, 84)], fill=olive)
        elif kind == "news":
            draw.rounded_rectangle([26, 10, 70, 86], radius=6, outline=olive, width=6)
            draw.line([(36, 32), (60, 32)], fill=olive, width=5)
            draw.line([(36, 48), (60, 48)], fill=olive, width=5)
            draw.line([(36, 64), (52, 64)], fill=olive, width=5)
        else:
            for x in (20, 52):
                for y in (20, 52):
                    draw.rounded_rectangle([x, y, x + 24, y + 24], radius=5, outline=olive, width=5)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@lru_cache(maxsize=1)
def _icon_set() -> dict[str, bytes]:
    names = (
        "followers",
        "extra",
        "likes",
        "comments",
        "news",
        "platforms",
        "up",
        "down",
        "neutral",
    )
    return {name: _draw_icon(name) for name in names}


def _insight_cell(cid: str, images: dict[str, bytes], label: str, value: str, rule: bool) -> str:
    border = "border-bottom:1px solid #eadfca;" if rule else ""
    return (
        f'<td width="33%" valign="top" style="text-align:center;padding:12px 4px;{border}">'
        f"{_image(cid, images, label, width=36)}"
        f'<div style="font-size:12px;color:{STONE};margin-top:6px;line-height:1.35">{_esc(label)}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{INK};margin-top:2px">{value}</div>'
        f"</td>"
    )


def insight_table(
    followers: str,
    extra: str,
    likes: str,
    comments: str,
    news: str,
    platforms: str,
    positive: str,
    negative: str,
    neutral: str,
) -> tuple[str, dict[str, bytes]]:
    """A three-by-three stat sheet. Sentiment uses a thumbs-up, thumbs-down, and a dash."""
    images = {f"stat-{name}": payload for name, payload in _icon_set().items()}
    rows = [
        [
            ("stat-followers", "المتابعون", followers),
            ("stat-extra", "متابعون إضافيون", extra),
            ("stat-likes", "إعجابات الأسبوع", likes),
        ],
        [
            ("stat-comments", "التعليقات", comments),
            ("stat-news", "الأخبار", news),
            ("stat-platforms", "المنصات", platforms),
        ],
        [
            ("stat-up", "الردود الإيجابية", positive),
            ("stat-down", "الردود السلبية", negative),
            ("stat-neutral", "الردود المحايدة", neutral),
        ],
    ]
    body = []
    for index, row in enumerate(rows):
        cells = "".join(
            _insight_cell(cid, images, label, value, rule=index < 2)
            for cid, label, value in row
        )
        body.append(f"<tr>{cells}</tr>")
    html_table = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{WHITE};border:1px solid #eadfca;border-radius:16px">'
        + "".join(body)
        + "</table>"
    )
    return html_table, images


def _comment_likes(rows: Sequence[dict[str, Any]]) -> int | None:
    found = False
    total = 0
    for row in rows:
        value = row.get("likes")
        if value is None:
            continue
        found = True
        total += int(value)
    return total if found else None


def _stat_tile(value: str, label: str, background: str, color: str = WHITE) -> str:
    return f"""
    <td width="25%" valign="middle" style="background:{background};border-radius:16px;padding:16px 8px;text-align:center">
      <div style="font-size:30px;line-height:1.1;font-weight:700;color:{color}">{value}</div>
      <div style="font-size:12px;line-height:1.4;color:{color};margin-top:4px">{_esc(label)}</div>
    </td>"""


class WeeklyMailer:
    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.secrets.smtp_ready and bool(self.config.recipients)

    def render_html(
        self,
        summary: RunSummary,
        owned_followers: Sequence[dict[str, Any]],
        top_negative: Sequence[dict[str, Any]],
        week_comments: Sequence[dict[str, Any]],
        week_news: Sequence[dict[str, Any]],
        ranking: Sequence[dict[str, Any]] = (),
        outside_news: Sequence[dict[str, Any]] = (),
        post_likes: int | None = None,
    ) -> tuple[str, dict[str, bytes]]:
        days = self.config.settings.windows.comments_scrape_days
        sentiment = summary.sentiment_counts
        positive = int(sentiment.get("Positive", 0))
        negative = int(sentiment.get("Negative", 0))
        neutral = int(sentiment.get("Neutral", 0))
        scored = positive + negative + neutral
        extra = _extra_followers(owned_followers)
        follower_total = sum(
            int(row["followers"])
            for row in owned_followers
            if row.get("followers") is not None
            and row.get("platform") not in OWNED_ONLY_PLATFORMS
        )
        images: dict[str, bytes] = {}
        try:
            images["sentiment"] = _pie(
                [
                    (positive, _PIE_GREEN),
                    (negative, _PIE_RED),
                    (neutral, _PIE_NEUTRAL),
                ]
            )
        except Exception:  # noqa: BLE001 - the percentages still show without the picture
            log.exception("Comment pie could not be drawn")
        logo = _logo_png()
        if logo:
            images["logo"] = logo
        likes = post_likes if post_likes is not None else _comment_likes(week_comments)
        platforms = len({str(row.get("platform")) for row in owned_followers if row.get("platform")})
        grid, grid_images = insight_table(
            _ltr(_fmt_int(follower_total)),
            _ltr(_fmt_signed_int(extra)),
            _ltr(_fmt_int(likes)),
            _ltr(_fmt_int(len(week_comments))),
            _ltr(_fmt_int(len(week_news))),
            _ltr(_fmt_int(platforms)),
            _ltr(_share(positive, scored)),
            _ltr(_share(negative, scored)),
            _ltr(_share(neutral, scored)),
        )
        images.update(grid_images)
        now = datetime.now(_RIYADH)
        stamp = (
            f"{_WEEKDAYS[now.weekday()]} {now.day} {_MONTHS[now.month - 1]} {now.year}"
            f" · {_ltr(now.strftime('%H:%M'))}"
        )

        logo_img = _image("logo", images, "المكتب الاستراتيجي لتطوير منطقة الجوف", width=280)
        pie_img = _image("sentiment", images, "نسبة التعليقات", width=168)
        news_cards = self._news_cards(_select_news(week_news, outside_news or None))
        platform_block = self._platforms(owned_followers)
        comment_block = self._comment_sections(week_comments, top_negative, days)
        comparison_block = self._comparison(ranking, images)
        dashboard_url = _esc(self.config.dashboard_url)

        body = f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<style>
  body {{ margin:0; padding:0; width:100%; }}
  img {{ border:0; display:block; margin:0 auto; }}
  @media only screen and (max-width:680px) {{
    .jsdo-card {{ width:100% !important; max-width:100% !important; }}
    .jsdo-pad {{ padding-left:14px !important; padding-right:14px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;width:100%;background:{PAPER};font-family:Tahoma,'Segoe UI',Arial,sans-serif;color:{INK};text-align:center;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER};margin:0 auto;">
    <tr><td align="center" style="padding:12px 8px 24px;text-align:center;">
      <table role="presentation" class="jsdo-card" dir="rtl" align="center" width="100%" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;margin:0 auto;background:{CREAM};border-radius:18px;overflow:hidden;direction:rtl;text-align:center;">
        <tr><td style="background:#ffffff;text-align:center;padding:18px 16px 14px">
          {logo_img or f'<div style="color:#f0d9b1;font-size:14px">{_esc("المكتب الاستراتيجي لتطوير منطقة الجوف")}</div>'}
        </td></tr>
        <tr><td style="background:{OLIVE};padding:22px 28px 18px;text-align:center">
          <div style="letter-spacing:8px;color:{GOLD};font-size:11px">◆ ◆ ◆</div>
          <div style="margin-top:8px;color:#f0d9b1;font-size:13px">المكتب الاستراتيجي لتطوير منطقة الجوف</div>
          <div style="margin-top:4px;color:{WHITE};font-size:28px;font-weight:700;line-height:1.35">التقرير الأسبوعي</div>
          <div style="color:{CREAM};font-size:15px">وسائل التواصل الاجتماعي</div>
        </td></tr>
        <tr><td style="background:{OLIVE_DARK};color:{CREAM};text-align:center;padding:9px 16px;font-size:13px">
          آخر {_ltr(str(days))} أيام · {stamp}
        </td></tr>
        <tr><td class="jsdo-pad" align="center" style="padding:8px 16px 26px;text-align:center;">
          {_section("أبرز الأخبار")}
          {news_cards}
          {_section("ملخص الأسبوع")}
          <table role="presentation" width="100%" cellpadding="0" cellspacing="8">
            <tr>
              <td align="center" valign="middle" style="background:{WHITE};border-radius:16px;padding:14px 12px;text-align:center;border:1px solid #eadfca">
                <table role="presentation" align="center" cellpadding="0" cellspacing="0" style="margin:0 auto;"><tr>
                  <td width="168" valign="middle" align="center" style="text-align:center;">{pie_img}</td>
                  <td valign="middle" align="center" style="text-align:center;padding:0 12px">
                    <div style="font-size:26px;font-weight:700;color:#21a65a;line-height:1">{_ltr(_share(positive, scored))}</div>
                    <div style="font-size:12px;color:{STONE};margin:2px 0 8px">نسبة التعليقات الإيجابية <span style="display:inline-block;width:9px;height:9px;background:#21a65a"></span></div>
                    <div style="font-size:26px;font-weight:700;color:{RED};line-height:1">{_ltr(_share(negative, scored))}</div>
                    <div style="font-size:12px;color:{STONE};margin:2px 0 8px">نسبة التعليقات السلبية <span style="display:inline-block;width:9px;height:9px;background:{RED}"></span></div>
                    <div style="font-size:13px;color:{STONE}">محايد {_ltr(_share(neutral, scored))} <span style="display:inline-block;width:9px;height:9px;background:#b0b8a8"></span></div>
                  </td>
                </tr></table>
              </td>
            </tr>
          </table>
          {_section("إحصائيات الحسابات")}
          {grid}
          {_section("المتابعون")}
          {platform_block}
          {comparison_block}
          {comment_block}
          <p style="text-align:center;margin:26px 0 8px">
            <span style="display:inline-block;background:{GOLD};color:{INK};padding:12px 22px;border-radius:8px;font-weight:700;font-size:15px">تحميل التقرير</span>
            <a href="{dashboard_url}" style="display:inline-block;background:{OLIVE};color:{WHITE};text-decoration:none;padding:12px 22px;border-radius:8px;font-weight:700;font-size:15px;margin-right:8px">لوحة المعلومات</a>
          </p>
          <p style="text-align:center;margin:0;font-size:11px;color:{STONE}">
            تقرير آلي · التشغيل رقم {summary.run_id}
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
        return body, images

    def render_brief(
        self,
        summary: RunSummary,
        owned_followers: Sequence[dict[str, Any]],
        top_negative: Sequence[dict[str, Any]],
        week_comments: Sequence[dict[str, Any]],
        week_news: Sequence[dict[str, Any]],
        ranking: Sequence[dict[str, Any]] = (),
        outside_news: Sequence[dict[str, Any]] = (),
        download_href: str = "",
        download_name: str = "aljouf-weekly-report.pdf",
    ) -> str:
        """A short note Gmail can show in one view, with a link to the full report."""
        days = self.config.settings.windows.comments_scrape_days
        sentiment = summary.sentiment_counts
        positive = int(sentiment.get("Positive", 0))
        negative = int(sentiment.get("Negative", 0))
        neutral = int(sentiment.get("Neutral", 0))
        scored = positive + negative + neutral
        extra = _extra_followers(owned_followers)
        follower_total = sum(
            int(row["followers"])
            for row in owned_followers
            if row.get("followers") is not None
            and row.get("platform") not in OWNED_ONLY_PLATFORMS
        )
        now = datetime.now(_RIYADH)
        stamp = (
            f"{_WEEKDAYS[now.weekday()]} {now.day} {_MONTHS[now.month - 1]} {now.year}"
        )
        news = _select_news(week_news, outside_news or None)
        style = (
            f"display:inline-block;background:{GOLD};color:{INK};text-decoration:none;"
            f"padding:10px 18px;border-radius:8px;font-weight:700;font-size:15px"
        )
        if download_href:
            download = f'<a href="{_esc(download_href)}" style="{style}">تحميل التقرير</a>'
            download_note = ""
        else:
            download = f'<span style="{style}">تحميل التقرير</span>'
            download_note = (
                f'<div style="font-size:12px;color:{STONE};margin-top:6px">الملف {_ltr(download_name)} مرفق مع الرسالة</div>'
            )
        dashboard = (
            f'<a href="{_esc(self.config.dashboard_url)}" style="display:inline-block;background:{OLIVE};'
            f'color:{WHITE};text-decoration:none;padding:10px 18px;border-radius:8px;font-weight:700;font-size:15px">'
            f"لوحة المعلومات</a>"
        )
        return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head><meta charset="utf-8"></head>
<body style="margin:0;background:{PAPER};font-family:Tahoma,Arial,sans-serif;color:{INK}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{OLIVE};color:{WHITE};text-align:center">
<tr><td style="padding:18px 16px">
<div style="font-size:13px;color:#f0d9b1">المكتب الاستراتيجي لتطوير منطقة الجوف</div>
<div style="font-size:24px;font-weight:700;margin-top:4px">التقرير الأسبوعي</div>
<div style="font-size:13px;margin-top:4px">آخر {_ltr(str(days))} أيام · {stamp}</div>
</td></tr></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:14px 16px 18px">
<div style="text-align:center;margin:0 0 14px">{download} {dashboard}{download_note}</div>
<div style="font-size:16px;font-weight:700;color:{OLIVE};margin:0 0 8px">أبرز الأخبار</div>
{_news_lines(news)}
<div style="font-size:16px;font-weight:700;color:{OLIVE};margin:14px 0 8px">ملخص الأسبوع</div>
<div style="background:{WHITE};border:1px solid #eadfca;border-radius:12px;padding:12px;text-align:center">
<div style="font-size:22px;font-weight:700;color:{OLIVE}">{_ltr(_share(positive, scored))} <span style="font-size:13px;font-weight:400;color:{STONE}">إيجابية</span></div>
<div style="font-size:22px;font-weight:700;color:{RED};margin-top:4px">{_ltr(_share(negative, scored))} <span style="font-size:13px;font-weight:400;color:{STONE}">سلبية</span>
<span style="font-size:13px;color:{LAVENDER};font-weight:400"> · محايد {_ltr(_share(neutral, scored))}</span></div>
<div style="margin-top:8px;font-size:15px">{_ltr(_fmt_int(follower_total))} متابع · {_ltr(_fmt_signed_int(extra))} إضافيون · {_ltr(_fmt_int(len(week_comments)))} تعليقات</div>
</div>
<div style="font-size:16px;font-weight:700;color:{OLIVE};margin:14px 0 8px">المتابعون</div>
<div style="font-size:14px;line-height:1.6">{_platform_line(owned_followers)}</div>
<div style="font-size:16px;font-weight:700;color:{OLIVE};margin:14px 0 8px">موقفنا بين المكاتب</div>
<div style="font-size:14px;line-height:1.6">{_standing_line(ranking)}</div>
{_comment_lines(week_comments, top_negative)}
<div style="text-align:center;margin-top:16px;font-size:11px;color:{STONE}">تقرير آلي · التشغيل رقم {summary.run_id}</div>
</td></tr></table>
</body></html>"""

    def render_text(self, summary: RunSummary, follower_total: int | None = None) -> str:
        sentiment = summary.sentiment_counts
        scored = sum(sentiment.values())
        positive = _share(int(sentiment.get("Positive", 0)), scored)
        negative = _share(int(sentiment.get("Negative", 0)), scored)
        total = summary.followers_collected if follower_total is None else follower_total
        return (
            "التقرير الأسبوعي - المكتب الاستراتيجي لتطوير منطقة الجوف\n"
            f"التشغيل رقم: {summary.run_id}\n"
            f"المتابعون: {total:,}\n"
            f"التعليقات الإيجابية: {positive}\n"
            f"التعليقات السلبية: {negative}\n"
            f"لوحة المعلومات: {self.config.dashboard_url}\n"
        )

    def build_message(
        self,
        summary: RunSummary,
        html_body: str,
        follower_total: int | None = None,
        images: dict[str, bytes] | None = None,
        report_name: str | None = None,
        report_bytes: bytes | None = None,
        report_subtype: str = "html",
    ) -> EmailMessage:
        secrets = self.config.secrets
        recipients = self.config.recipients
        message = EmailMessage(policy=SMTP.clone(utf8=True))
        message["Subject"] = (
            f"{self.config.settings.email.subject_prefix} · "
            f"{datetime.now(_RIYADH).strftime('%Y-%m-%d %H:%M')}"
        )
        message["From"] = secrets.email_from or formataddr(
            ("JSDO Social Reporting", secrets.smtp_username or "")
        )
        message["To"] = ", ".join(recipients)
        message["Date"] = formatdate(localtime=True)
        message.set_content(self.render_text(summary, follower_total))
        message.add_alternative(html_body, subtype="html")
        html_part = message.get_payload()[-1]
        for cid, payload in (images or {}).items():
            html_part.add_related(
                payload,
                maintype="image",
                subtype="png",
                cid=f"<{cid}>",
                disposition="inline",
                filename=f"{cid}.png",
            )
        if report_name and report_bytes:
            maintype = "application" if report_subtype == "pdf" else "text"
            message.add_attachment(
                report_bytes,
                maintype=maintype,
                subtype=report_subtype,
                filename=report_name,
            )
        return message

    def send(
        self,
        summary: RunSummary,
        html_body: str,
        follower_total: int | None = None,
        images: dict[str, bytes] | None = None,
        report_name: str | None = None,
        report_bytes: bytes | None = None,
        report_subtype: str = "html",
    ) -> list[str]:
        if not self.enabled:
            reason = (
                "SMTP credentials are incomplete"
                if not self.config.secrets.smtp_ready
                else "no recipients configured"
            )
            log.info("Email skipped (%s).", reason)
            return []

        secrets = self.config.secrets
        recipients = self.config.recipients
        message = self.build_message(
            summary,
            html_body,
            follower_total,
            images,
            report_name,
            report_bytes,
            report_subtype,
        )

        try:
            if secrets.smtp_port == 465:
                server: smtplib.SMTP = smtplib.SMTP_SSL(
                    secrets.smtp_host, secrets.smtp_port, timeout=60
                )
            else:
                server = smtplib.SMTP(secrets.smtp_host, secrets.smtp_port, timeout=60)
            with server:
                server.ehlo()
                if secrets.smtp_use_tls and secrets.smtp_port != 465:
                    server.starttls()
                    server.ehlo()
                server.login(secrets.smtp_username, secrets.smtp_password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailNotConfigured(f"Failed to send the weekly email: {exc}") from exc

        log.info("Email sent to %s", ", ".join(recipients))
        return recipients

    def _charts(
        self,
        positive: int,
        neutral: int,
        negative: int,
        followers: Sequence[dict[str, Any]],
        extra: int | None,
    ) -> dict[str, bytes]:
        try:
            groups = {"up": 0, "down": 0, "flat": 0, "new": 0}
            for row in followers:
                percent = _change_percent(row)
                if percent is None:
                    groups["new"] += 1
                elif percent > 0.05:
                    groups["up"] += 1
                elif percent < -0.05:
                    groups["down"] += 1
                else:
                    groups["flat"] += 1
            extra_color = "red" if extra is not None and extra < 0 else "olive"
            return {
                "sentiment": _donut(
                    [(positive, "olive"), (neutral, "lavender"), (negative, "red")],
                    _share(positive, positive + neutral + negative),
                    "olive",
                ),
                "followers": _donut(
                    [
                        (groups["up"], "olive"),
                        (groups["flat"], "sand"),
                        (groups["down"], "red"),
                        (groups["new"], "lavender"),
                    ],
                    _fmt_signed_int(extra),
                    extra_color,
                ),
            }
        except Exception:  # noqa: BLE001 - the briefing still sends without the pictures
            log.exception("Report charts could not be drawn")
            return {}

    def _news_cards(self, rows: Sequence[dict[str, Any]]) -> str:
        if not rows:
            return f'<p style="margin:0;color:{STONE}">لا توجد أخبار خلال هذه الفترة.</p>'
        cards = []
        for index, row in enumerate(rows, start=1):
            title = _esc(row.get("title"))
            url = _esc(row.get("url"))
            source = _esc(row.get("source") or row.get("domain") or "")
            published = _esc(str(row.get("published_at") or "")[:10])
            link = f'<a href="{url}" style="color:{INK};text-decoration:none">{title}</a>' if url else title
            badge = _esc(_news_badge(row))
            cards.append(
                f"""
                <table role="presentation" dir="rtl" width="100%" cellpadding="0" cellspacing="0" style="background:{WHITE};border:1px solid #eadfca;border-radius:12px;margin:0 0 8px">
                  <tr>
                    <td width="42" valign="middle" style="text-align:center;color:{GOLD};font-size:22px;font-weight:700">{_ltr(str(index))}</td>
                    <td valign="middle" style="padding:12px 4px 12px 12px">
                      <div style="font-size:15px;font-weight:700;line-height:1.5">{link}</div>
                      <div style="font-size:12px;color:{STONE};margin-top:3px">{badge} · {source} · {published}</div>
                    </td>
                    <td width="7" bgcolor="{OLIVE}"></td>
                  </tr>
                </table>"""
            )
        return "\n".join(cards)

    def _platforms(self, rows: Sequence[dict[str, Any]]) -> str:
        if not rows:
            return f'<p style="margin:0;color:{STONE}">لا توجد بيانات متابعين.</p>'
        by_platform = {str(row.get("platform")): row for row in rows}
        ordered = [by_platform[name] for name in _PLATFORM_ORDER if name in by_platform]
        ordered += [row for row in rows if row.get("platform") not in _PLATFORM_ORDER]
        highest = max((int(row["followers"]) for row in ordered if row.get("followers") is not None), default=1) or 1
        cards = []
        bars = []
        for row in ordered:
            name = _esc(PLATFORM_AR.get(str(row.get("platform")), str(row.get("platform"))))
            profile_url = str(row.get("profile_url") or "").strip()
            if profile_url:
                name = f'<a href="{_esc(profile_url)}" style="color:{STONE};text-decoration:none">{name}</a>'
            delta = row.get("delta")
            if row.get("followers") is None:
                change = f'<span style="color:{STONE}">{_ltr("—")}</span>'
                bar_color = STONE
            elif delta is None and str(row.get("platform")) in OWNED_ONLY_PLATFORMS:
                change = ""
                bar_color = OLIVE
            elif delta is None:
                change = f'<span style="color:{LAVENDER}">جديد</span>'
                bar_color = LAVENDER
            elif int(delta) < 0:
                change = f'<span style="color:{RED}">▼ {_ltr(_fmt_signed_int(int(delta)))}</span>'
                bar_color = RED
            elif int(delta) > 0:
                change = f'<span style="color:{OLIVE}">▲ {_ltr(_fmt_signed_int(int(delta)))}</span>'
                bar_color = OLIVE
            else:
                change = f'<span style="color:{STONE}">{_ltr("0")}</span>'
                bar_color = OLIVE
            cards.append(
                f"""
                <td width="25%" valign="top" style="background:{WHITE};border:1px solid #eadfca;border-radius:14px;padding:12px 6px;text-align:center">
                  <div style="font-size:12px;color:{STONE}">{name}</div>
                  <div style="font-size:22px;font-weight:700;color:{INK};margin:4px 0">{_ltr(_fmt_int(row.get('followers')))}</div>
                  <div style="font-size:14px;font-weight:700">{change}</div>
                </td>"""
            )
            if row.get("followers") is None:
                continue
            width = 0
            if int(row["followers"]) > 0:
                width = max(4, round(int(row["followers"]) / highest * 100))
            rest = 100 - width
            bars.append(
                f"""
                <tr>
                  <td width="30%" align="center" style="font-size:12px;color:{STONE};padding:6px 4px;text-align:center">{name}</td>
                  <td width="22%" align="center" style="font-size:12px;font-weight:700;color:{INK};padding:6px 4px;text-align:center">{_ltr(_fmt_int(row.get('followers')))}</td>
                  <td width="48%" align="center" style="padding:6px 0">
                    <table role="presentation" align="center" dir="rtl" width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td width="{width}%" bgcolor="{bar_color}" style="height:12px;border-radius:8px;font-size:1px;line-height:12px">&nbsp;</td>
                        <td width="{rest}%"></td>
                      </tr>
                    </table>
                  </td>
                </tr>"""
            )
        comparable = [row for row in ordered if row.get("platform") not in OWNED_ONLY_PLATFORMS]
        own_only = [row for row in ordered if row.get("platform") in OWNED_ONLY_PLATFORMS]
        # The card HTML was built in `ordered` sequence, so split it the same way.
        card_rows = []
        cursor = 0
        for group in (comparable, own_only):
            if not group:
                continue
            chunk = cards[cursor : cursor + len(group)]
            cursor += len(group)
            width = max(25, 100 // len(group))
            chunk = [card.replace('width="25%"', f'width="{width}%"', 1) for card in chunk]
            card_rows.append(
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="6"><tr>{"".join(chunk)}</tr></table>'
            )
        return f"""
        {''.join(card_rows)}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">{''.join(bars)}</table>"""

    def _comparison(self, board: Sequence[dict[str, Any]], images: dict[str, bytes]) -> str:
        window = _comparison_window(board)
        if not window:
            return ""
        ours = next((row for row in window if row.get("owned")), None)
        above = [row for row in window if ours and int(row.get("followers") or 0) > int(ours.get("followers") or 0)]
        below = [row for row in window if ours and int(row.get("followers") or 0) < int(ours.get("followers") or 0)]
        note = ""
        if ours and above:
            gap = int(above[-1]["followers"]) - int(ours["followers"])
            note = f"يفصلنا عن {_esc(_office_label(above[-1]))} {_ltr(_fmt_int(gap))} متابعاً"
        if ours and below:
            lead = int(ours["followers"]) - int(below[0]["followers"])
            lead_text = f"ونتقدم على {_esc(_office_label(below[0]))} بـ {_ltr(_fmt_int(lead))}"
            note = f"{note}، {lead_text}" if note else lead_text
        try:
            chart = _comparison_chart(window)
        except Exception:  # noqa: BLE001 - the names still appear in the sentence below
            log.exception("Comparison chart could not be drawn")
            chart = None
        names = " · ".join(_esc(_office_label(row)) for row in window)
        if chart:
            images["comparison"] = chart
            graph = (
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'style="background:{WHITE};border:1px solid #eadfca;border-radius:16px">'
                f'<tr><td align="center" style="padding:12px 8px;text-align:center">'
                f"{_image('comparison', images, names, width=600)}</td></tr></table>"
            )
        else:
            graph = f'<p style="margin:0;font-size:14px;color:{INK}">{names}</p>'
        caption = (
            f'<p style="margin:8px 0 0;font-size:13px;color:{STONE};text-align:center">{note}.</p>'
            if note
            else ""
        )
        return f"""
        {_section("موقفنا بين المكاتب")}
        {graph}
        {caption}"""

    def _comment_sections(
        self,
        comments: Sequence[dict[str, Any]],
        top_negative: Sequence[dict[str, Any]],
        days: int,
    ) -> str:
        positive = _top_comments(comments, "Positive")
        negative = _top_comments(comments, "Negative") or _top_comments(top_negative, "Negative")
        if negative:
            second_title, second_color, second_rows = "التعليقات السلبية", RED, negative
        else:
            second_title, second_color, second_rows = (
                "التعليقات المحايدة",
                LAVENDER,
                _top_comments(comments, "Neutral"),
            )
        return (
            self._comment_group("التعليقات الإيجابية", positive, OLIVE, days)
            + self._comment_group(second_title, second_rows, second_color, days)
        )

    def _comment_group(
        self, title: str, rows: Sequence[dict[str, Any]], color: str, days: int
    ) -> str:
        if not rows:
            empty_label = {
                "التعليقات الإيجابية": "تعليقات إيجابية",
                "التعليقات السلبية": "تعليقات سلبية",
                "التعليقات المحايدة": "تعليقات محايدة",
            }.get(title, "تعليقات")
            empty = f"لا توجد {empty_label} خلال آخر {_ltr(str(days))} أيام."
            body = (
                f'<p style="margin:0;background:{WHITE};border:1px solid #eadfca;border-radius:12px;'
                f'padding:12px 14px;color:{STONE}">{empty}</p>'
            )
        else:
            cards = []
            for row in rows:
                platform = _esc(PLATFORM_AR.get(str(row.get("platform")), str(row.get("platform") or "")))
                text = _esc(str(row.get("text") or "")[:180])
                cards.append(
                    f"""
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{WHITE};border:1px solid #eadfca;border-radius:12px;margin:0 0 8px">
                      <tr>
                        <td width="6" bgcolor="{color}"></td>
                        <td style="padding:12px 14px">
                          <div style="font-size:12px;color:{color};font-weight:700">{platform}</div>
                          <div style="font-size:14px;line-height:1.55;color:{INK};margin-top:3px">{text}</div>
                        </td>
                      </tr>
                    </table>"""
                )
            body = "".join(cards)
        return f"{_section(title)}{body}"


def _image(cid: str, images: dict[str, bytes], alt: str, width: int = 140) -> str:
    if cid not in images:
        return ""
    return (
        f'<img src="cid:{cid}" width="{width}" alt="{_esc(alt)}" '
        f'style="display:block;margin:0 auto;border:0;max-width:100%">'
    )
