"""Inspect recent Apify runs and their datasets.

Diagnostic helper: lets us see what an actor actually returned without paying
for another run. Useful when a collector comes back empty and we need to tell
"the account has no comments" apart from "the actor could not read them".

Usage:
    python tools/inspect_runs.py              # list recent runs
    python tools/inspect_runs.py <runId>      # dump one run's dataset keys
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from apify_client import ApifyClient  # noqa: E402

from aljouf_report.config import load_config  # noqa: E402

INTERESTING = (
    "commentsCount",
    "comments_count",
    "likesCount",
    "timestamp",
    "url",
    "shortCode",
    "type",
    "ownerUsername",
)


def main() -> None:
    cfg = load_config()
    client = ApifyClient(cfg.secrets.apify_token)

    if len(sys.argv) > 1:
        run_id = sys.argv[1]
        run = client.run(run_id).get()
        dataset_id = getattr(run, "default_dataset_id", None)
        items = list(client.dataset(dataset_id).iterate_items())
        print(f"run {run_id}: {len(items)} items\n")
        for item in items:
            summary = {k: item.get(k) for k in INTERESTING if k in item}
            print(json.dumps(summary, ensure_ascii=True, default=str))
            if not summary:
                print("  keys:", sorted(item.keys())[:25])
        return

    runs = client.runs().list(limit=10, desc=True).items
    for run in runs:
        get = run.get if isinstance(run, dict) else lambda k, d=None: getattr(run, k, d)
        print(
            f"{get('id')}  {get('actId')}  {get('status')}  "
            f"started={get('startedAt')}  dataset={get('defaultDatasetId')}"
        )


if __name__ == "__main__":
    main()
