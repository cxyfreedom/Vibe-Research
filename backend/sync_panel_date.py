"""Import a single day from a legacy market SQLite database into PostgreSQL."""

from __future__ import annotations

import argparse
import json

import market_store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(market_store.import_panel_sqlite_date(
        args.sqlite_path, args.date, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
