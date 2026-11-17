"""SQLite helpers for the verified feed and Alerts early-access signups."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "isthisreal.db"
FEED_SEED = ROOT / "feed.json"
ALERT_LOG = DATA_DIR / "alert_log.jsonl"

VALID_TAGS = {"outage", "attack", "hoax"}
VALID_CHANNELS = {"email", "text"}
EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
PHONE_RE = re.compile(r"^\+?[\d\s().\-]{7,20}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TAG_LABELS = {"outage": "Outage", "attack": "Attack", "hoax": "Hoax"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS waitlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    plan TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    verdict TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_items (
    id TEXT PRIMARY KEY,
    headline TEXT NOT NULL,
    tag TEXT NOT NULL,
    occurred_on TEXT NOT NULL,
    occurred_end TEXT,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts_signups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    channel TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'early_access',
    created_at TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(path or DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.commit()
    return db


def seed_feed_if_empty(db: sqlite3.Connection) -> int:
    count = db.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0]
    if count:
        return 0
    return seed_feed(db)


def seed_feed(db: sqlite3.Connection, replace: bool = False) -> int:
    data = json.loads(FEED_SEED.read_text(encoding="utf-8"))
    items = data.get("items") or []
    added = 0
    if replace:
        db.execute("DELETE FROM feed_items")
    for raw in items:
        item = normalize_item(raw)
        exists = db.execute("SELECT id FROM feed_items WHERE id = ?", (item["id"],)).fetchone()
        if exists and not replace:
            continue
        upsert_item(db, item)
        added += 1
    db.commit()
    return added


def validate_source_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("A source URL is required, and it must start with http:// or https://.")
    return url


def _valid_date(value: str, label: str) -> str:
    value = (value or "").strip()
    if not DATE_RE.match(value):
        raise ValueError(f"{label} must be YYYY-MM-DD.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not a real date.") from exc
    return value


def normalize_item(raw: dict, generate_id: bool = False) -> dict:
    headline = " ".join((raw.get("headline") or "").split())
    if not headline:
        raise ValueError("A headline is required.")
    tag = (raw.get("tag") or "").strip().lower()
    if tag not in VALID_TAGS:
        raise ValueError("Tag must be one of: outage, attack, hoax.")
    occurred_on = _valid_date(raw.get("occurred_on") or raw.get("date") or "", "Date")
    occurred_end = raw.get("occurred_end") or raw.get("end_date") or None
    if occurred_end:
        occurred_end = _valid_date(occurred_end, "End date")
        if occurred_end < occurred_on:
            raise ValueError("End date can't be before the start date.")
    source_name = " ".join((raw.get("source_name") or "").split())
    if not source_name:
        raise ValueError("A source name is required.")
    source_url = validate_source_url(raw.get("source_url") or "")
    item_id = (raw.get("id") or "").strip()
    if not item_id:
        if not generate_id:
            raise ValueError("An id is required.")
        slug = re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")[:48]
        item_id = f"{slug}-{occurred_on}"
    created_at = raw.get("created_at") or datetime.now(timezone.utc).isoformat()
    return {
        "id": item_id,
        "headline": headline,
        "tag": tag,
        "occurred_on": occurred_on,
        "occurred_end": occurred_end,
        "source_name": source_name,
        "source_url": source_url,
        "created_at": created_at,
    }


def upsert_item(db: sqlite3.Connection, item: dict) -> None:
    db.execute(
        """
        INSERT INTO feed_items (id, headline, tag, occurred_on, occurred_end, source_name, source_url, created_at)
        VALUES (:id, :headline, :tag, :occurred_on, :occurred_end, :source_name, :source_url, :created_at)
        ON CONFLICT(id) DO UPDATE SET
            headline = excluded.headline,
            tag = excluded.tag,
            occurred_on = excluded.occurred_on,
            occurred_end = excluded.occurred_end,
            source_name = excluded.source_name,
            source_url = excluded.source_url
        """,
        item,
    )


def add_item(db: sqlite3.Connection, raw: dict) -> dict:
    item = normalize_item(raw, generate_id=True)
    existing = db.execute("SELECT id FROM feed_items WHERE id = ?", (item["id"],)).fetchone()
    if existing:
        raise ValueError(f"A feed item with id {item['id']} already exists.")
    upsert_item(db, item)
    db.commit()
    return item


def get_item(db: sqlite3.Connection, item_id: str) -> dict | None:
    row = db.execute("SELECT * FROM feed_items WHERE id = ?", (item_id,)).fetchone()
    return format_item(row) if row else None


def list_feed(db: sqlite3.Connection, limit: int = 5) -> list[dict]:
    rows = db.execute(
        """
        SELECT * FROM feed_items
        ORDER BY COALESCE(occurred_end, occurred_on) DESC, occurred_on DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [format_item(row) for row in rows]


def format_item(row: sqlite3.Row) -> dict:
    start = date.fromisoformat(row["occurred_on"])
    end = date.fromisoformat(row["occurred_end"]) if row["occurred_end"] else None
    if end and end != start:
        if start.month == end.month:
            display_date = f"{start.strftime('%b %-d')}–{end.strftime('%-d')}"
        else:
            display_date = f"{start.strftime('%b %-d')}–{end.strftime('%b %-d')}"
    else:
        display_date = start.strftime("%b %-d")
    return {
        "id": row["id"],
        "headline": row["headline"],
        "tag": row["tag"],
        "tag_label": TAG_LABELS.get(row["tag"], row["tag"].title()),
        "occurred_on": row["occurred_on"],
        "occurred_end": row["occurred_end"],
        "display_date": display_date,
        "source_name": row["source_name"],
        "source_url": row["source_url"],
    }


def add_signup(db: sqlite3.Connection, email: str, phone: str | None, channel: str) -> dict:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    channel = (channel or "email").strip().lower()
    if channel not in VALID_CHANNELS:
        raise ValueError("Choose email or text.")
    phone = re.sub(r"\s+", " ", (phone or "").strip()) or None
    if phone and not PHONE_RE.match(phone):
        raise ValueError("Enter a valid phone number, or leave it blank.")
    if channel == "text" and not phone:
        raise ValueError("A phone number is needed for text alerts.")
    now = datetime.now(timezone.utc).isoformat()
    existing = db.execute("SELECT id FROM alerts_signups WHERE email = ?", (email,)).fetchone()
    if existing:
        db.execute(
            "UPDATE alerts_signups SET phone = ?, channel = ?, created_at = ? WHERE email = ?",
            (phone, channel, now, email),
        )
        status = "updated"
    else:
        db.execute(
            "INSERT INTO alerts_signups (email, phone, channel, status, created_at) VALUES (?, ?, ?, 'early_access', ?)",
            (email, phone, channel, now),
        )
        status = "joined"
    db.commit()
    return {"ok": True, "status": status, "message": "You're on the early access list."}


def list_subscribers(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT email, phone, channel, status FROM alerts_signups ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def log_alert_send(entry: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with ALERT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
