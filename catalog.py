"""Service catalog for the status board and Pro watchlist.

Board entries are limited to official status pages already verified
in live_sources.STATUS_PAGES. The watchlist also includes a few
well-known consumer services that have no official status API;
those can only trigger alerts from the verified feed, never a
fabricated status dot.
"""

from __future__ import annotations

from live_sources import ALIAS_ONLY, STATUS_PAGES

# Consumer-first order for the live status board (20 services).
BOARD_KEYS = [
    "apple",
    "github",
    "discord",
    "slack",
    "zoom",
    "reddit",
    "openai",
    "cloudflare",
    "aws",
    "google_cloud",
    "azure",
    "dropbox",
    "notion",
    "shopify",
    "coinbase",
    "atlassian",
    "vercel",
    "netlify",
    "twilio",
    "digitalocean",
]

# Extra watchlist-only services (no official status dot).
WATCHLIST_EXTRA = [
    ("verizon", "Verizon", "everyday"),
    ("chase", "Chase", "everyday"),
    ("delta", "Delta", "everyday"),
    ("usps", "USPS", "everyday"),
    ("x", "X", "everyday"),
    ("microsoft", "Microsoft", "work"),
]

GROUP_FOR_BOARD = {
    "apple": "everyday",
    "github": "work",
    "discord": "everyday",
    "slack": "work",
    "zoom": "work",
    "reddit": "everyday",
    "openai": "everyday",
    "cloudflare": "work",
    "aws": "work",
    "google_cloud": "work",
    "azure": "work",
    "dropbox": "work",
    "notion": "work",
    "shopify": "everyday",
    "coinbase": "everyday",
    "atlassian": "work",
    "vercel": "work",
    "netlify": "work",
    "twilio": "work",
    "digitalocean": "work",
    "heroku": "work",
}

# Public marketing/home sites — used only as "contact them here" links.
OFFICIAL_HOME = {
    "apple": "https://www.apple.com",
    "github": "https://github.com",
    "discord": "https://discord.com",
    "slack": "https://slack.com",
    "zoom": "https://zoom.us",
    "reddit": "https://www.reddit.com",
    "openai": "https://openai.com",
    "cloudflare": "https://www.cloudflare.com",
    "aws": "https://aws.amazon.com",
    "google_cloud": "https://cloud.google.com",
    "azure": "https://azure.microsoft.com",
    "dropbox": "https://www.dropbox.com",
    "notion": "https://www.notion.com",
    "shopify": "https://www.shopify.com",
    "coinbase": "https://www.coinbase.com",
    "atlassian": "https://www.atlassian.com",
    "vercel": "https://vercel.com",
    "netlify": "https://www.netlify.com",
    "twilio": "https://www.twilio.com",
    "digitalocean": "https://www.digitalocean.com",
    "heroku": "https://www.heroku.com",
    "verizon": "https://www.verizon.com",
    "chase": "https://www.chase.com",
    "delta": "https://www.delta.com",
    "usps": "https://www.usps.com",
    "x": "https://x.com",
    "microsoft": "https://www.microsoft.com",
    "ikea": "https://www.ikea.com",
    "faa": "https://www.faa.gov",
    "amazon": "https://www.amazon.com",
    "google": "https://www.google.com",
    "meta": "https://www.meta.com",
    "netflix": "https://www.netflix.com",
    "cnn": "https://www.cnn.com",
    "hsbc": "https://www.hsbc.com",
    "paypal": "https://www.paypal.com",
}


def display_name(key: str) -> str:
    if key in STATUS_PAGES:
        return STATUS_PAGES[key]["name"]
    if key in ALIAS_ONLY:
        return ALIAS_ONLY[key]["name"]
    for k, name, _grp in WATCHLIST_EXTRA:
        if k == key:
            return name
    return key.replace("_", " ").title()


def status_page_url(key: str) -> str | None:
    meta = STATUS_PAGES.get(key)
    return meta["public_url"] if meta else None


def official_home(key: str | None) -> str | None:
    if not key:
        return None
    return OFFICIAL_HOME.get(key)


def official_home_label(key: str | None) -> str | None:
    url = official_home(key)
    if not url:
        return None
    return url.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")


def watchlist_catalog() -> list[dict]:
    items: list[dict] = []
    seen = set()
    for key in BOARD_KEYS + ["heroku"]:
        if key in STATUS_PAGES and key not in seen:
            items.append(
                {
                    "key": key,
                    "name": display_name(key),
                    "group": GROUP_FOR_BOARD.get(key, "work"),
                    "has_status": True,
                }
            )
            seen.add(key)
    for key, name, group in WATCHLIST_EXTRA:
        if key not in seen:
            items.append({"key": key, "name": name, "group": group, "has_status": key in STATUS_PAGES})
            seen.add(key)
    return items


VALID_WATCHLIST_KEYS = {item["key"] for item in watchlist_catalog()}


def normalize_watchlist(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except ValueError:
            raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for item in raw:
        key = str(item).strip().lower().replace(" ", "_")
        if key in {"x/twitter", "twitter", "x.com"}:
            key = "x"
        if key in VALID_WATCHLIST_KEYS and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def match_services_in_text(text: str) -> list[str]:
    """Return known service keys mentioned in a headline or query. No guessing."""
    import re

    lowered = " " + re.sub(r"[^a-z0-9.+]+", " ", (text or "").lower()) + " "
    hits = []
    # Longer aliases first so "google cloud" wins over "google".
    candidates = []
    catalog = {item["key"]: item for item in watchlist_catalog()}
    for key, meta in {**STATUS_PAGES, **ALIAS_ONLY}.items():
        names = [meta.get("name") or key]
        names.extend(meta.get("aliases") or [])
        if key in catalog:
            names.append(catalog[key]["name"])
        for name in names:
            candidates.append((name.lower(), key))
    candidates.sort(key=lambda r: len(r[0]), reverse=True)
    used = set()
    for alias, key in candidates:
        if key in used:
            continue
        token = " " + alias + " "
        if token in lowered:
            hits.append(key)
            used.add(key)
    return hits
