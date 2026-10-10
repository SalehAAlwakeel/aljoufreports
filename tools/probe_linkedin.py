"""One-off probe: does the LinkedIn follower actor accept a batched array input?"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from aljouf_report.collectors.apify import ApifyRunner  # noqa: E402
from aljouf_report.collectors.followers import SPECS, FollowerCollector  # noqa: E402
from aljouf_report.config import load_config  # noqa: E402

cfg = load_config()
runner = ApifyRunner(cfg.secrets.apify_token, cfg.settings.apify)
collector = FollowerCollector(cfg, runner)

pairs = cfg.accounts.with_platform("linkedin")
print(f"probing {len(pairs)} linkedin accounts in one run\n")

snapshots = collector._collect_batched("linkedin", SPECS["linkedin"], pairs)

print()
for snap in snapshots:
    value = f"{snap.followers:,}" if snap.followers is not None else "MISSING"
    print(f"{snap.handle:34} {value}")
print(f"\ncost: ${runner.total_cost_usd:.4f}")
