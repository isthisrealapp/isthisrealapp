"""IsThisRealApp — local Flask app."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

from checker import check as run_check
from share_card import render_share_card
from store import (
    DATA_DIR,
    DB_PATH,
    add_signup,
    connect,
    list_feed,
    seed_feed_if_empty,
)

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")

app = Flask(
    __name__,
    static_folder=str(ROOT / "static"),
    template_folder=str(ROOT / "templates"),
)
app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


def get_db() -> sqlite3.Connection:
    db = getattr(g, "_db", None)
    if db is None:
        db = connect()
        seed_feed_if_empty(db)
        g._db = db
    return db


@app.teardown_appcontext
def close_db(_exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


@app.get("/")
def index():
    feed = list_feed(get_db(), limit=5)
    return render_template("index.html", feed=feed)


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "img/favicon.svg", mimetype="image/svg+xml")


@app.post("/api/check")
def api_check():
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or request.form.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Paste a text, email, link, or rumor to check."}), 400
    if len(query) > 8000:
        return jsonify({"error": "That's a bit long — try pasting the key part of the message."}), 400

    result = run_check(query, use_llm=True)
    now = datetime.now(ET)
    result["checked_at"] = now.strftime("%b %-d, %Y  ·  Eastern Time")
    result["checked_at_iso"] = now.isoformat()
    check_id = uuid.uuid4().hex[:12]
    result["id"] = check_id
    result["share_url"] = f"/api/share/{check_id}.png"

    db = get_db()
    db.execute(
        "INSERT INTO checks (id, query, verdict, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            check_id,
            query,
            result["verdict"],
            json.dumps(result, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    db.commit()
    return jsonify(result)


@app.get("/api/share/<check_id>.png")
def api_share(check_id: str):
    if not re.fullmatch(r"[a-f0-9]{8,32}", check_id):
        return jsonify({"error": "Not found."}), 404
    db = get_db()
    row = db.execute("SELECT result_json FROM checks WHERE id = ?", (check_id,)).fetchone()
    if not row:
        return jsonify({"error": "That verdict is no longer available. Run the check again."}), 404
    result = json.loads(row["result_json"])
    png = render_share_card(result)
    return Response(
        png,
        mimetype="image/png",
        headers={
            "Content-Disposition": f'inline; filename="isthisrealapp-{result.get("verdict", "check")}.png"',
            "Cache-Control": "public, max-age=86400",
        },
    )


@app.post("/api/share")
def api_share_direct():
    """Generate a card from a posted result without a stored id (preview/fallback)."""
    payload = request.get_json(silent=True) or {}
    if not payload.get("verdict"):
        return jsonify({"error": "Missing verdict."}), 400
    png = render_share_card(payload)
    return Response(png, mimetype="image/png")


@app.get("/api/feed")
def api_feed():
    try:
        limit = int(request.args.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 50))
    return jsonify({"items": list_feed(get_db(), limit=limit)})


@app.post("/api/alerts")
def api_alerts():
    payload = request.get_json(silent=True) or {}
    email = payload.get("email") or ""
    phone = payload.get("phone") or ""
    channel = payload.get("channel") or "email"
    try:
        result = add_signup(get_db(), email, phone, channel)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(result)


@app.post("/api/waitlist")
def api_waitlist():
    """Back-compat: treat an email-only waitlist signup as email-channel early access."""
    payload = request.get_json(silent=True) or {}
    try:
        result = add_signup(get_db(), payload.get("email") or "", payload.get("phone") or "", "email")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(result)


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "isthisrealapp"})


def main():
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    print(f"IsThisRealApp running at http://127.0.0.1:{port}")
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
