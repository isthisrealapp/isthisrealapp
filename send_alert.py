#!/usr/bin/env python3
"""Stub-send an alert for a feed item or a named service.

Only subscribers watching the affected service are targeted.
No real email is sent — each would-be send is logged to data/alert_log.jsonl.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from catalog import display_name, match_services_in_text
from store import connect, get_item, list_feed, list_subscribers, log_alert_send, seed_feed_if_empty


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def subscribers_for(db, services: list[str]) -> list[dict]:
    if not services:
        return []
    seen = set()
    out = []
    for key in services:
        for sub in list_subscribers(db, service=key):
            email = sub["email"]
            if email in seen:
                continue
            seen.add(email)
            out.append(sub)
    return out


def stub_send(sub: dict, payload: dict) -> None:
    dest = sub["email"]
    entry = {
        "ts": _now(),
        "subscriber": dest,
        "channel": "email",
        "destination": dest,
        "status": "stubbed",
        "note": "No real email sent.",
        **payload,
    }
    log_alert_send(entry)
    kind = payload.get("kind") or payload.get("tag") or "alert"
    headline = payload.get("headline") or payload.get("summary") or ""
    print(f"STUB email to {dest}: {kind} — {headline}")


def send_alert_for_item(db, item_id: str, services: list[str] | None = None) -> int:
    item = get_item(db, item_id)
    if not item:
        raise ValueError(f"No feed item with id {item_id}.")
    inferred = match_services_in_text(item["headline"])
    targets = services or inferred
    subscribers = subscribers_for(db, targets) if targets else []
    if not targets:
        log_alert_send(
            {
                "ts": _now(),
                "item_id": item["id"],
                "headline": item["headline"],
                "status": "stubbed",
                "note": "No service could be inferred from the headline; no one was targeted.",
                "sent": 0,
            }
        )
        print(f"STUB: no service matched for: {item['headline']}. Nobody notified.")
        return 0
    if not subscribers:
        log_alert_send(
            {
                "ts": _now(),
                "item_id": item["id"],
                "headline": item["headline"],
                "services": targets,
                "status": "stubbed",
                "note": "No early-access subscribers watching these services.",
                "sent": 0,
            }
        )
        print(f"STUB: no watchers for {', '.join(targets)}. Would have alerted about: {item['headline']}")
        return 0

    sent = 0
    for sub in subscribers:
        stub_send(
            sub,
            {
                "item_id": item["id"],
                "headline": item["headline"],
                "tag": item["tag"],
                "kind": item["tag"],
                "source_url": item["source_url"],
                "services": targets,
            },
        )
        sent += 1
    print(f"Stubbed {sent} alert(s) for {item['id']} → {', '.join(targets)}. Logged to data/alert_log.jsonl. Nothing was sent.")
    return sent


def send_status_alert(db, service: str, kind: str, summary: str, status_url: str | None = None) -> int:
    """Stub-send an incident or all-clear for a watched service."""
    if kind not in {"outage", "degraded", "all_clear", "attack"}:
        raise ValueError("kind must be outage, degraded, all_clear, or attack.")
    name = display_name(service)
    subscribers = subscribers_for(db, [service])
    headline = {
        "outage": f"{name} is reporting an official outage or incident.",
        "degraded": f"{name} is reporting a service issue.",
        "all_clear": f"{name} is back to official normal operation.",
        "attack": f"A cyberattack involving {name} has been confirmed.",
    }[kind]
    if summary and kind != "all_clear":
        headline = summary
    if not subscribers:
        log_alert_send(
            {
                "ts": _now(),
                "service": service,
                "kind": kind,
                "headline": headline,
                "status_url": status_url,
                "status": "stubbed",
                "note": "No early-access subscribers watching this service.",
                "sent": 0,
            }
        )
        print(f"STUB: no watchers for {service}. Would have sent {kind}: {headline}")
        return 0
    sent = 0
    for sub in subscribers:
        stub_send(
            sub,
            {
                "service": service,
                "kind": kind,
                "headline": headline,
                "summary": summary,
                "status_url": status_url,
            },
        )
        sent += 1
    print(f"Stubbed {sent} {kind} alert(s) for {service}. Logged to data/alert_log.jsonl. Nothing was sent.")
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stub-send an email alert to subscribers watching an affected service. Logs the send; does not email anyone."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", help="Feed item id")
    group.add_argument("--latest", action="store_true", help="Use the most recent feed item")
    group.add_argument("--service", help="Service key (e.g. github) for a status-page event")
    parser.add_argument("--kind", default="outage", help="For --service: outage, degraded, all_clear, or attack")
    parser.add_argument("--summary", default="", help="Optional official summary for --service")
    parser.add_argument("--status-url", default="", help="Optional official status URL for --service")
    args = parser.parse_args(argv)

    db = connect()
    seed_feed_if_empty(db)
    try:
        if args.service:
            send_status_alert(db, args.service, args.kind, args.summary, args.status_url or None)
            return 0
        item_id = args.id
        if args.latest:
            items = list_feed(db, limit=1)
            if not items:
                print("Error: the feed is empty.", file=sys.stderr)
                return 1
            item_id = items[0]["id"]
        send_alert_for_item(db, item_id)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
