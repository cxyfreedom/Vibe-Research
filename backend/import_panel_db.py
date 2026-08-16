"""Import a legacy market SQLite database into PostgreSQL."""

import argparse
import json

import market_store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sqlite_path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--news-only", action="store_true")
    args = parser.parse_args()
    if not args.dry_run:
        market_store.init_db()
    importer = market_store.import_news_sqlite if args.news_only else market_store.import_panel_sqlite
    print(json.dumps(importer(args.sqlite_path, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
