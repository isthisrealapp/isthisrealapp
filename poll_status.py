#!/usr/bin/env python3
"""Poll official status pages for watched services and stub-log alerts.

This script is meant to be run by a scheduler later. It is not scheduled here.
On the first run it only writes a baseline snapshot — it does not alert on
already-open incidents. Later runs log a stub email for each watcher when a
service goes degraded/down, and an all-clear when it returns to official
normal. No real email is sent.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from catalog import BOARD_KEYS
from live_sources import gather_status_board
from send_alert import send_status_alert
from store import DATA_DIR, connect, list_subscribers

SNAPSHOT = DATA_DIR / "status_snapshot.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_snapshot() -> dict:
    if not SNAPSHOT.exists():
        return {}
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_snapshot(payload: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def watched_keys(db) -> set[str]:
    keys = set()
    any_legacy = False
    for sub in list_subscribers(db):
        if sub.get("watchlist"):
            keys.update(sub["watchlist"])
        else:
            any_legacy = True
    if any_legacy or not keys:
        keys.update(BOARD_KEYS)
    # Only poll services that have official status pages.
    return {key for key in keys if key in BOARD_KEYS or key in set(BOARD_KEYS)}


def level_kind(level: str) -> str | None:
    if level == "down":
        return "outage"
    if level == "degraded":
        return "degraded"
    return None


def poll(budget: float = 8.0) -> dict:
    db = connect()
    keys = sorted(watched_keys(db) & set(BOARD_KEYS))
    if not keys:
        keys = list(BOARD_KEYS)
    board = gather_status_board(keys=keys, budget=budget)
    current = {}
    for item in board.get("items") or []:
        current[item["key"]] = {
            "level": item.get("level") or "unknown",
            "summary": item.get("summary") or "",
            "status_url": item.get("status_url"),
        }

    previous = load_snapshot()
    changes = []
    first_run = not previous
    if first_run:
        save_snapshot({"ts": _now(), "services": current})
        print(f"Baseline snapshot saved for {len(current)} services. No alerts on first run.")
        return {"first_run": True, "services": len(current), "changes": []}

    prev_services = previous.get("services") or previous
    for key, now in current.items():
        was = (prev_services.get(key) or {}).get("level") or "unknown"
        became = now["level"]
        if was == became:
            continue
        was_bad = was in {"down", "degraded"}
        now_bad = became in {"down", "degraded"}
        if now_bad and not was_bad:
            kind = level_kind(became)
            changes.append((key, kind, now))
        elif was_bad and became == "ok":
            changes.append((key, "all_clear", now))
        elif was == "degraded" and became == "down":
            changes.append((key, "outage", now))
        elif was == "down" and became == "degraded":
            # Still an official issue — don't send a premature all-clear.
            continue

    sent = 0
    for key, kind, now in changes:
        print(f"Change: {key} → {kind}: {now['summary']}")
        sent += send_status_alert(db, key, kind, now["summary"], now.get("status_url"))

    save_snapshot({"ts": _now(), "services": current})
    if not changes:
        print(f"No status changes across {len(current)} services. Snapshot updated. Nothing was sent.")
    else:
        print(f"{len(changes)} change(s), {sent} stub alert(s) logged. Nothing was sent.")
    return {"first_run": False, "services": len(current), "changes": [c[0] for c in changes], "sent": sent}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Poll official status pages and stub-log watchlist alerts. Not scheduled."
    )
    parser.add_argument("--budget", type=float, default=8.0, help="Seconds allowed for status fetches")
    args = parser.parse_args(argv)
    try:
        poll(budget=args.budget)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
