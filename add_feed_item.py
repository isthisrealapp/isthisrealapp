#!/usr/bin/env python3
"""Add a verified item to the What's real right now feed. A source URL is required."""

from __future__ import annotations

import argparse
import sys

from store import VALID_TAGS, add_item, connect, seed_feed, seed_feed_if_empty


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add a verified outage, attack, or hoax to the feed. A source URL is required."
    )
    parser.add_argument("--headline", help="One-line headline")
    parser.add_argument("--tag", choices=sorted(VALID_TAGS), help="outage, attack, or hoax")
    parser.add_argument("--date", help="When it happened, YYYY-MM-DD")
    parser.add_argument("--end-date", help="Optional end date for a range, YYYY-MM-DD")
    parser.add_argument("--source-name", help="Name of the official or reputable source")
    parser.add_argument("--source-url", help="http(s) URL for that source (required)")
    parser.add_argument("--id", help="Optional stable id")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Load missing items from feed.json (use --seed --replace to reset the seed set)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="With --seed, replace existing seed items",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="After adding, stub-send an alert to early-access subscribers",
    )
    args = parser.parse_args(argv)

    db = connect()

    if args.seed:
        added = seed_feed(db, replace=args.replace)
        print(f"Seeded {added} feed item(s) from feed.json.")
        return 0

    seed_feed_if_empty(db)

    required = {
        "headline": args.headline,
        "tag": args.tag,
        "date": args.date,
        "source_name": args.source_name,
        "source_url": args.source_url,
    }
    missing = [name.replace("_", "-") for name, value in required.items() if not value]
    if missing:
        parser.error("Missing required arguments: " + ", ".join("--" + m for m in missing) + ". A source URL is required.")

    try:
        item = add_item(
            db,
            {
                "id": args.id,
                "headline": args.headline,
                "tag": args.tag,
                "occurred_on": args.date,
                "occurred_end": args.end_date,
                "source_name": args.source_name,
                "source_url": args.source_url,
            },
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Added {item['id']}: {item['headline']} ({item['tag']}) — {item['source_url']}")

    if args.notify:
        from send_alert import send_alert_for_item

        send_alert_for_item(db, item["id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
