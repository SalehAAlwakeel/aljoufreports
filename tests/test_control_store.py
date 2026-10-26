"""The control page writes the files the collectors and the email job read."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from aljouf_report.config import load_config
from aljouf_report.control_store import (
    ControlStoreError,
    add_competitor,
    competitors,
    keywords,
    recipients,
    remove_competitor,
    save_keywords,
    save_recipients,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings_copy(tmp_path: Path) -> Path:
    target = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", target)
    return target


@pytest.fixture
def accounts_copy(tmp_path: Path) -> Path:
    target = tmp_path / "accounts.yaml"
    shutil.copy(ROOT / "config" / "accounts.yaml", target)
    return target


def test_keywords_round_trip_keeps_the_rest_of_the_file(settings_copy: Path):
    original = keywords(settings_copy)
    assert any(item["query"] == "سكاكا" for item in original)
    saved = save_keywords(
        [item for item in original if item["query"] != "صوير"]
        + [{"query": "كلمة تجريبية", "label": "تجربة", "scope": "local"}],
        settings_copy,
    )
    assert "صوير" not in {item["query"] for item in saved}
    added = next(item for item in saved if item["query"] == "كلمة تجريبية")
    assert added["language"] == "ar"
    assert added["scope"] == "local"
    text = settings_copy.read_text(encoding="utf-8")
    assert "trusted_sources:" in text
    assert "subject_prefix:" in text
    assert "صوير" not in text


def test_duplicate_keyword_is_rejected(settings_copy: Path):
    with pytest.raises(ControlStoreError):
        save_keywords(
            [
                {"query": "الجوف", "scope": "local"},
                {"query": "الجوف", "scope": "local"},
            ],
            settings_copy,
        )


def test_recipients_keep_capitalization_and_win_over_the_environment(
    settings_copy: Path, accounts_copy: Path, monkeypatch: pytest.MonkeyPatch
):
    with pytest.raises(ControlStoreError):
        save_recipients(["not-an-email"], settings_copy)

    saved = save_recipients(
        ["Desk@jsdo.gov.sa", "desk@jsdo.gov.sa", "second@example.com"],
        settings_copy,
    )
    assert saved == ["Desk@jsdo.gov.sa", "second@example.com"]
    assert recipients(settings_copy) == saved

    monkeypatch.setenv("EMAIL_RECIPIENTS", "other@example.com")
    load_config.cache_clear()
    config = load_config(str(settings_copy), str(accounts_copy))
    assert config.recipients == ["Desk@jsdo.gov.sa", "second@example.com"]
    load_config.cache_clear()


def test_add_and_remove_competitor_keeps_our_snapchat_count(accounts_copy: Path):
    before = yaml.safe_load(accounts_copy.read_text(encoding="utf-8"))
    owned = next(entity for entity in before["entities"] if entity["owned"])
    snapchat = owned["platforms"]["snapchat"]["followers"]

    add_competitor(
        key="najran",
        short_name_ar="نجران",
        name_ar="هيئة تطوير نجران",
        handles={"x": "NajranOffice", "instagram": ""},
        path=accounts_copy,
    )
    names = {row["short_name_ar"] for row in competitors(accounts_copy)}
    assert "نجران" in names

    remove_competitor("hail", accounts_copy)
    names = {row["short_name_ar"] for row in competitors(accounts_copy)}
    assert "نجران" in names
    assert "حائل" not in names

    after = yaml.safe_load(accounts_copy.read_text(encoding="utf-8"))
    owned_after = next(entity for entity in after["entities"] if entity["owned"])
    assert owned_after["platforms"]["snapchat"]["followers"] == snapchat
    with pytest.raises(ControlStoreError):
        remove_competitor(owned_after["key"], accounts_copy)
