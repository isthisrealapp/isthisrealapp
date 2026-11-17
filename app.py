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
    make_response,
    render_template,
    request,
    send_from_directory,
)

from catalog import watchlist_catalog
from checker import check as run_check, validate_query
from live_sources import gather_status_board
from share_card import render_share_card
from store import (
    FREE_DAILY_LIMIT,
    add_signup,
    checks_remaining_today,
    connect,
    list_feed,
    list_history,
    record_check,
    seed_feed_if_empty,
)

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
CLIENT_COOKIE = "itr_client"

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


def _valid_client_id(value: str | None) -> str | None:
    if not value or not re.fullmatch(r"[a-f0-9]{32}", value):
        return None
    return value


def client_id_from_request() -> tuple[str, bool]:
    """Return (client_id, is_new)."""
    existing = _valid_client_id(request.cookies.get(CLIENT_COOKIE))
    if existing:
        return existing, False
    return uuid.uuid4().hex, True


def attach_client_cookie(resp, client_id: str, is_new: bool):
    if is_new:
        resp.set_cookie(
            CLIENT_COOKIE,
            client_id,
            max_age=60 * 60 * 24 * 365,
            samesite="Lax",
            httponly=True,
            secure=request.is_secure,
        )
    return resp


def _page_context():
    board = {"items": [], "checked_at_label": "", "checked_at_display": ""}
    try:
        board = gather_status_board(budget=3.5)
    except Exception:
        board = {"items": [], "checked_at_label": "", "checked_at_display": ""}
    return {
        "feed": list_feed(get_db(), limit=5),
        "board": board,
        "watchlist": watchlist_catalog(),
        "free_daily_limit": FREE_DAILY_LIMIT,
    }


@app.get("/")
def index():
    return render_template("index.html", **_page_context())


@app.get("/privacy")
def privacy():
    return render_template("legal.html", page="privacy", title="Privacy")


@app.get("/terms")
def terms():
    return render_template("legal.html", page="terms", title="Terms")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "img/favicon.svg", mimetype="image/svg+xml")


@app.post("/api/check")
def api_check():
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or request.form.get("query") or "").strip()
    error = validate_query(query)
    if error:
        return jsonify({"error": error}), 400
    if len(query) > 8000:
        return jsonify({"error": "That's a bit long — try pasting the key part of the message."}), 400

    client_id, is_new = client_id_from_request()
    db = get_db()
    remaining = checks_remaining_today(db, client_id) if not is_new else FREE_DAILY_LIMIT
    if remaining <= 0:
        resp = make_response(
            jsonify(
                {
                    "error": (
                        f"You've used today's {FREE_DAILY_LIMIT} free checks. "
                        "Pro will include unlimited checks — you can join early access below."
                    ),
                    "limit_reached": True,
                    "remaining": 0,
                    "daily_limit": FREE_DAILY_LIMIT,
                }
            ),
            429,
        )
        return attach_client_cookie(resp, client_id, is_new)

    result = run_check(query, use_llm=True)
    if result.get("error"):
        resp = make_response(jsonify({"error": result["error"]}), 400)
        return attach_client_cookie(resp, client_id, is_new)

    now = datetime.now(ET)
    result["checked_at"] = now.strftime("%b %-d, %Y  ·  Eastern Time")
    result["checked_at_iso"] = now.isoformat()
    check_id = uuid.uuid4().hex[:12]
    result["id"] = check_id
    result["share_url"] = f"/api/share/{check_id}.png"

    record_check(db, check_id, query, result["verdict"], result, client_id)
    result["remaining"] = checks_remaining_today(db, client_id)
    result["daily_limit"] = FREE_DAILY_LIMIT

    resp = make_response(jsonify(result))
    return attach_client_cookie(resp, client_id, is_new)


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


@app.get("/api/status-board")
def api_status_board():
    try:
        board = gather_status_board(budget=5.0)
    except Exception:
        board = {"items": [], "checked_at_label": "", "error": "Couldn't load official status just now."}
    return jsonify(board)


@app.get("/api/history")
def api_history():
    client_id, is_new = client_id_from_request()
    items = [] if is_new else list_history(get_db(), client_id, limit=10)
    remaining = FREE_DAILY_LIMIT if is_new else checks_remaining_today(get_db(), client_id)
    resp = make_response(
        jsonify({"items": items, "remaining": remaining, "daily_limit": FREE_DAILY_LIMIT})
    )
    return attach_client_cookie(resp, client_id, is_new)


@app.post("/api/alerts")
def api_alerts():
    payload = request.get_json(silent=True) or {}
    email = payload.get("email") or ""
    watchlist = payload.get("watchlist") or []
    brief = payload.get("brief") or "weekly"
    try:
        result = add_signup(get_db(), email, watchlist=watchlist, brief=brief)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(result)


@app.post("/api/waitlist")
def api_waitlist():
    """Back-compat: treat an email-only waitlist signup as Pro early access."""
    payload = request.get_json(silent=True) or {}
    try:
        result = add_signup(
            get_db(),
            payload.get("email") or "",
            watchlist=payload.get("watchlist") or [],
            brief=payload.get("brief") or "weekly",
        )
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
