"""Live, keyless evidence: official status pages and Google News RSS.

All fetches are optional, timed, cached, and fail soft. Nothing here invents
sources — if a request fails, that source is simply omitted.
"""

from __future__ import annotations

import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

ET_TZ = ZoneInfo("America/New_York")
SSL_CTX = ssl.create_default_context()
USER_AGENT = "IsThisRealApp/1.0 (+https://isthisrealapp.com)"
CACHE_TTL = 240  # seconds
FETCH_TIMEOUT = 2.2
NEWS_HOST = "https://news.google.com/rss/search"

# Recency window that matters most for "is it happening now?"
RECENT_HOURS = 72

TRUSTED = {
    # Tier 1 — wire services, public broadcasters, fact-checkers, security press
    "ap": 1.0, "ap news": 1.0, "associated press": 1.0, "ap fact check": 1.0,
    "reuters": 1.0, "reuters fact check": 1.0, "bbc": 1.0, "bbc news": 1.0,
    "npr": 1.0, "pbs": 1.0, "pbs newshour": 1.0, "bleepingcomputer": 1.0,
    "snopes": 1.0, "politifact": 1.0, "factcheck.org": 1.0, "factcheck": 1.0,
    # Tier 2 — major newspapers and reputable tech/business outlets
    "cnbc": 0.85, "wsj": 0.85, "the wall street journal": 0.85,
    "nytimes": 0.85, "the new york times": 0.85, "new york times": 0.85,
    "washington post": 0.85, "the washington post": 0.85, "wapo": 0.85,
    "the verge": 0.8, "techcrunch": 0.8, "ars technica": 0.8, "wired": 0.8,
    "bloomberg": 0.85, "cnn": 0.7, "cbs news": 0.75, "abc news": 0.75,
    "nbc news": 0.75, "the guardian": 0.8, "axios": 0.8, "usa today": 0.7,
    "the atlantic": 0.8, "time": 0.7, "npr.org": 1.0, "forbes": 0.6,
    "the times": 0.7, "financial times": 0.85, "ft": 0.85,
    "fox business": 0.6, "fox news": 0.5,
}

FACT_CHECKERS = {
    "snopes", "politifact", "factcheck.org", "factcheck",
    "ap fact check", "reuters fact check", "pbs", "pbs newshour",
}

OUTAGE_RE = re.compile(
    r"\b(outage|down|disrupted?|disruption|offline|unavailable|not working|"
    r"service interruption|ground stop|degraded|slow or unavailable|"
    r"all systems (operational|normal)|restored|back online)\b",
    re.I,
)
ATTACK_RE = re.compile(
    r"\b(hacked|hack|breach(?:ed)?|cyber[-\s]?attack|ransomware|malware|"
    r"skimmer|skimming|compromised|data stolen|supply[-\s]chain attack)\b",
    re.I,
)
HOAX_RE = re.compile(
    r"\b(hoax|debunk(?:ed|ing)?|fact[-\s]?check(?:ed|ing)?|false claim|"
    r"not true|isn't happening|no evidence|rumor|misleading|debunking)\b",
    re.I,
)
RUMOR_RE = re.compile(
    r"\b("
    r"is (it|this|that) (real|true|happening|fake)|"
    r"is it true|"
    r"did .{0,80}\b(die|died|pass away|get (killed|arrested|shot))|"
    r"\b(died|dead|passed away)\b|"
    r"shut(?:ting)? down (tomorrow|today|tonight|soon|next)|"
    r"internet shut|"
    r"end of the (world|internet)|"
    r"blackout|"
    r"\brumor\b"
    r")",
    re.I,
)
RESOLVED_RE = re.compile(r"\b(resolved|restored|resumed|back online|is over|has ended)\b", re.I)
ETA_RE = re.compile(
    r"\b(?:ETA|E\.T\.A\.|estimated (?:resolution|restore|recovery|fix)(?: time)?)\s*[:\-]?\s*([^\n.]{3,80})",
    re.I,
)

