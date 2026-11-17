#!/usr/bin/env python3
"""Stub-send an Alerts notification for a feed item. No real email or SMS is sent."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from store import connect, get_item, list_feed, list_subscribers, log_alert_send, seed_feed_if_empty


def send_alert_for_item(db, item_id: str) -> int:
    item = get_item(db, item_id)
    if not item:
        raise ValueError(f"No feed item with id {item_id}.")
    subscribers = list_subscribers(db)
    now = datetime.now(timezone.utc).isoformat()
    if not subscribers:
        log_alert_send(
            {
                "ts": now,
                "item_id": item["id"],
                "headline": item["headline"],
                "status": "stubbed",
                "note": "No early-access subscribers to notify.",
                "sent": 0,
            }
        )
        print(f"STUB: no subscribers. Would have alerted about: {item['headline']}")
        return 0

    sent = 0
    for sub in subscribers:
        channel = sub["channel"]
        dest = sub["phone"] if channel == "text" else sub["email"]
        entry = {
            "ts": now,
            "item_id": item["id"],
            "headline": item["headline"],
            "tag": item["tag"],
            "source_url": item["source_url"],
            "subscriber": sub["email"],
            "channel": channel,
            "destination": dest,
            "status": "stubbed",
            "note": "No real email or SMS sent.",
        }
        log_alert_send(entry)
        print(
            f"STUB {channel} to {dest}: {item['tag_label']} — {item['headline']} "
            f"({item['display_date']}) {item['source_url']}"
        )
        sent += 1
    print(f"Stubbed {sent} alert(s) for {item['id']}. Logged to data/alert_log.jsonl. Nothing was sent.")
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stub-send an alert for a verified feed item. Logs the send; does not email or text anyone."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", help="Feed item id")
    group.add_argument("--latest", action="store_true", help="Use the most recent feed item")
    args = parser.parse_args(argv)

    db = connect()
    seed_feed_if_empty(db)
    item_id = args.id
    if args.latest:
        items = list_feed(db, limit=1)
        if not items:
            print("Error: the feed is empty.", file=sys.stderr)
            return 1
        item_id = items[0]["id"]
    try:
        send_alert_for_item(db, item_id)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
