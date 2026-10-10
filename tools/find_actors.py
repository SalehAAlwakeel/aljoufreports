"""Search the Apify store for actors matching a query.

Diagnostic helper for picking or replacing an actor without leaving the
terminal. Community actors get renamed and deprecated often, so the IDs in
`config/settings.yaml` need to be checkable.

Usage:
    python tools/find_actors.py "linkedin comments"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from aljouf_report.config import load_config  # noqa: E402

query = " ".join(sys.argv[1:]) or "linkedin comments"
token = load_config().secrets.apify_token

response = httpx.get(
    "https://api.apify.com/v2/store",
    params={"search": query, "limit": 25, "sortBy": "popularity"},
    headers={"Authorization": f"Bearer {token}"},
    timeout=60,
)
response.raise_for_status()

for item in response.json()["data"]["items"]:
    pricing = item.get("currentPricingInfo") or {}
    model = pricing.get("pricingModel", "?")
    price = pricing.get("pricePerUnitUsd") or pricing.get("trialMinutes") or ""
    runs = (item.get("stats") or {}).get("totalRuns", 0)
    name = f"{item['username']}/{item['name']}"
    print(f"{name:52} {model:22} {str(price):8} runs={runs:,}")
    print(f"    {item.get('title', '')}")
