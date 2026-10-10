"""Run an actor with an ad-hoc input and print the shape of its output.

The input is read from a JSON file rather than an argument because PowerShell
mangles inline JSON quoting.

Usage:
    python tools/probe_actor.py <actorId> <input.json>
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

from aljouf_report.collectors.apify import ApifyRunner  # noqa: E402
from aljouf_report.config import load_config  # noqa: E402

actor_id = sys.argv[1]
run_input = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

cfg = load_config()
runner = ApifyRunner(cfg.secrets.apify_token, cfg.settings.apify)
items = runner.run(actor_id, run_input)

print(f"items: {len(items)}  cost: ${runner.total_cost_usd:.4f}\n")
if items:
    print("top-level keys:", sorted(items[0].keys()))
    print()
    print(json.dumps(items[0], ensure_ascii=True, indent=1, default=str)[:3000])
