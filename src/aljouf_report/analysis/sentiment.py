"""LLM sentiment analysis tuned for Saudi dialect, slang and sarcasm.

Comments are sent in batches and matched back by id. Results are persisted by
`comment_hash`, so the database doubles as a cache: a re-run only pays for
comments it has never seen.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Sequence

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import AppConfig
from ..models import SentimentResult, utcnow
from .prompts import (
    FEW_SHOT_ASSISTANT,
    FEW_SHOT_USER,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
)

log = logging.getLogger(__name__)

VALID_LABELS = {"Positive", "Negative", "Neutral"}

# The dashboard shows this in a narrow column; longer reasons get cut off
# mid-word and look like a bug. Eight Arabic words is enough to name the
# reason without the filler the model likes to add.
REASON_MAX_WORDS = 8


def shorten_reason(text: str, max_words: int = REASON_MAX_WORDS) -> str:
    """Keep the first `max_words` words so a verbose model cannot overflow."""
    words = (text or "").strip().split()
    if not words:
        return "بدون سبب محدد."
    clipped = " ".join(words[:max_words])
    if len(words) > max_words and not clipped.endswith("."):
        clipped += "."
    return clipped


class SentimentNotConfigured(RuntimeError):
    pass


class SentimentBatchError(RuntimeError):
    """Transient failure; worth retrying."""


class SentimentFatalError(RuntimeError):
    """Permanent failure. Retrying cannot help, so stop immediately."""


# Billing and auth failures arrive as 429/401 and look retryable, but no amount
# of backoff fixes an empty balance or a revoked key. Matching on the API's
# error codes lets a misconfigured account fail in a second instead of a minute.
FATAL_MARKERS = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_not_active",
    "invalid_api_key",
    "account_deactivated",
)


def _is_fatal(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in FATAL_MARKERS)


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class SentimentAnalyzer:
    def __init__(self, config: AppConfig):
        self.config = config
        self.model = config.secrets.openai_model
        self.errors: list[str] = []
        self._client: Any | None = None

    @property
    def available(self) -> bool:
        return bool(self.config.secrets.openai_api_key)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.available:
            raise SentimentNotConfigured(
                "OPENAI_API_KEY is not set, so comment sentiment cannot be analysed."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SentimentNotConfigured(
                "openai is not installed. Run: pip install -r requirements.txt"
            ) from exc
        # Retries are owned by tenacity on _analyze_batch. Leaving the SDK's
        # own retries enabled multiplies the two (3 x 3 attempts).
        self._client = OpenAI(
            api_key=self.config.secrets.openai_api_key, max_retries=0
        )
        return self._client

    # -- public API ------------------------------------------------------- #
    def analyze(self, comments: Sequence[dict[str, Any]]) -> list[SentimentResult]:
        """Score comments shaped like {'comment_hash': ..., 'text': ...}."""
        if not comments:
            return []
        if not self.available:
            msg = "sentiment: skipped, OPENAI_API_KEY not set"
            log.warning(msg)
            self.errors.append(msg)
            return []

        batch_size = self.config.settings.sentiment.batch_size
        max_chars = self.config.settings.sentiment.max_comment_chars
        results: list[SentimentResult] = []

        batches = list(_chunks(list(comments), batch_size))
        log.info(
            "Sentiment: analysing %d comments in %d batch(es) with %s",
            len(comments),
            len(batches),
            self.model,
        )

        for index, batch in enumerate(batches, start=1):
            payload = [
                {"id": c["comment_hash"], "text": str(c["text"])[:max_chars]}
                for c in batch
            ]
            try:
                results.extend(self._analyze_batch(payload))
                log.info("  batch %d/%d done", index, len(batches))
            except SentimentFatalError as exc:
                # Credentials or billing are broken; every later batch would
                # fail identically. Record once and stop.
                msg = f"sentiment: aborted at batch {index}/{len(batches)}: {exc}"
                log.error(msg)
                self.errors.append(msg)
                break
            except Exception as exc:  # noqa: BLE001 - one bad batch must not abort
                msg = f"sentiment: batch {index}/{len(batches)} failed: {exc}"
                log.warning(msg)
                self.errors.append(msg)

        return results

    # -- internals -------------------------------------------------------- #
    @retry(
        retry=retry_if_exception_type(SentimentBatchError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=20),
        reraise=True,
    )
    def _analyze_batch(self, payload: list[dict[str, str]]) -> list[SentimentResult]:
        client = self._get_client()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": FEW_SHOT_USER},
            {"role": "assistant", "content": FEW_SHOT_ASSISTANT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=1),
            },
        ]

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": RESPONSE_SCHEMA,
                },
            )
        except Exception as exc:
            message = str(exc)
            if _is_fatal(message):
                raise SentimentFatalError(message) from exc
            raise SentimentBatchError(message) from exc

        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise SentimentBatchError("Empty response from the model.")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SentimentBatchError(f"Unparseable JSON: {exc}") from exc

        return self._to_results(parsed.get("results") or [], payload)

    def _to_results(
        self, raw_results: list[dict[str, Any]], payload: list[dict[str, str]]
    ) -> list[SentimentResult]:
        valid_ids = {item["id"] for item in payload}
        now = utcnow()
        results: list[SentimentResult] = []

        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            comment_hash = str(entry.get("id") or "")
            if comment_hash not in valid_ids:
                # Hallucinated or mangled id: drop it rather than mislabel a
                # different comment.
                log.debug("Sentiment: discarding result with unknown id %r", comment_hash)
                continue

            label = str(entry.get("label") or "").strip().title()
            if label not in VALID_LABELS:
                label = "Neutral"

            confidence = entry.get("confidence")
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = None

            results.append(
                SentimentResult(
                    comment_hash=comment_hash,
                    label=label,  # type: ignore[arg-type]
                    reason_ar=shorten_reason(str(entry.get("reason_ar") or "")),
                    confidence=confidence,
                    model=self.model,
                    analyzed_at=now,
                )
            )

        missing = valid_ids - {r.comment_hash for r in results}
        if missing:
            log.warning("Sentiment: model skipped %d comment(s) in this batch", len(missing))
        return results
