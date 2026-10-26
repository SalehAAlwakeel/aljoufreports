"""Read and write the settings the dashboard control page is allowed to change.

Keywords and recipients live in ``config/settings.yaml``. Competitor accounts
live in ``config/accounts.yaml``. The collectors and the Thursday job read
those files, so a change here is what the next scrape and the next email use.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from aljouf_report.config import CONFIG_DIR, load_config

COMPARABLE_PLATFORMS = ("instagram", "x", "youtube", "linkedin")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_KEYWORDS_RE = re.compile(r"^  keywords:\n.*?(?=^[A-Za-z0-9_]+:)", re.M | re.S)
_RECIPIENTS_RE = re.compile(r"^  recipients:(?: \[\]\n|\n(?:    - .*\n)*)", re.M)


class ControlStoreError(ValueError):
    """A control-page edit that cannot be saved."""


def settings_path() -> Path:
    return CONFIG_DIR / "settings.yaml"


def accounts_path() -> Path:
    return CONFIG_DIR / "accounts.yaml"


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _reload() -> None:
    load_config.cache_clear()


def keywords(path: Path | None = None) -> list[dict]:
    data = _read(path or settings_path())
    return list(data.get("news", {}).get("keywords") or [])


def save_keywords(items: list[dict], path: Path | None = None) -> list[dict]:
    """Replace the news keyword list. Other settings in the file stay put."""
    target = path or settings_path()
    cleaned: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        query = str(raw.get("query") or "").strip()
        if not query:
            continue
        if query in seen:
            raise ControlStoreError(f"الكلمة مكررة: {query}")
        seen.add(query)
        label = str(raw.get("label") or query).strip() or query
        scope = raw.get("scope") or "local"
        if scope not in {"local", "national"}:
            raise ControlStoreError("نوع الكلمة يجب أن يكون محلياً أو سعودياً عاماً.")
        item = {
            "query": query,
            "label": label,
            "language": "en" if query.isascii() else "ar",
            "scope": scope,
        }
        if raw.get("require_region"):
            item["require_region"] = True
        cleaned.append(item)
    if not cleaned:
        raise ControlStoreError("أبقِ كلمة واحدة على الأقل.")

    lines = ["  keywords:"]
    for item in cleaned:
        dumped = yaml.safe_dump(
            item,
            allow_unicode=True,
            default_flow_style=True,
            sort_keys=False,
            width=1000,
        ).strip()
        lines.append(f"    - {dumped}")
    block = "\n".join(lines) + "\n"
    text = target.read_text(encoding="utf-8")
    updated, count = _KEYWORDS_RE.subn(block, text, count=1)
    if count != 1:
        raise ControlStoreError("تعذر العثور على قائمة الكلمات في الإعدادات.")
    target.write_text(updated, encoding="utf-8")
    _reload()
    return cleaned


def recipients(path: Path | None = None) -> list[str]:
    data = _read(path or settings_path())
    return [str(item).strip() for item in data.get("email", {}).get("recipients") or []]


def save_recipients(addresses: list[str], path: Path | None = None) -> list[str]:
    """Replace the report mailing list. Capitalization is kept as typed."""
    target = path or settings_path()
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in addresses:
        email = str(raw).strip()
        if not email:
            continue
        if not _EMAIL_RE.match(email):
            raise ControlStoreError(f"بريد غير صالح: {email}")
        key = email.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(email)
    if cleaned:
        block = "  recipients:\n" + "".join(f"    - {email}\n" for email in cleaned)
    else:
        block = "  recipients: []\n"
    text = target.read_text(encoding="utf-8")
    updated, count = _RECIPIENTS_RE.subn(block, text, count=1)
    if count != 1:
        raise ControlStoreError("تعذر العثور على قائمة البريد في الإعدادات.")
    target.write_text(updated, encoding="utf-8")
    _reload()
    return cleaned


def _platform_url(platform: str, handle: str) -> str:
    if platform == "instagram":
        return f"https://www.instagram.com/{handle.lstrip('@')}"
    if platform == "x":
        return f"https://x.com/{handle.lstrip('@')}"
    if platform == "youtube":
        name = handle if handle.startswith("@") else f"@{handle}"
        return f"https://www.youtube.com/{name}"
    if platform == "linkedin":
        return f"https://www.linkedin.com/company/{handle.strip('/')}/"
    raise ControlStoreError(f"منصة غير معروفة: {platform}")


def _write_accounts(data: dict, path: Path) -> None:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    marker = original.find("entities:")
    header = original[:marker] if marker > 0 else ""
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
    path.write_text(header + body, encoding="utf-8")
    _reload()


def competitors(path: Path | None = None) -> list[dict]:
    data = _read(path or accounts_path())
    rows = []
    for entity in data.get("entities") or []:
        if entity.get("owned"):
            continue
        platforms = entity.get("platforms") or {}
        rows.append(
            {
                "key": entity.get("key"),
                "short_name_ar": entity.get("short_name_ar") or entity.get("name_ar"),
                "name_ar": entity.get("name_ar"),
                "name_en": entity.get("name_en") or "",
                "platforms": {
                    name: (account or {}).get("handle", "")
                    for name, account in platforms.items()
                    if name in COMPARABLE_PLATFORMS
                },
            }
        )
    return rows


def add_competitor(
    *,
    key: str,
    short_name_ar: str,
    name_ar: str,
    name_en: str = "",
    handles: dict[str, str] | None = None,
    path: Path | None = None,
) -> dict:
    """Add one comparison office. At least one public account is required."""
    target = path or accounts_path()
    slug = key.strip().lower()
    if not _KEY_RE.match(slug):
        raise ControlStoreError("الرمز بالانقليزي: أحرف إنجليزية صغيرة وأرقام، مثل najran.")
    short_name = short_name_ar.strip()
    full_name = name_ar.strip() or short_name
    if not short_name:
        raise ControlStoreError("اكتب الاسم المختصر الذي يظهر في الرسم.")
    platforms = {}
    for platform in COMPARABLE_PLATFORMS:
        handle = str((handles or {}).get(platform) or "").strip()
        if not handle:
            continue
        platforms[platform] = {"handle": handle, "url": _platform_url(platform, handle)}
    if not platforms:
        raise ControlStoreError("أضف حساباً واحداً على الأقل: إنستغرام أو إكس أو يوتيوب أو لينكدإن.")

    data = _read(target)
    entities = list(data.get("entities") or [])
    if any(str(entity.get("key")) == slug for entity in entities):
        raise ControlStoreError("هذا الرمز مستخدم لجهة موجودة.")
    entity = {
        "key": slug,
        "name_ar": full_name,
        "short_name_ar": short_name,
        "name_en": name_en.strip() or full_name,
        "owned": False,
        "platforms": platforms,
    }
    entities.append(entity)
    data["entities"] = entities
    _write_accounts(data, target)
    return entity


def remove_competitor(key: str, path: Path | None = None) -> None:
    """Drop a comparison office. Our own accounts cannot be removed here."""
    target = path or accounts_path()
    data = _read(target)
    entities = list(data.get("entities") or [])
    kept = []
    removed = False
    for entity in entities:
        if str(entity.get("key")) == key:
            if entity.get("owned"):
                raise ControlStoreError("لا يمكن حذف حسابات الجوف من صفحة التحكم.")
            removed = True
            continue
        kept.append(entity)
    if not removed:
        raise ControlStoreError("الجهة غير موجودة.")
    data["entities"] = kept
    _write_accounts(data, target)