# Company registry: aliases plus optional official status endpoint.
# Only endpoints verified from this environment are included.
STATUS_PAGES: dict[str, dict] = {
    "github": {
        "name": "GitHub",
        "aliases": ["github"],
        "kind": "statuspage",
        "status_url": "https://www.githubstatus.com/api/v2/status.json",
        "incidents_url": "https://www.githubstatus.com/api/v2/incidents/unresolved.json",
        "public_url": "https://www.githubstatus.com",
    },
    "discord": {
        "name": "Discord",
        "aliases": ["discord"],
        "kind": "statuspage",
        "status_url": "https://discordstatus.com/api/v2/status.json",
        "incidents_url": "https://discordstatus.com/api/v2/incidents/unresolved.json",
        "public_url": "https://discordstatus.com",
    },
    "reddit": {
        "name": "Reddit",
        "aliases": ["reddit"],
        "kind": "statuspage",
        "status_url": "https://www.redditstatus.com/api/v2/status.json",
        "incidents_url": "https://www.redditstatus.com/api/v2/incidents/unresolved.json",
        "public_url": "https://www.redditstatus.com",
    },
    "cloudflare": {
        "name": "Cloudflare",
        "aliases": ["cloudflare"],
        "kind": "statuspage",
        "status_url": "https://www.cloudflarestatus.com/api/v2/status.json",
        "incidents_url": "https://www.cloudflarestatus.com/api/v2/incidents/unresolved.json",
        "public_url": "https://www.cloudflarestatus.com",
    },
    "openai": {
        "name": "OpenAI",
        "aliases": ["openai", "chatgpt", "chat gpt"],
        "kind": "statuspage",
        "status_url": "https://status.openai.com/api/v2/status.json",
        "incidents_url": "https://status.openai.com/api/v2/incidents/unresolved.json",
        "public_url": "https://status.openai.com",
    },
    "zoom": {
        "name": "Zoom",
        "aliases": ["zoom"],
        "kind": "statuspage",
        "status_url": "https://status.zoom.us/api/v2/status.json",
        "incidents_url": "https://status.zoom.us/api/v2/incidents/unresolved.json",
        "public_url": "https://www.zoomstatus.com",
    },
    "atlassian": {
        "name": "Atlassian",
        "aliases": ["atlassian", "jira", "confluence", "bitbucket", "trello"],
        "kind": "statuspage",
        "status_url": "https://status.atlassian.com/api/v2/status.json",
        "incidents_url": "https://status.atlassian.com/api/v2/incidents/unresolved.json",
        "public_url": "https://status.atlassian.com",
    },
    "dropbox": {
        "name": "Dropbox",
        "aliases": ["dropbox"],
        "kind": "statuspage",
        "status_url": "https://status.dropbox.com/api/v2/status.json",
        "incidents_url": "https://status.dropbox.com/api/v2/incidents/unresolved.json",
        "public_url": "https://status.dropbox.com",
    },
    "twilio": {
        "name": "Twilio",
        "aliases": ["twilio"],
        "kind": "statuspage",
        "status_url": "https://status.twilio.com/api/v2/status.json",
        "incidents_url": "https://status.twilio.com/api/v2/incidents/unresolved.json",
        "public_url": "https://status.twilio.com",
    },
    "coinbase": {
        "name": "Coinbase",
        "aliases": ["coinbase"],
        "kind": "statuspage",
        "status_url": "https://status.coinbase.com/api/v2/status.json",
        "incidents_url": "https://status.coinbase.com/api/v2/incidents/unresolved.json",
        "public_url": "https://status.coinbase.com",
    },
    "notion": {
        "name": "Notion",
        "aliases": ["notion"],
        "kind": "statuspage",
        "status_url": "https://www.notion-status.com/api/v2/status.json",
        "incidents_url": "https://www.notion-status.com/api/v2/incidents/unresolved.json",
        "public_url": "https://www.notion-status.com",
    },
    "shopify": {
        "name": "Shopify",
        "aliases": ["shopify"],
        "kind": "statuspage",
        "status_url": "https://www.shopifystatus.com/api/v2/status.json",
        "incidents_url": "https://www.shopifystatus.com/api/v2/incidents/unresolved.json",
        "public_url": "https://www.shopifystatus.com",
    },
    "vercel": {
        "name": "Vercel",
        "aliases": ["vercel"],
        "kind": "statuspage",
        "status_url": "https://www.vercel-status.com/api/v2/status.json",
        "incidents_url": "https://www.vercel-status.com/api/v2/incidents/unresolved.json",
        "public_url": "https://www.vercel-status.com",
    },
    "netlify": {
        "name": "Netlify",
        "aliases": ["netlify"],
        "kind": "statuspage",
        "status_url": "https://www.netlifystatus.com/api/v2/status.json",
        "incidents_url": "https://www.netlifystatus.com/api/v2/incidents/unresolved.json",
        "public_url": "https://www.netlifystatus.com",
    },
    "digitalocean": {
        "name": "DigitalOcean",
        "aliases": ["digitalocean", "digital ocean"],
        "kind": "statuspage",
        "status_url": "https://status.digitalocean.com/api/v2/status.json",
        "incidents_url": "https://status.digitalocean.com/api/v2/incidents/unresolved.json",
        "public_url": "https://status.digitalocean.com",
    },
    "slack": {
        "name": "Slack",
        "aliases": ["slack"],
        "kind": "slack",
        "status_url": "https://status.slack.com/api/v2.0.0/current",
        "public_url": "https://status.slack.com",
    },
    "heroku": {
        "name": "Heroku",
        "aliases": ["heroku"],
        "kind": "heroku",
        "status_url": "https://status.heroku.com/api/v4/current-status",
        "public_url": "https://status.heroku.com",
    },
    "apple": {
        "name": "Apple",
        "aliases": ["apple", "icloud", "app store", "apple pay", "imessage", "facetime"],
        "kind": "apple",
        "status_url": "https://www.apple.com/support/systemstatus/data/system_status_en_US.js",
        "public_url": "https://www.apple.com/support/systemstatus/",
    },
    "aws": {
        "name": "AWS",
        "aliases": ["aws", "amazon web services", "amazon aws"],
        "kind": "aws_rss",
        "status_url": "https://status.aws.amazon.com/rss/all.rss",
        "public_url": "https://health.aws.amazon.com/health/status",
    },
    "google_cloud": {
        "name": "Google Cloud",
        "aliases": ["google cloud", "gcp", "gcloud"],
        "kind": "gcp",
        "status_url": "https://status.cloud.google.com/incidents.json",
        "public_url": "https://status.cloud.google.com",
    },
    "azure": {
        "name": "Azure",
        "aliases": ["azure", "microsoft azure"],
        "kind": "azure_rss",
        "status_url": "https://azure.status.microsoft/en-us/status/feed/",
        "public_url": "https://azure.status.microsoft/en-us/status/",
    },
}

# Companies we can name in a query even without a status page.
ALIAS_ONLY: dict[str, dict] = {
    "verizon": {"name": "Verizon", "aliases": ["verizon"]},
    "chase": {"name": "Chase", "aliases": ["chase", "jpmorgan", "jp morgan", "jpmorgan chase"]},
    "ikea": {"name": "IKEA", "aliases": ["ikea"]},
    "delta": {"name": "Delta", "aliases": ["delta", "delta airlines", "delta air lines"]},
    "usps": {"name": "USPS", "aliases": ["usps", "u.s. postal", "us postal", "united states postal"]},
    "faa": {"name": "FAA", "aliases": ["faa", "federal aviation"]},
    "x": {"name": "X", "aliases": ["twitter", "x.com", "x/twitter"]},
    "microsoft": {"name": "Microsoft", "aliases": ["microsoft", "microsoft 365", "office 365", "m365", "outlook", "teams"]},
    "google": {"name": "Google", "aliases": ["google", "gmail", "youtube"]},
    "meta": {"name": "Meta", "aliases": ["facebook", "instagram", "whatsapp", "meta"]},
    "netflix": {"name": "Netflix", "aliases": ["netflix"]},
    "amazon": {"name": "Amazon", "aliases": ["amazon"]},
    "cnn": {"name": "CNN", "aliases": ["cnn"]},
    "hsbc": {"name": "HSBC", "aliases": ["hsbc"]},
    "optimizely": {"name": "Optimizely", "aliases": ["optimizely"]},
}

