"""Backfill global-index history into persisted daily-review snapshots."""

from __future__ import annotations

import argparse
import json

import gstock
import market_store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dates", nargs="*", help="YYYY-MM-DD; defaults to every saved daily review")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    market_store.init_db()
    dates = args.dates or market_store.available_dates("daily_review", "vibe", "")
    history = gstock.global_indices_for_dates(dates)
    incomplete = {day: len(history.get(day, [])) for day in dates if len(history.get(day, [])) != 5}
    if incomplete:
        raise RuntimeError(f"全球指数历史不完整，未写入：{incomplete}")

    updated = 0
    if not args.dry_run:
        for day in dates:
            updated += int(market_store.update_daily_review_global_indices(day, history[day]))
    print(json.dumps({"dates": sorted(dates), "snapshots": len(dates), "updated": updated,
                      "dry_run": args.dry_run}, ensure_ascii=False))


if __name__ == "__main__":
    main()
