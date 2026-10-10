"""Configuration: YAML for structure, environment variables for secrets."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

Platform = Literal["instagram", "x", "youtube", "linkedin"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
class PlatformAccount(BaseModel):
    handle: str
    url: str


class Entity(BaseModel):
    key: str
    name_ar: str
    name_en: str
    owned: bool = False
    platforms: dict[str, PlatformAccount] = Field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.name_ar or self.name_en

    def account(self, platform: str) -> PlatformAccount | None:
        return self.platforms.get(platform)


class AccountBook(BaseModel):
    entities: list[Entity]

    @property
    def owned(self) -> list[Entity]:
        return [e for e in self.entities if e.owned]

    @property
    def competitors(self) -> list[Entity]:
        return [e for e in self.entities if not e.owned]

    def by_key(self, key: str) -> Entity | None:
        return next((e for e in self.entities if e.key == key), None)

    def with_platform(self, platform: str) -> list[tuple[Entity, PlatformAccount]]:
        """Entities that actually have an account on `platform`."""
        pairs = []
        for entity in self.entities:
            account = entity.account(platform)
            if account:
                pairs.append((entity, account))
        return pairs


# --------------------------------------------------------------------------- #
# Settings (config/settings.yaml)
# --------------------------------------------------------------------------- #
class Windows(BaseModel):
    news_lookback_days: int = 14
    comments_lookback_days: int = 7


class NewsKeyword(BaseModel):
    query: str
    label: str
    language: str = "en"


class NewsSettings(BaseModel):
    sources: list[str] = Field(default_factory=lambda: ["google_news", "gdelt"])
    max_per_keyword: int = 40
    keywords: list[NewsKeyword] = Field(default_factory=list)


class FollowerSettings(BaseModel):
    platforms: list[str] = Field(
        default_factory=lambda: ["instagram", "x", "youtube", "linkedin"]
    )


class CommentSettings(BaseModel):
    owned_only: bool = True
    max_posts_per_account: int = 10
    max_comments_per_post: int = 50
    platforms: list[str] = Field(default_factory=lambda: ["instagram", "x", "youtube"])


class SentimentSettings(BaseModel):
    batch_size: int = 15
    max_comment_chars: int = 1200
    use_cache: bool = True


class ApifySettings(BaseModel):
    timeout_secs: int = 900
    memory_mbytes: int = 1024
    # Hard per-run spend ceiling in USD. Set to 0 to disable.
    max_charge_usd: float = 0.50
    actors: dict[str, str] = Field(default_factory=dict)

    def actor(self, name: str) -> str:
        try:
            return self.actors[name]
        except KeyError as exc:  # pragma: no cover - configuration error
            raise KeyError(
                f"No Apify actor configured for '{name}'. "
                f"Add it under apify.actors in config/settings.yaml."
            ) from exc


class EmailSettings(BaseModel):
    subject_prefix: str = "Weekly Social Report"
    recipients: list[str] = Field(default_factory=list)
    dashboard_url: str = "http://localhost:8501"


class DashboardSettings(BaseModel):
    # Reload the page on this interval so an unattended screen stays current.
    # 0 disables it. This only re-reads the database; it does not scrape.
    auto_refresh_minutes: int = 30


class StorageSettings(BaseModel):
    sqlite_path: str = "data/aljouf.db"
    sheets_enabled: bool = True


class Settings(BaseModel):
    windows: Windows = Field(default_factory=Windows)
    news: NewsSettings = Field(default_factory=NewsSettings)
    followers: FollowerSettings = Field(default_factory=FollowerSettings)
    comments: CommentSettings = Field(default_factory=CommentSettings)
    sentiment: SentimentSettings = Field(default_factory=SentimentSettings)
    apify: ApifySettings = Field(default_factory=ApifySettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)


# --------------------------------------------------------------------------- #
# Secrets / environment
# --------------------------------------------------------------------------- #
class Secrets(BaseModel):
    apify_token: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    google_sheets_id: str | None = None
    google_service_account_file: str | None = None
    google_service_account_json: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_from: str | None = None
    email_recipients: list[str] = Field(default_factory=list)

    dashboard_url: str | None = None
    log_level: str = "INFO"
    sqlite_path: str | None = None

    @property
    def google_credentials(self) -> dict[str, Any] | None:
        """Service account key, from inline JSON or a file on disk."""
        if self.google_service_account_json:
            return json.loads(self.google_service_account_json)
        if self.google_service_account_file:
            path = Path(self.google_service_account_file)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        return None

    @property
    def sheets_ready(self) -> bool:
        return bool(self.google_sheets_id and self.google_credentials)

    @property
    def smtp_ready(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.smtp_password)


def _env_secret(name: str) -> str | None:
    """Read a secret, treating an unedited .env.example placeholder as unset.

    The template ships values like `apify_api_xxxxxxxx`. Returning those as
    real credentials turns a configuration mistake into an opaque auth error.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if "xxxx" in lowered or lowered.startswith(("your-", "your_", "<")):
        return None
    return raw


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_secrets() -> Secrets:
    load_dotenv(PROJECT_ROOT / ".env")
    return Secrets(
        apify_token=_env_secret("APIFY_TOKEN"),
        openai_api_key=_env_secret("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
        google_sheets_id=_env_secret("GOOGLE_SHEETS_ID"),
        google_service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or None,
        google_service_account_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or None,
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=int(os.getenv("SMTP_PORT") or 587),
        smtp_username=os.getenv("SMTP_USERNAME") or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_use_tls=_env_bool("SMTP_USE_TLS", True),
        email_from=os.getenv("EMAIL_FROM") or None,
        email_recipients=_env_list("EMAIL_RECIPIENTS"),
        dashboard_url=os.getenv("DASHBOARD_URL") or None,
        log_level=os.getenv("LOG_LEVEL") or "INFO",
        sqlite_path=os.getenv("SQLITE_PATH") or None,
    )


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
class AppConfig(BaseModel):
    settings: Settings
    accounts: AccountBook
    secrets: Secrets

    @property
    def db_path(self) -> Path:
        raw = self.secrets.sqlite_path or self.settings.storage.sqlite_path
        path = Path(raw)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def dashboard_url(self) -> str:
        return self.secrets.dashboard_url or self.settings.email.dashboard_url

    @property
    def recipients(self) -> list[str]:
        return self.secrets.email_recipients or self.settings.email.recipients


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def load_config(
    settings_path: str | None = None,
    accounts_path: str | None = None,
) -> AppConfig:
    settings_file = Path(settings_path) if settings_path else CONFIG_DIR / "settings.yaml"
    accounts_file = Path(accounts_path) if accounts_path else CONFIG_DIR / "accounts.yaml"
    return AppConfig(
        settings=Settings.model_validate(_read_yaml(settings_file)),
        accounts=AccountBook.model_validate(_read_yaml(accounts_file)),
        secrets=load_secrets(),
    )
