"""Apify client wrapper.

Apify runs public-web scrapers on our behalf, which is how we get follower
counts and comments without any social-platform API key, OAuth consent, or
admin login.
"""

from __future__ import annotations

import inspect
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import ApifySettings

log = logging.getLogger(__name__)


def _field(run: Any, snake: str, camel: str) -> Any:
    """Read a run field across apify-client generations.

    apify-client <3 returned plain dicts with camelCase keys; 3.x returns a
    typed model with snake_case attributes. Supporting both keeps the pipeline
    working when the dependency is upgraded or pinned differently.
    """
    if run is None:
        return None
    value = getattr(run, snake, None)
    if value is not None:
        return value
    if isinstance(run, dict):
        return run.get(camel) or run.get(snake)
    return None


class ApifyNotConfigured(RuntimeError):
    pass


class ApifyRunError(RuntimeError):
    pass


class ApifyRunner:
    def __init__(self, token: str | None, settings: ApifySettings):
        self.token = token
        self.settings = settings
        self.total_cost_usd = 0.0
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

    def _call(self, client: Any, actor_id: str, run_input: dict[str, Any]) -> Any:
        """Invoke an actor, adapting to the installed client's signature."""
        actor = client.actor(actor_id)
        params = inspect.signature(actor.call).parameters

        kwargs: dict[str, Any] = {"run_input": run_input}
        if "memory_mbytes" in params:
            kwargs["memory_mbytes"] = self.settings.memory_mbytes

        # The run time limit was renamed between client generations.
        if "run_timeout" in params:
            kwargs["run_timeout"] = timedelta(seconds=self.settings.timeout_secs)
        elif "timeout_secs" in params:
            kwargs["timeout_secs"] = self.settings.timeout_secs

        # Hard spend ceiling per actor run, so a misconfigured input cannot
        # drain an Apify balance.
        if self.settings.max_charge_usd and "max_total_charge_usd" in params:
            kwargs["max_total_charge_usd"] = Decimal(str(self.settings.max_charge_usd))

        return actor.call(**kwargs)

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
            run = self._call(client, actor_id, run_input)
        except Exception as exc:
            raise ApifyRunError(f"Actor {actor_id} failed to start: {exc}") from exc

        if not run:
            raise ApifyRunError(f"Actor {actor_id} returned no run metadata.")

        status = _field(run, "status", "status")
        status = getattr(status, "value", status)
        if status != "SUCCEEDED":
            # A TIMED-OUT run often still has partial results worth keeping.
            log.warning("Apify: actor %s finished with status %s", actor_id, status)

        dataset_id = _field(run, "default_dataset_id", "defaultDatasetId")
        if not dataset_id:
            raise ApifyRunError(f"Actor {actor_id} produced no dataset.")

        items = list(client.dataset(dataset_id).iterate_items())

        cost = _field(run, "usage_total_usd", "usageTotalUsd")
        self.total_cost_usd += float(cost or 0)
        log.info(
            "Apify: actor %s returned %d items (cost $%.4f, session total $%.4f)",
            actor_id,
            len(items),
            float(cost or 0),
            self.total_cost_usd,
        )
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
