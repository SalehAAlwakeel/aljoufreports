"""Apify client wrapper.

Apify runs public-web scrapers on our behalf, which is how we get follower
counts and comments without any social-platform API key, OAuth consent, or
admin login.
"""

from __future__ import annotations

import logging
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import ApifySettings

log = logging.getLogger(__name__)


class ApifyNotConfigured(RuntimeError):
    pass


class ApifyRunError(RuntimeError):
    pass


class ApifyRunner:
    def __init__(self, token: str | None, settings: ApifySettings):
        self.token = token
        self.settings = settings
        self._client: Any | None = None

    @property
    def available(self) -> bool:
        return bool(self.token)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.token:
            raise ApifyNotConfigured(
                "APIFY_TOKEN is not set. Follower and comment collection need it. "
                "Create a token at https://console.apify.com/account/integrations"
            )
        try:
            from apify_client import ApifyClient
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ApifyNotConfigured(
                "apify-client is not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._client = ApifyClient(self.token)
        return self._client

    @retry(
        retry=retry_if_exception_type(ApifyRunError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        reraise=True,
    )
    def run(self, actor_id: str, run_input: dict[str, Any]) -> list[dict[str, Any]]:
        """Run an actor to completion and return its dataset items."""
        client = self._get_client()
        log.info("Apify: running actor %s", actor_id)
        try:
            run = client.actor(actor_id).call(
                run_input=run_input,
                timeout_secs=self.settings.timeout_secs,
                memory_mbytes=self.settings.memory_mbytes,
            )
        except Exception as exc:
            raise ApifyRunError(f"Actor {actor_id} failed to start: {exc}") from exc

        if not run:
            raise ApifyRunError(f"Actor {actor_id} returned no run metadata.")

        status = run.get("status")
        if status != "SUCCEEDED":
            # A TIMED-OUT run often still has partial results worth keeping.
            log.warning("Apify: actor %s finished with status %s", actor_id, status)

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise ApifyRunError(f"Actor {actor_id} produced no dataset.")

        items = list(client.dataset(dataset_id).iterate_items())
        log.info("Apify: actor %s returned %d items", actor_id, len(items))
        return items

    def run_safe(
        self, actor_id: str, run_input: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Like `run`, but converts failures into an error string.

        One broken actor should never abort the weekly report, so every
        collector call goes through here.
        """
        try:
            return self.run(actor_id, run_input), None
        except (ApifyNotConfigured, ApifyRunError) as exc:
            log.warning("Apify: %s", exc)
            return [], str(exc)
        except Exception as exc:  # noqa: BLE001 - defensive boundary
            log.exception("Apify: unexpected failure in %s", actor_id)
            return [], f"{type(exc).__name__}: {exc}"
