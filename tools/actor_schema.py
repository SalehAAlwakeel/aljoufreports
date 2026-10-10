"""Print an actor's input schema so run inputs can be built correctly.

Guessing an actor's input shape is the most common cause of a collector
failing with "Input is not valid", so read it from the source instead.

Usage:
    python tools/actor_schema.py apimaestro/linkedin-company-posts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from aljouf_report.config import load_config  # noqa: E402

actor_id = sys.argv[1].replace("/", "~")
token = load_config().secrets.apify_token
headers = {"Authorization": f"Bearer {token}"}

actor = httpx.get(
    f"https://api.apify.com/v2/acts/{actor_id}", headers=headers, timeout=60
).json()["data"]

build_id = (actor.get("taggedBuilds") or {}).get("latest", {}).get("buildId")
build = httpx.get(
    f"https://api.apify.com/v2/actor-builds/{build_id}", headers=headers, timeout=60
).json()["data"]

schema = build.get("inputSchema")
if isinstance(schema, str):
    schema = json.loads(schema)

print(f"=== {sys.argv[1]} ===")
for name, spec in (schema.get("properties") or {}).items():
    required = "REQUIRED" if name in (schema.get("required") or []) else ""
    print(f"{name:28} {spec.get('type','?'):10} {required}")
    if spec.get("title"):
        print(f"    {spec['title']}")
    if spec.get("prefill") is not None:
        print(f"    prefill: {json.dumps(spec['prefill'], ensure_ascii=True)[:160]}")
    if spec.get("default") is not None:
        print(f"    default: {json.dumps(spec['default'], ensure_ascii=True)[:160]}")