# Precomputed alias lookup, longest first so "microsoft 365" wins over "microsoft".
def _alias_index() -> list[tuple[str, str, dict]]:
    rows = []
    for key, meta in {**{k: {**v} for k, v in STATUS_PAGES.items()}, **ALIAS_ONLY}.items():
        for alias in meta.get("aliases") or [meta["name"].lower()]:
            rows.append((alias.lower(), key, meta))
    rows.sort(key=lambda r: len(r[0]), reverse=True)
    return rows


ALIAS_INDEX = _alias_index()


@dataclass
class NewsItem:
    title: str
    outlet: str
    url: str
    published: datetime | None
    trust: float
    hours_ago: float | None

    @property
    def recency(self) -> float:
        if self.hours_ago is None:
            return 0.15
        if self.hours_ago <= 12:
            return 1.0
        if self.hours_ago <= 24:
            return 0.85
        if self.hours_ago <= 48:
            return 0.6
        if self.hours_ago <= RECENT_HOURS:
            return 0.4
        if self.hours_ago <= 24 * 7:
            return 0.15
        return 0.0

    @property
    def weight(self) -> float:
        return self.trust * self.recency

    def as_source(self) -> dict:
        return {
            "name": self.outlet,
            "url": self.url,
            "time_ago": format_ago(self.published) if self.published else "",
        }


@dataclass
class StatusResult:
    key: str
    name: str
    public_url: str
    operational: bool
    summary: str
    incidents: list[str] = field(default_factory=list)
    resolved_today: list[str] = field(default_factory=list)
    updated: datetime | None = None
    level: str = ""  # ok, degraded, down, unknown
    eta: str | None = None

    def __post_init__(self) -> None:
        if not self.level:
            self.level = "ok" if self.operational else "down"

    def as_source(self) -> dict:
        label = f"{self.name} Status"
        ago = format_ago(self.updated) if self.updated else ""
        return {"name": label, "url": self.public_url, "time_ago": ago}

    def as_board_item(self, checked_at: datetime | None = None) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "level": self.level,
            "summary": self.summary,
            "eta": self.eta,
            "status_url": self.public_url,
            "updated": format_ago(self.updated) if self.updated else "",
            "checked_at": format_ago(checked_at or self.updated) if (checked_at or self.updated) else "",
            "query": f"Is {self.name} down?",
        }


@dataclass
class ParsedQuery:
    text: str
    entity_key: str | None = None
    entity_name: str | None = None
    entity_aliases: list[str] = field(default_factory=list)
    has_status: bool = False
    topic: str = "general"  # outage, attack, hoax, rumor, general
    news_query: str = ""


@dataclass
class LiveEvidence:
    parsed: ParsedQuery
    status: StatusResult | None = None
    news: list[NewsItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def cache_get(key: str):
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        expires, value = hit
        if expires < time.time():
            _cache.pop(key, None)
            return None
        return value


def cache_set(key: str, value, ttl: int = CACHE_TTL) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)


