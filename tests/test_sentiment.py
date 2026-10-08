"""Sentiment parsing is defensive because the model output is untrusted input."""

import json
from types import SimpleNamespace

import pytest

from aljouf_report.analysis.sentiment import SentimentAnalyzer
from aljouf_report.config import load_config


@pytest.fixture
def analyzer(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    load_config.cache_clear()
    config = load_config()
    load_config.cache_clear()
    return SentimentAnalyzer(config)


def _fake_response(payload: dict):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
            )
        ]
    )


def test_batch_results_map_back_to_comment_hashes(analyzer, monkeypatch):
    payload = [{"id": "h1", "text": "شغل مرتب"}, {"id": "h2", "text": "وش ذا الهبال"}]
    response = _fake_response(
        {
            "results": [
                {
                    "id": "h1",
                    "label": "Positive",
                    "reason_ar": "ثناء على العمل.",
                    "confidence": 0.95,
                },
                {
                    "id": "h2",
                    "label": "Negative",
                    "reason_ar": "انتقاد حاد.",
                    "confidence": 0.9,
                },
            ]
        }
    )
    monkeypatch.setattr(
        analyzer,
        "_get_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: response)
            )
        ),
    )

    results = analyzer._analyze_batch(payload)
    assert {r.comment_hash: r.label for r in results} == {
        "h1": "Positive",
        "h2": "Negative",
    }


def test_unknown_ids_are_discarded_not_misassigned(analyzer, monkeypatch):
    payload = [{"id": "h1", "text": "نص"}]
    response = _fake_response(
        {
            "results": [
                {"id": "h1", "label": "Neutral", "reason_ar": "استفسار.", "confidence": 0.8},
                {
                    "id": "hallucinated",
                    "label": "Positive",
                    "reason_ar": "لا يوجد.",
                    "confidence": 0.9,
                },
            ]
        }
    )
    monkeypatch.setattr(
        analyzer,
        "_get_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: response)
            )
        ),
    )

    results = analyzer._analyze_batch(payload)
    assert [r.comment_hash for r in results] == ["h1"]


def test_invalid_label_falls_back_to_neutral(analyzer):
    payload = [{"id": "h1", "text": "نص"}]
    results = analyzer._to_results(
        [{"id": "h1", "label": "Mixed", "reason_ar": "غير واضح.", "confidence": 0.5}],
        payload,
    )
    assert results[0].label == "Neutral"


def test_confidence_is_clamped_and_optional(analyzer):
    payload = [{"id": "h1", "text": "a"}, {"id": "h2", "text": "b"}]
    results = analyzer._to_results(
        [
            {"id": "h1", "label": "Positive", "reason_ar": "x", "confidence": 5},
            {"id": "h2", "label": "Positive", "reason_ar": "x", "confidence": "n/a"},
        ],
        payload,
    )
    assert results[0].confidence == 1.0
    assert results[1].confidence is None


def test_missing_reason_gets_a_placeholder(analyzer):
    results = analyzer._to_results(
        [{"id": "h1", "label": "Positive", "reason_ar": "", "confidence": 0.9}],
        [{"id": "h1", "text": "a"}],
    )
    assert results[0].reason_ar == "بدون سبب محدد."


def test_analyze_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    load_config.cache_clear()
    config = load_config()
    load_config.cache_clear()

    analyzer = SentimentAnalyzer(config)
    assert analyzer.analyze([{"comment_hash": "h1", "text": "نص"}]) == []
    assert analyzer.errors  # The skip is recorded as a warning.