def format_ago(dt: datetime | None, now: datetime | None = None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(ET_TZ)
    dt_local = dt.astimezone(ET_TZ)
    seconds = (now - dt_local).total_seconds()
    if seconds < 0:
        seconds = 0
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        n = int(seconds // 60)
        return f"{n} minute{'s' if n != 1 else ''} ago"
    if seconds < 86400:
        n = int(seconds // 3600)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    if seconds < 86400 * 2:
        return "yesterday"
    if seconds < 86400 * 7:
        n = int(seconds // 86400)
        return f"{n} days ago"
    return dt_local.strftime("%b %-d")


def _fetch(url: str, timeout: float = FETCH_TIMEOUT) -> bytes | None:
    cached = cache_get("url:" + url)
    if cached is not None:
        return cached
    remaining = max(0.4, timeout)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/rss+xml, application/xml, text/xml, */*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=remaining, context=SSL_CTX) as resp:
            data = resp.read(400_000)
        cache_set("url:" + url, data)
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None


def parse_query(text: str) -> ParsedQuery:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    lowered = raw.lower()
    topic = "general"
    if ATTACK_RE.search(lowered):
        topic = "attack"
    elif RUMOR_RE.search(lowered) or HOAX_RE.search(lowered):
        topic = "rumor"
    elif OUTAGE_RE.search(lowered) or re.search(r"\b(cancel(?:led|ed)?|ground stop|flights?)\b", lowered):
        topic = "outage"

    # Special-case X/Twitter so a bare "x" doesn't match randomly.
    entity_key = entity_name = None
    aliases: list[str] = []
    has_status = False
    if re.search(r"\b(twitter|x\.com|x/twitter)\b", lowered) or re.search(r"\bis x (down|hacked|out)\b", lowered):
        entity_key, entity_name, aliases = "x", "X", ["twitter", "x.com", "x (twitter)"]
    else:
        haystack = " " + re.sub(r"[^a-z0-9.+]+", " ", lowered) + " "
        for alias, key, meta in ALIAS_INDEX:
            token = " " + alias + " "
            if token in haystack or lowered.startswith(alias + " ") or lowered.endswith(" " + alias):
                entity_key = key
                entity_name = meta["name"]
                aliases = list(meta.get("aliases") or [alias])
                has_status = key in STATUS_PAGES
                break

    if not entity_key and topic not in {"rumor", "hoax"}:
        m = re.search(
            r"^\s*(?:is|was|are|were|did)\s+(?:there\s+(?:a\s+|an\s+)?|the\s+)?(.+?)\s+"
            r"(?:down|hacked|breached|out|real|true|canceled|cancelled|having|experiencing)\b",
            raw,
            re.I,
        )
        if m:
            guess = re.sub(r"\b(a|an|the|nationwide|major|big)\b", " ", m.group(1), flags=re.I)
            guess = re.sub(r"\s+", " ", guess).strip(" ?!.")
            if 1 < len(guess) <= 40:
                words = guess.split()
                if len(words) > 3:
                    guess = " ".join(words[:3])
                if guess.islower():
                    guess = guess.title()
                entity_name = guess
                aliases = [guess.lower()]

    news_query = _build_news_query(raw, entity_name, aliases, topic)
    return ParsedQuery(
        text=raw,
        entity_key=entity_key,
        entity_name=entity_name,
        entity_aliases=aliases,
        has_status=has_status,
        topic=topic,
        news_query=news_query,
    )


def _build_news_query(raw: str, entity_name: str | None, aliases: list[str], topic: str) -> str:
    name = entity_name or ""
    if entity_name and topic == "attack":
        return f'"{name}" (hacked OR hack OR breach OR cyberattack OR ransomware)'
    if entity_name and topic == "outage":
        return f'"{name}" (outage OR down OR disruption OR offline)'
    if entity_name and topic in {"hoax", "rumor"}:
        return f'"{name}" (hoax OR rumor OR debunked OR "fact check" OR false OR died OR death)'
    if entity_name:
        # Generic question about a company — look for either outages or attacks.
        return f'"{name}" (outage OR down OR hacked OR breach OR cyberattack)'
    # Fall back to a cleaned version of the user's words, plus fact-check terms for claims.
    cleaned = re.sub(r"[^\w\s-]+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if topic in {"hoax", "rumor"}:
        return f"{cleaned} (hoax OR debunked OR \"fact check\" OR false OR rumor)"
    return cleaned[:120]


def _trust_for(outlet: str) -> float:
    key = re.sub(r"\s+", " ", (outlet or "").strip().lower())
    if key in TRUSTED:
        return TRUSTED[key]
    for name, score in TRUSTED.items():
        if re.search(r"(^|[\s.])" + re.escape(name) + r"($|[\s.])", key):
            return score
    return 0.35


def _parse_pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def fetch_google_news(query: str, timeout: float = FETCH_TIMEOUT) -> list[NewsItem]:
    if not query.strip():
        return []
    params = urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    url = f"{NEWS_HOST}?{params}"
    raw = _fetch(url, timeout=timeout)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    now = datetime.now(timezone.utc)
    items: list[NewsItem] = []
    seen_titles = set()
    for node in root.findall(".//item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        # Titles often end with " - Outlet"; keep full title for display/filter, split outlet if needed.
        link = (node.findtext("link") or "").strip()
        source_el = node.find("{*}source")
        outlet = (source_el.text or "").strip() if source_el is not None else ""
        if not outlet and " - " in title:
            title, outlet = title.rsplit(" - ", 1)
        outlet = outlet.strip(" |•-")
        if outlet.lower() in {"x", "twitter", "facebook", "instagram", "tiktok", "youtube"}:
            continue
        published = _parse_pubdate(node.findtext("pubDate"))
        hours = None
        if published:
            hours = (now - published.astimezone(timezone.utc)).total_seconds() / 3600
        key = re.sub(r"\W+", " ", title.lower()).strip()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        items.append(
            NewsItem(
                title=title,
                outlet=outlet or "Google News",
                url=link or url,
                published=published,
                trust=_trust_for(outlet),
                hours_ago=hours,
            )
        )
    return items


def _eta_from_statuspage_incidents(incidents: list[dict]) -> str | None:
    """Return an official timing phrase only when the status page states one."""
    for inc in incidents:
        until = inc.get("scheduled_until") or inc.get("expected_resolution")
        dt = _parse_iso(until) if until else None
        if dt:
            return "Until " + dt.astimezone(ET_TZ).strftime("%-I:%M %p Eastern, %b %-d")
        for upd in inc.get("incident_updates") or []:
            body = upd.get("body") or ""
            match = ETA_RE.search(body)
            if match:
                phrase = match.group(0).strip().rstrip(".")
                if 3 < len(phrase) <= 140:
                    return phrase
    return None


def _level_from_indicator(indicator: str, incident_impacts: list[str]) -> str:
    indicator = (indicator or "none").lower()
    impacts = [i.lower() for i in incident_impacts if i and i.lower() not in {"none", "none_isolated"}]
    # The official rollup indicator is the status page's own summary.
    if indicator in {"none", "none_isolated", ""}:
        return "ok"
    if indicator in {"major", "critical"} or any(i in {"major", "critical"} for i in impacts):
        return "down"
    if indicator in {"minor", "maintenance"} or any(i in {"minor", "maintenance"} for i in impacts):
        return "degraded"
    return "degraded"


def _parse_statuspage(meta: dict, status_body: bytes | None, inc_body: bytes | None) -> StatusResult:
    operational = True
    summary = f"{meta['name']}'s official status page is reachable."
    incidents: list[str] = []
    impacts: list[str] = []
    updated = None
    eta = None
    indicator = "none"
    if status_body:
        data = json.loads(status_body)
        status = data.get("status") or {}
        indicator = (status.get("indicator") or "none").lower()
        summary = status.get("description") or summary
        operational = indicator in {"none", "none_isolated", ""}
        updated = _parse_iso((data.get("page") or {}).get("updated_at"))
    raw_incidents: list[dict] = []
    if inc_body:
        data = json.loads(inc_body)
        raw_incidents = [inc for inc in (data.get("incidents") or []) if isinstance(inc, dict)]
        for inc in raw_incidents:
            name = inc.get("name") or inc.get("impact") or "Incident"
            incidents.append(name)
            if inc.get("impact"):
                impacts.append(str(inc.get("impact")))
        # Isolated incidents with impact "none" do not override the official
        # rollup. The top-level indicator is the status page's own summary.
        if incidents and indicator not in {"none", "none_isolated", ""}:
            operational = False
        eta = _eta_from_statuspage_incidents(raw_incidents)
    level = _level_from_indicator(indicator, impacts)
    return StatusResult(
        key="",
        name=meta["name"],
        public_url=meta["public_url"],
        operational=operational,
        summary=summary,
        incidents=incidents,
        updated=updated,
        level=level,
        eta=eta,
    )


def _parse_slack(meta: dict, body: bytes) -> StatusResult:
    data = json.loads(body)
    status = (data.get("status") or "").lower()
    active = data.get("active_incidents") or []
    operational = status in {"ok", "healthy", "normal"} and not active
    titles = [i.get("title") or i.get("name") or "Incident" for i in active if isinstance(i, dict)]
    summary = "All systems operational" if operational else (titles[0] if titles else "Slack is reporting an issue")
    return StatusResult(
        key="slack",
        name="Slack",
        public_url=meta["public_url"],
        operational=operational,
        summary=summary,
        incidents=titles,
        updated=_parse_iso(data.get("date_updated") or data.get("date_created")),
    )


def _parse_heroku(meta: dict, body: bytes) -> StatusResult:
    data = json.loads(body)
    systems = data.get("status") or []
    bad = [s.get("system") for s in systems if (s.get("status") or "green") != "green"]
    red = [s.get("system") for s in systems if (s.get("status") or "green") == "red"]
    incidents = [i.get("title") for i in (data.get("incidents") or []) if i.get("title")]
    operational = not bad and not incidents
    if red:
        level = "down"
    elif bad or incidents:
        level = "degraded"
    else:
        level = "ok"
    summary = "All systems operational" if operational else (
        "Issues reported on " + ", ".join(bad or incidents[:3])
    )
    return StatusResult(
        key="heroku",
        name="Heroku",
        public_url=meta["public_url"],
        operational=operational,
        summary=summary,
        incidents=incidents or bad,
        level=level,
    )


def _parse_apple(meta: dict, body: bytes) -> StatusResult:
    data = json.loads(body)
    ongoing, resolved_today = [], []
    now = datetime.now(timezone.utc)
    updated = None
    for service in data.get("services") or []:
        sname = service.get("serviceName") or "Apple service"
        for ev in service.get("events") or []:
            status = (ev.get("eventStatus") or "").lower()
            typ = ev.get("statusType") or "Issue"
            msg = ev.get("message") or typ
            start = ev.get("epochStartDate")
            if isinstance(start, (int, float)) and start > 10_000_000_000:
                start_dt = datetime.fromtimestamp(start / 1000, tz=timezone.utc)
            else:
                start_dt = None
            line = f"{sname}: {msg}"
            if status in {"ongoing", "active", "in progress", "inprogress"} or (
                status != "resolved" and not ev.get("endDate") and ev.get("eventStatus")
            ):
                # "resolved" with no endDate still counts as resolved.
                if status != "resolved":
                    ongoing.append(line)
            elif status == "resolved" and start_dt and (now - start_dt).total_seconds() <= 36 * 3600:
                resolved_today.append(line)
            if start_dt and (updated is None or start_dt > updated):
                updated = start_dt
    operational = not ongoing
    if len(ongoing) >= 2:
        level = "down"
    elif ongoing:
        level = "degraded"
    else:
        level = "ok"
    if ongoing:
        names = []
        for line in ongoing:
            names.append(line.split(":")[0].strip())
        # unique, stable order
        seen = []
        for n in names:
            if n not in seen:
                seen.append(n)
        if len(seen) == 1:
            summary = f"Apple is reporting a current issue with {seen[0]}."
        elif len(seen) <= 3:
            summary = "Apple is reporting current issues with " + ", ".join(seen[:-1]) + f", and {seen[-1]}."
        else:
            summary = "Apple is reporting current issues with " + ", ".join(seen[:3]) + ", and other services."
    elif resolved_today:
        summary = "Apple listed a service issue in the last day that is now marked resolved."
    else:
        summary = "Apple's system status page shows no current issues."
    return StatusResult(
        key="apple",
        name="Apple",
        public_url=meta["public_url"],
        operational=operational,
        summary=summary,
        incidents=ongoing,
        resolved_today=resolved_today,
        updated=updated or now,
        level=level,
    )


def _rss_items_recent(body: bytes, hours: int = RECENT_HOURS) -> tuple[list[str], datetime | None]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return [], None
    now = datetime.now(timezone.utc)
    titles, latest = [], None
    for node in root.findall(".//item"):
        title = (node.findtext("title") or "").strip()
        pub = _parse_pubdate(node.findtext("pubDate"))
        if pub and (now - pub.astimezone(timezone.utc)).total_seconds() > hours * 3600:
            continue
        if title:
            titles.append(title)
        if pub and (latest is None or pub > latest):
            latest = pub
    return titles, latest


def _parse_aws(meta: dict, body: bytes) -> StatusResult:
    titles, latest = _rss_items_recent(body)
    # AWS often leaves old "service disruption" posts up; only treat recent ones as current.
    current = [t for t in titles if not RESOLVED_RE.search(t)]
    operational = not current
    summary = (
        "Recent AWS status posts: " + "; ".join(current[:2])
        if current
        else "No recent incidents on the AWS status feed."
    )
    return StatusResult(
        key="aws",
        name="AWS",
        public_url=meta["public_url"],
        operational=operational,
        summary=summary,
        incidents=current,
        updated=latest,
    )


def _parse_azure(meta: dict, body: bytes) -> StatusResult:
    titles, latest = _rss_items_recent(body)
    operational = not titles
    summary = (
        "Azure is posting current status items: " + "; ".join(titles[:2])
        if titles
        else "The Azure status feed has no current incidents."
    )
    return StatusResult(
        key="azure",
        name="Azure",
        public_url=meta["public_url"],
        operational=operational,
        summary=summary,
        incidents=titles,
        updated=latest,
    )


def _parse_gcp(meta: dict, body: bytes) -> StatusResult:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return StatusResult("google_cloud", "Google Cloud", meta["public_url"], True, "Could not read the Google Cloud status feed.")
    now = datetime.now(timezone.utc)
    open_ones, recent = [], []
    latest = None
    if isinstance(data, list):
        for inc in data:
            begin = _parse_iso(inc.get("begin") or inc.get("created"))
            end = _parse_iso(inc.get("end"))
            desc = inc.get("external_desc") or inc.get("service_name") or "Incident"
            if not end:
                open_ones.append(desc)
            elif begin and (now - begin).total_seconds() <= 36 * 3600:
                recent.append(desc)
            if begin and (latest is None or begin > latest):
                latest = begin
    operational = not open_ones
    if open_ones:
        summary = "Google Cloud has an open incident: " + open_ones[0]
    elif recent:
        summary = "Google Cloud listed a recent incident that is now marked ended."
    else:
        summary = "No open incidents on Google Cloud's status page."
    return StatusResult(
        key="google_cloud",
        name="Google Cloud",
        public_url=meta["public_url"],
        operational=operational,
        summary=summary,
        incidents=open_ones,
        resolved_today=recent,
        updated=latest,
    )


def fetch_status(entity_key: str, timeout: float = FETCH_TIMEOUT) -> StatusResult | None:
    meta = STATUS_PAGES.get(entity_key)
    if not meta:
        return None
    kind = meta["kind"]
    try:
        if kind == "statuspage":
            status_body = _fetch(meta["status_url"], timeout)
            inc_body = _fetch(meta["incidents_url"], timeout) if meta.get("incidents_url") else None
            if not status_body and not inc_body:
                return None
            result = _parse_statuspage(meta, status_body, inc_body)
            result.key = entity_key
            return result
        body = _fetch(meta["status_url"], timeout)
        if not body:
            return None
        parsers = {
            "slack": _parse_slack,
            "heroku": _parse_heroku,
            "apple": _parse_apple,
            "aws_rss": _parse_aws,
            "azure_rss": _parse_azure,
            "gcp": _parse_gcp,
        }
        parser = parsers.get(kind)
        if not parser:
            return None
        result = parser(meta, body)
        result.key = entity_key
        return result
    except (json.JSONDecodeError, ET.ParseError, KeyError, TypeError, ValueError):
        return None



BOARD_CACHE_KEY = "status-board:v1"
BOARD_CACHE_TTL = 120


def gather_status_board(keys: list[str] | None = None, budget: float = 6.0) -> dict:
    """Fetch official status for the board. Cached, fail-soft, never invented."""
    from catalog import BOARD_KEYS, display_name, status_page_url

    keys = list(keys or BOARD_KEYS)
    cached = cache_get(BOARD_CACHE_KEY)
    if cached:
        return refresh_board_labels(cached)

    start = time.monotonic()
    now = datetime.now(ET_TZ)

    def remaining() -> float:
        return max(0.4, budget - (time.monotonic() - start))

    results: dict[str, StatusResult | None] = {key: None for key in keys}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_status, key, remaining()): key for key in keys}
        for fut in as_completed(futures, timeout=max(0.5, remaining() + 0.3)):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception:
                results[key] = None

    items = []
    for key in keys:
        result = results.get(key)
        if result:
            items.append(result.as_board_item(checked_at=now))
        else:
            items.append(
                {
                    "key": key,
                    "name": display_name(key),
                    "level": "unknown",
                    "summary": "Couldn't reach the official status page just now.",
                    "eta": None,
                    "status_url": status_page_url(key),
                    "updated": "",
                    "checked_at": format_ago(now),
                    "query": f"Is {display_name(key)} down?",
                }
            )
    payload = {
        "items": items,
        "checked_at": now.isoformat(),
        "checked_at_label": "just now",
        "checked_at_display": now.strftime("%-I:%M %p Eastern"),
    }
    cache_set(BOARD_CACHE_KEY, payload, ttl=BOARD_CACHE_TTL)
    return payload


def refresh_board_labels(payload: dict) -> dict:
    payload = dict(payload)
    checked = _parse_iso(payload.get("checked_at"))
    payload["checked_at_label"] = format_ago(checked) if checked else "just now"
    if checked:
        payload["checked_at_display"] = checked.astimezone(ET_TZ).strftime("%-I:%M %p Eastern")
    return payload


def _item_mentions(item: NewsItem, aliases: list[str], entity_name: str | None) -> bool:

    if not aliases and not entity_name:
        return True
    blob = f"{item.title} {item.outlet}".lower()
    names = [a.lower() for a in aliases]
    if entity_name:
        names.append(entity_name.lower())
    # Avoid treating posts *from* X about other outages as "X is down".
    for name in names:
        if name in {"x", "twitter", "x.com"}:
            if re.search(r"\b(twitter|x\.com)\b.*\b(down|outage)\b", blob) or re.search(
                r"\b(is x down|x outage|twitter down|twitter outage)\b", blob
            ):
                return True
            continue
        if name in blob:
            return True
    return False


def filter_news(items: list[NewsItem], parsed: ParsedQuery) -> list[NewsItem]:
    out = []
    for item in items:
        if item.hours_ago is not None and item.hours_ago > 24 * 14:
            continue
        if parsed.entity_name and not _item_mentions(item, parsed.entity_aliases, parsed.entity_name):
            continue
        out.append(item)
    out.sort(key=lambda i: (i.weight, -(i.hours_ago or 999)), reverse=True)
    return out


def gather_evidence(text: str, budget: float = 5.5) -> LiveEvidence:
    parsed = parse_query(text)
    ev = LiveEvidence(parsed=parsed)
    start = time.monotonic()

    def remaining() -> float:
        return max(0.4, budget - (time.monotonic() - start))

    jobs = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        if parsed.entity_key and parsed.entity_key in STATUS_PAGES:
            jobs[pool.submit(fetch_status, parsed.entity_key, remaining())] = "status"
        jobs[pool.submit(fetch_google_news, parsed.news_query, remaining())] = "news"
        if parsed.topic in {"hoax", "rumor"}:
            fq = parsed.news_query + ' (site:snopes.com OR site:politifact.com OR site:factcheck.org OR site:pbs.org OR "fact check")'
            jobs[pool.submit(fetch_google_news, fq, remaining())] = "factcheck"
        for fut in as_completed(jobs, timeout=max(0.5, remaining() + 0.2)):
            kind = jobs[fut]
            try:
                result = fut.result()
            except Exception as exc:  # noqa: BLE001 — fail soft
                ev.errors.append(f"{kind}: {type(exc).__name__}")
                continue
            if kind == "status":
                ev.status = result
            else:
                ev.news.extend(result or [])
    # Dedup news by title
    seen = set()
    uniq = []
    for item in ev.news:
        key = re.sub(r"\W+", " ", item.title.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    ev.news = filter_news(uniq, parsed)
    return ev


def score_topic(items: list[NewsItem], pattern: re.Pattern) -> tuple[float, list[NewsItem]]:
    hits = [i for i in items if pattern.search(i.title) and i.recency > 0]
    # Recent fact-checkers get a small boost.
    total = 0.0
    for i in hits:
        bonus = 0.25 if i.outlet.strip().lower() in FACT_CHECKERS else 0.0
        total += i.weight + bonus
    hits.sort(key=lambda i: i.weight, reverse=True)
    return total, hits


def pick_sources(ev: LiveEvidence, *groups: list[NewsItem], include_status: bool = False, limit: int = 3) -> list[dict]:
    out: list[dict] = []
    seen = set()
    if include_status and ev.status:
        src = ev.status.as_source()
        out.append(src)
        seen.add(src["url"])
    for group in groups:
        for item in group:
            if item.url in seen or item.trust < 0.55:
                continue
            out.append(item.as_source())
            seen.add(item.url)
            if len(out) >= limit:
                return out
    # Fill from leftover recent trusted news if needed.
    if len(out) < limit:
        for item in ev.news:
            if item.url in seen or item.weight < 0.2 or item.trust < 0.55:
                continue
            out.append(item.as_source())
            seen.add(item.url)
            if len(out) >= limit:
                break
    return out


def interpret(ev: LiveEvidence | None, text: str | None = None) -> dict:
    """Turn live evidence into a decisive verdict. Never invents facts."""
    if ev is None:
        ev = LiveEvidence(parsed=parse_query(text or ""))
    status = ev.status
    name = ev.parsed.entity_name or "this"
    attack_score, attack_hits = score_topic(ev.news, ATTACK_RE)
    outage_score, outage_hits = score_topic(ev.news, OUTAGE_RE)
    hoax_score, hoax_hits = score_topic(ev.news, HOAX_RE)

    strong = []
    if hoax_score >= 1.1 and hoax_hits:
        strong.append(("hoax", hoax_score))
    if ev.parsed.topic != "outage" and attack_score >= 1.5 and attack_hits:
        strong.append(("attack", attack_score))
    if outage_score >= 1.6 and outage_hits:
        strong.append(("outage", outage_score))
    strong.sort(key=lambda x: x[1], reverse=True)
    mixed = len(strong) >= 2 and (strong[0][1] - strong[1][1]) < 1.0
    mixed_line = "Sources are mixed."

    # Official current incident is the strongest outage signal.
    if status and not status.operational:
        reason = status.summary
        if ev.parsed.entity_name and not reason.lower().startswith(name.lower()):
            reason = f"{name} is reporting a current issue: {status.summary}"
        # If the question was about a hack but official status is just an outage
        # and news doesn't support an attack, stay with Real outage.
        if ev.parsed.topic == "attack" and attack_score >= 1.4:
            return {
                "verdict": "real_attack",
                "reason": f"Recent reporting describes a cyberattack involving {name}.",
                "what_to_do": "Use official apps and sites only, and watch for follow-up from the company.",
                "sources": pick_sources(ev, attack_hits, include_status=True),
                "confidence": mixed_line if mixed else "Confirmed in trusted reporting.",
            }
        return {
            "verdict": "real_outage",
            "reason": _one_line(reason),
            "what_to_do": f"Check {status.name}'s official status page if something still looks off.",
            "sources": pick_sources(ev, outage_hits, include_status=True),
            "confidence": mixed_line if mixed else "Official status confirms this.",
        }

    if hoax_score >= 1.1 and hoax_hits:
        lead = hoax_hits[0]
        reason = f"{lead.outlet} and other sources say this claim doesn't hold up."
        if re.search(r"\b(hoax|false|debunked|isn't happening|not true)\b", lead.title, re.I):
            reason = lead.title.split(" - ")[0].rstrip(".") + "."
        return {
            "verdict": "hoax",
            "reason": _one_line(reason),
            "what_to_do": "Don't share the claim. Official alerts come from government agencies, not viral posts.",
            "sources": pick_sources(ev, hoax_hits, include_status=False),
            "confidence": mixed_line if mixed else "Debunked by trusted sources.",
        }

    if ev.parsed.topic != "outage" and attack_score >= 1.5 and attack_hits:
        lead = attack_hits[0]
        reason = f"Recent reporting describes a cyberattack involving {name}."
        if ev.parsed.entity_name and ev.parsed.entity_name.lower() in lead.title.lower():
            reason = lead.title.split(" - ")[0].rstrip(".") + "."
        return {
            "verdict": "real_attack",
            "reason": _one_line(reason),
            "what_to_do": "If you entered a card or password on an affected site, watch your accounts.",
            "sources": pick_sources(ev, attack_hits, include_status=bool(status)),
            "confidence": mixed_line if mixed else "Confirmed in trusted reporting.",
        }

    if outage_score >= 1.6 and outage_hits:
        lead = outage_hits[0]
        resolved = any(RESOLVED_RE.search(i.title) for i in outage_hits[:3])
        if status and status.operational and resolved:
            reason = (
                f"{name} had a recently reported outage; its official status page now says things are normal."
            )
            return {
                "verdict": "real_outage",
                "reason": _one_line(reason),
                "what_to_do": f"Use {name}'s official app or site. If something still fails, check the official status page.",
                "sources": pick_sources(ev, outage_hits, include_status=True),
                "confidence": "Based on official or trusted reports.",
            }
        if status and status.operational and not resolved:
            # Official page is firsthand for current status; news disagrees.
            if outage_score >= 3.0 and len(outage_hits) >= 2:
                reason = f"Recent trusted reports describe an outage or disruption involving {name}."
                return {
                    "verdict": "real_outage",
                    "reason": _one_line(reason),
                    "what_to_do": f"Use {name}'s official app or website, not a link from a text. Also check the official status page.",
                    "sources": pick_sources(ev, outage_hits, include_status=True),
                    "confidence": mixed_line,
                }
            reason = f"{name} isn't down. Its official status page says everything is working."
            return {
                "verdict": "working_fine",
                "reason": _one_line(reason),
                "what_to_do": f"If it still looks broken for you, check {name}'s official status page.",
                "sources": pick_sources(ev, outage_hits, include_status=True),
                "confidence": mixed_line,
            }
        if status and status.resolved_today and status.operational:
            reason = status.summary
            return {
                "verdict": "real_outage",
                "reason": _one_line(reason),
                "what_to_do": f"If something still looks off, check {name}'s official status page.",
                "sources": pick_sources(ev, outage_hits, include_status=True),
                "confidence": "Official status confirms this.",
            }
        if resolved:
            reason = f"Recent reports describe an outage at {name} that appears to have been resolved."
        else:
            reason = f"Recent trusted reports describe an outage or disruption involving {name}."
        return {
            "verdict": "real_outage",
            "reason": _one_line(reason),
            "what_to_do": f"Use {name}'s official app or website, not a link from a text.",
            "sources": pick_sources(ev, outage_hits, include_status=bool(status)),
            "confidence": mixed_line if mixed else "Based on official or trusted reports.",
        }

    # Official page is fine, no strong news.
    if status and status.operational:
        if status.resolved_today:
            reason = status.summary
            return {
                "verdict": "real_outage",
                "reason": _one_line(reason),
                "what_to_do": f"If something still looks off, check {name}'s official status page.",
                "sources": pick_sources(ev, outage_hits, include_status=True),
                "confidence": "Official status confirms this.",
            }
        reason = f"{name} isn't down. Its official status page says everything is working."
        return {
            "verdict": "working_fine",
            "reason": _one_line(reason),
            "what_to_do": f"If it still looks broken for you, check {name}'s official status page.",
            "sources": pick_sources(ev, include_status=True),
            "confidence": "Official status confirms this.",
        }

    return _no_evidence(ev)


def _display_name(ev: LiveEvidence) -> str:
    if ev.parsed.entity_key == "x":
        return "X"
    return ev.parsed.entity_name or "this"


def _no_evidence(ev: LiveEvidence) -> dict:
    """Decisive fallback when official status and trusted news don't confirm the claim."""
    name = _display_name(ev)
    topic = ev.parsed.topic
    # Don't attach leftover unrelated headlines as if they support the call.
    sources: list[dict] = []

    if topic == "attack":
        if ev.parsed.entity_name:
            reason = f"No trusted reports that {name} was hacked."
        else:
            reason = "No trusted reports of a hack."
        return {
            "verdict": "no_hack_found",
            "reason": _one_line(reason),
            "what_to_do": "If a message claimed there was a hack, don't click any links in it. Check the company's official website.",
            "sources": sources,
            "confidence": "No trusted reports found.",
        }

    if topic == "outage":
        if ev.parsed.entity_name:
            reason = (
                f"Nothing suggests {name} is down right now. "
                "No trusted news or reports of a widespread outage."
            )
        else:
            reason = "Nothing suggests a widespread outage right now. No trusted news or reports of one."
        return {
            "verdict": "no_outage_found",
            "reason": _one_line(reason),
            "what_to_do": "If it isn't working for you, it's probably local. Try restarting your device.",
            "sources": sources,
            "confidence": "No official status or trusted reports of an outage.",
        }

    if topic in {"hoax", "rumor"} or _looks_like_claim(ev.parsed.text):
        reason = "No trusted outlet is reporting this. Treat it as a rumor until it's confirmed."
        return {
            "verdict": "probably_not_real",
            "reason": _one_line(reason),
            "what_to_do": "Don't share it until a trusted outlet confirms it.",
            "sources": sources,
            "confidence": "No credible coverage.",
        }

    reason = "No scam red flags found. If unsure, contact them using a number you already trust."
    return {
        "verdict": "looks_legit",
        "reason": _one_line(reason),
        "what_to_do": "If unsure, contact them using a number you already trust.",
        "sources": sources,
        "confidence": "No scam signals found.",
    }


def _looks_like_claim(text: str) -> bool:
    t = (text or "").strip()
    if t.endswith("?"):
        return True
    return bool(re.match(
        r"^\s*(is|was|are|were|did|does|do|has|have|had|why|what|who|can|could|should)\b",
        t,
        re.I,
    ))



def _one_line(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    # Keep a single sentence when possible, two short ones at most.
    parts = re.split(r"(?<=[.!?])\s+", text)
    if len(parts) > 2:
        text = " ".join(parts[:2])
    if len(text) > 280:
        text = text[:277].rsplit(" ", 1)[0] + "…"
    if text and text[-1] not in ".!?":
        text += "."
    return text


def enrich_scam_with_live(reason: str, ev: LiveEvidence) -> tuple[str, list[dict]]:
    """Keep a Scam verdict, but mention the real company's official status when we have it."""
    sources = []
    if ev.status:
        sources.append(ev.status.as_source())
        name = ev.status.name
        if ev.status.operational:
            extra = f" {name}'s official status page is not reporting a related outage."
        else:
            extra = f" {name}'s official status page is reporting a separate issue, but this message is still a scam."
        if extra.strip() not in reason:
            reason = _one_line(reason + extra)
    return reason, sources
