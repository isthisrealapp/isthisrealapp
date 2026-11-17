"""Rule-based checker plus a curated knowledge base.

An optional LLM may polish wording when an API key is present.
It must never invent sources or override a high-confidence verdict
from the rules or knowledge base.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from live_sources import enrich_scam_with_live, gather_evidence, interpret

ROOT = Path(__file__).resolve().parent
KB_PATH = ROOT / "knowledge_base.json"

VERDICT_META = {
    "scam": {
        "label": "Scam",
        "display": "Scam",
        "tone": "danger",
        "short": "Don't click or pay.",
    },
    "likely_scam": {
        "label": "Likely scam",
        "display": "Likely scam",
        "tone": "danger",
        "short": "Treat it as unsafe.",
    },
    "hoax": {
        "label": "Hoax",
        "display": "Hoax",
        "tone": "hoax",
        "short": "This isn't happening.",
    },
    "real_outage": {
        "label": "Real outage, not a hack",
        "display": "Real",
        "sublabel": "Outage, not a hack",
        "tone": "real",
        "short": "A real outage — not a confirmed hack.",
    },
    "real_attack": {
        "label": "Real attack",
        "display": "Real",
        "sublabel": "Confirmed attack",
        "tone": "real_attack",
        "short": "This was a confirmed attack.",
    },
    "working_fine": {
        "label": "Working fine",
        "display": "Working fine",
        "tone": "ok",
        "short": "Official status says everything is working.",
    },
    "unclear": {
        "label": "Unclear",
        "display": "Unclear",
        "tone": "unclear",
        "short": "We can't confirm this.",
    },
}

BRAND_DISPLAY = {
    "usps": "USPS",
    "ups": "UPS",
    "faa": "FAA",
    "irs": "IRS",
    "ssa": "SSA",
    "fcc": "FCC",
    "fema": "FEMA",
    "tsa": "TSA",
    "at&t": "AT&T",
    "hsbc": "HSBC",
    "cnn": "CNN",
    "ikea": "IKEA",
    "jpmorgan": "JPMorgan",
    "jp morgan": "JPMorgan",
}

OFFICIAL_DOMAINS: dict[str, list[str]] = {
    "delta": ["delta.com"],
    "american airlines": ["aa.com", "americanairlines.com"],
    "american": ["aa.com", "americanairlines.com"],
    "united": ["united.com"],
    "southwest": ["southwest.com"],
    "jetblue": ["jetblue.com"],
    "alaska": ["alaskaair.com"],
    "spirit": ["spirit.com"],
    "frontier": ["flyfrontier.com"],
    "chase": ["chase.com", "jpmorganchase.com", "jpmorgan.com"],
    "jpmorgan": ["jpmorganchase.com", "jpmorgan.com", "chase.com"],
    "jp morgan": ["jpmorganchase.com", "jpmorgan.com", "chase.com"],
    "bank of america": ["bankofamerica.com", "bofa.com"],
    "wells fargo": ["wellsfargo.com"],
    "citibank": ["citi.com", "citibank.com"],
    "capital one": ["capitalone.com"],
    "apple": ["apple.com", "icloud.com"],
    "microsoft": [
        "microsoft.com",
        "office.com",
        "live.com",
        "microsoftonline.com",
        "outlook.com",
        "windows.com",
    ],
    "ikea": ["ikea.com"],
    "zoom": ["zoom.us", "zoom.com"],
    "cnn": ["cnn.com"],
    "hsbc": ["hsbc.com"],
    "usps": ["usps.com"],
    "ups": ["ups.com"],
    "fedex": ["fedex.com"],
    "amazon": ["amazon.com", "amzn.com"],
    "paypal": ["paypal.com"],
    "verizon": ["verizon.com", "verizonwireless.com"],
    "at&t": ["att.com"],
    "t-mobile": ["t-mobile.com", "tmobile.com"],
    "irs": ["irs.gov"],
    "ssa": ["ssa.gov"],
    "fema": ["fema.gov", "ready.gov"],
    "fcc": ["fcc.gov"],
    "faa": ["faa.gov"],
    "tsa": ["tsa.gov"],
}

SHORTENERS = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "ow.ly",
    "is.gd",
    "cutt.ly",
    "rb.gy",
    "tiny.cc",
    "shorturl.at",
    "goo.gl",
    "rebrand.ly",
    "lnkd.in",
    "buff.ly",
    "s.id",
    "v.gd",
}

# Common "this is a question about an event" lead-ins, not a message purporting
# to be from a company.
QUESTION_LEAD = re.compile(
    r"^\s*(is|was|are|were|did|does|do|has|have|had|why|what|who|can|could|should)\b",
    re.I,
)

URL_RE = re.compile(
    r"(https?://[^\s<>\")']+|"
    r"(?:www\.)?(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}"
    r"(?:/[^\s<>\")']*)?)",
    re.I,
)


GIFT_CARD_RE = re.compile(
    r"\b(gift\s*cards?|itunes\s*card|apple\s*card\s*code|google\s*play\s*card|"
    r"steam\s*card|visa\s*gift|target\s*card|amazon\s*gift)\b",
    re.I,
)
CRYPTO_RE = re.compile(
    r"\b(bitcoin|btc\b|ethereum|ether\b|usdt|usdc|crypto\s*wallet|wallet\s*address|"
    r"send\s+(btc|eth|crypto)|coinbase|binance)\b",
    re.I,
)
SECRETS_RE = re.compile(
    r"\b(verification code|one[-\s]?time code|\botp\b|password|passcode|"
    r"social security|\bssn\b|pin number|routing number|"
    r"confirm your (password|pin|ssn)|send (me )?the code|"
    r"give us your (password|pin|ssn|code)|reply with (your )?code)\b",
    re.I,
)
URGENCY_RE = re.compile(
    r"\b(act now|immediately|within \d+\s*(hours?|minutes?|hrs?)|"
    r"account will be (closed|suspended|locked|frozen)|"
    r"will be (closed|suspended|locked|frozen)|final notice|"
    r"expires? (today|tonight|in \d+)|urgent|right now|"
    r"limited time|your account has been (locked|suspended|compromised)|"
    r"unusual (activity|sign[-\s]?in)|verify (your )?account (now|immediately)?)\b",
    re.I,
)
PAY_REBOOK_RE = re.compile(
    r"\b(pay\s*\$?\s*\d+|pay (the )?(fee|to rebook|to (?:cancel|unlock|restore))|"
    r"rebooking fee|refund (processing )?fee|processing fee|"
    r"click (here )?to (rebook|refund|pay)|unpaid fare|"
    r"confirm (your )?payment)\b",
    re.I,
)
PACKAGE_FEE_RE = re.compile(
    r"\b(package|parcel|shipment|delivery)\b.{0,80}\b(pay|fee|customs|postage due|release|held|pending)\b"
    r"|\b(pay|fee|customs|postage due)\b.{0,80}\b(package|parcel|shipment|delivery)\b",
    re.I,
)
WIRE_RE = re.compile(
    r"\b(wire transfer|western union|moneygram|gift card to (pay|cover)|"
    r"send money (to|via)|pay with (bitcoin|crypto|gift))\b",
    re.I,
)


def load_knowledge_base() -> list[dict]:
    with KB_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return data.get("events", [])


def _normalize(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _domain_of(raw: str) -> str | None:
    candidate = raw.strip().rstrip(".,);!]>")
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        host = urlparse(candidate).hostname or ""
    except ValueError:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def extract_urls_and_domains(text: str) -> tuple[list[str], list[str]]:
    found = URL_RE.findall(text)
    urls: list[str] = []
    domains: list[str] = []
    for raw in found:
        cleaned = raw.rstrip(".,);!]>")
        urls.append(cleaned)
        d = _domain_of(cleaned)
        if d:
            domains.append(d)
    return urls, domains


def _is_official(domain: str, official: list[str]) -> bool:
    for o in official:
        if domain == o or domain.endswith("." + o):
            return True
    return False


def lookalike_hits(text: str, domains: list[str]) -> list[dict]:
    hits = []
    lowered = text.lower()
    for brand, official in OFFICIAL_DOMAINS.items():
        brand_mentioned = re.search(r"\b" + re.escape(brand) + r"\b", lowered)
        for domain in domains:
            if _is_official(domain, official):
                continue
            compact = domain.replace("-", "").replace(".", "")
            brand_compact = brand.replace(" ", "").replace("&", "")
            brand_in_domain = (
                brand.replace(" ", "") in domain.replace("-", "").replace(".", "")
                or brand.split()[0] in domain
            )
            # "delta-rebook.co" should fire when the message mentions Delta
            # or the domain itself contains the brand.
            if brand_in_domain and not _is_official(domain, official):
                if brand_mentioned or brand_compact in compact:
                    hits.append(
                        {
                            "brand": brand,
                            "domain": domain,
                            "official": official[0],
                            "code": "lookalike_domain",
                            "detail": (
                                f"{domain} is not {BRAND_DISPLAY.get(brand, brand.title())}'s official site "
                                f"({official[0]})."
                            ),
                        }
                    )
            elif brand_mentioned and domain not in official:
                # A non-official domain in a message that names a brand, when
                # the domain looks like a brand + action/suffix.
                first = brand.split()[0]
                if first in domain and first not in {"the", "bank"}:
                    hits.append(
                        {
                            "brand": brand,
                            "domain": domain,
                            "official": official[0],
                            "code": "mismatched_brand_domain",
                            "detail": (
                                f"This message mentions {BRAND_DISPLAY.get(brand, brand.title())} but links to "
                                f"{domain}, not {official[0]}."
                            ),
                        }
                    )
    # Dedup by domain
    seen = set()
    unique = []
    for h in hits:
        key = (h["domain"], h["brand"])
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique


@dataclass
class Signal:
    code: str
    label: str
    weight: int
    detail: str


def detect_signals(text: str) -> list[Signal]:
    signals: list[Signal] = []
    urls, domains = extract_urls_and_domains(text)

    if URGENCY_RE.search(text):
        signals.append(
            Signal("urgency", "Urgent or threatening language", 2, "Uses pressure or a deadline to push a quick action.")
        )
    if PAY_REBOOK_RE.search(text):
        signals.append(
            Signal(
                "pay_to_rebook",
                "Asks you to pay a fee to rebook, refund, or unlock something",
                4,
                "Companies almost never ask you to pay a small fee through a text or a surprise link.",
            )
        )
    if PACKAGE_FEE_RE.search(text):
        signals.append(
            Signal(
                "package_fee",
                "Asks you to pay a fee to release a package",
                4,
                "Carriers don't ask you to pay a delivery or customs fee through a surprise text link.",
            )
        )
    if GIFT_CARD_RE.search(text):
        signals.append(
            Signal("gift_cards", "Asks for a gift card", 5, "No legitimate airline, bank, or government agency is paid in gift cards.")
        )
    if CRYPTO_RE.search(text):
        signals.append(
            Signal("crypto", "Asks for cryptocurrency", 5, "A request to pay in crypto is a very strong scam signal.")
        )
    if WIRE_RE.search(text):
        signals.append(
            Signal("wire", "Asks for a wire or irreversible payment", 4, "Wires, money transfers, and crypto are hard to reverse — scammers prefer them.")
        )
    if SECRETS_RE.search(text):
        signals.append(
            Signal(
                "secrets",
                "Asks for a password, code, PIN, or Social Security number",
                5,
                "Don't send codes or passwords. Real companies won't ask for these by text or email.",
            )
        )

    for d in domains:
        if d in SHORTENERS:
            signals.append(
                Signal("shortened_link", "Uses a shortened link", 2, f"The shortened link ({d}) hides the real destination.")
            )
            break

    for hit in lookalike_hits(text, domains):
        signals.append(
            Signal(hit["code"], "Lookalike or mismatched domain", 5, hit["detail"])
        )

    # Bare "click here" plus a non-official or unknown link and a brand name
    if re.search(r"\b(click here|tap here|log in here|login here|verify here)\b", text, re.I) and domains:
        signals.append(
            Signal("click_here", "Pushes you to click a link", 1, "A generic 'click here' is often used to hide a bad destination.")
        )

    return signals


def match_knowledge_base(text: str, events: list[dict]) -> list[tuple[int, dict]]:
    norm = _plain(text)
    scored: list[tuple[int, dict]] = []
    for event in events:
        score = 0
        for kw in event.get("keywords", []):
            k = _plain(kw)
            if not k:
                continue
            if k in norm:
                # Longer phrases count more
                score += 3 + min(len(k.split()), 4)
            else:
                parts = k.split()
                if len(parts) >= 2 and all(p in norm.split() or p in norm for p in parts):
                    score += 2
        if score:
            score += int(event.get("priority", 0) / 5)
            scored.append((score, event))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def is_question(text: str) -> bool:
    t = text.strip()
    if t.endswith("?"):
        return True
    return bool(QUESTION_LEAD.match(t))


def _sources(event: dict | None, extra: list[dict] | None = None) -> list[dict]:
    out: list[dict] = []
    seen = set()
    for src in (event or {}).get("sources", []) + (extra or []):
        url = src.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        item = {"name": src.get("name") or url, "url": url}
        if src.get("time_ago"):
            item["time_ago"] = src["time_ago"]
        out.append(item)
    return out


def decide(text: str, events: list[dict], live=None) -> dict:
    text = _normalize(text)
    signals = detect_signals(text)
    weight = sum(s.weight for s in signals)
    matches = match_knowledge_base(text, events)
    top = matches[0][1] if matches and matches[0][0] >= 5 else None
    question = is_question(text)

    # A message that itself tries to collect money / credentials is a scam
    # even if it piggybacks on a real event (FAA cancellations, Chase outage).
    strong_codes = {s.code for s in signals}
    is_scammy_message = weight >= 6 or (
        "lookalike_domain" in strong_codes or "mismatched_brand_domain" in strong_codes
    ) and weight >= 4
    is_likely = (not is_scammy_message) and weight >= 4

    if is_scammy_message and not question:
        # Prefer the airline-scam event as supporting context when relevant.
        event = None
        for _, ev in matches:
            if ev["verdict"] == "scam":
                event = ev
                break
        if event is None and top and top["verdict"] == "scam":
            event = top
        reason = _scam_reason(signals, text, event)
        what = (
            event["what_to_do"]
            if event
            else "Don't click, pay, or share codes. Use the official app or website."
        )
        sources = _sources(event)
        if live:
            reason, live_sources = enrich_scam_with_live(reason, live)
            sources = _sources(event, live_sources)
        return _result(
            "scam",
            text,
            reason,
            what,
            sources,
            signals,
            top,
        )

    if is_likely and not question:
        reason = _scam_reason(signals, text, None, likely=True)
        what = "Don't click or pay. Confirm it in the official app or website."
        extra_sources = _sources(top) if top and top.get("verdict") == "scam" else []
        return _result("likely_scam", text, reason, what, extra_sources, signals, top)

    if top:
        verdict = top["verdict"]
        # If the user asked a question about a known hoax/outage/attack, use the KB.
        # The airline-scam KB entry is for scam *messages*; a bare question like
        # "are rebooking fees a scam?" can still use it, but "is chase hacked?"
        # should never become "scam" just because scams exist this week.
        if verdict == "scam" and question and weight < 4:
            # Fall through to the next non-scam match if there is one.
            alt = next((ev for sc, ev in matches[1:] if ev["verdict"] != "scam" and sc >= 5), None)
            if alt:
                top = alt
                verdict = alt["verdict"]
            else:
                top = None
                verdict = "unclear"

        if top:
            reason = top["summary"]
            what = top["what_to_do"]
            sources = _sources(top)
            if live:
                reason, what, sources = _blend_kb_with_live(top, live, reason, what, sources)
            return _result(
                verdict,
                text,
                reason,
                what,
                sources,
                signals,
                top,
            )

    # No KB match, no strong scam signals
    if weight >= 3:
        reason = _scam_reason(signals, text, None, likely=True)
        what = "Don't click or pay. Confirm it in the official app or website."
        return _result("likely_scam" if weight >= 4 else "unclear", text, reason, what, [], signals, None)

    if live:
        suggestion = interpret(live)
        return _result(
            suggestion["verdict"],
            text,
            suggestion["reason"],
            suggestion["what_to_do"],
            suggestion.get("sources") or [],
            signals,
            None,
        )

    reason = (
        "We can't confirm this. It doesn't match a known hoax or attack, and it "
        "doesn't show the usual scam red flags."
    )
    what = "If you were expecting it, confirm through a number or site you already use."
    return _result("unclear", text, reason, what, [], signals, None)



def _blend_kb_with_live(event, live, reason, what, sources):
    """Keep the curated verdict, but prefer a fresher official-status one-liner
    and append live citations (never inventing links)."""
    extra = []
    status_first = False
    if live and live.status:
        extra.append(live.status.as_source())
        status = live.status
        if not status.operational and status.summary:
            reason = status.summary
            status_first = True
        elif status.resolved_today and event.get("verdict") == "real_outage":
            reason = status.summary
            status_first = True
    if live and live.news:
        from live_sources import pick_sources
        extra.extend(pick_sources(live, live.news, include_status=False, limit=3))
    if extra:
        if status_first:
            sources = _sources(None, extra + (event.get("sources") or []))
        else:
            sources = _sources(event, extra)
        sources = sources[:3]
    # Keep the reason to two short sentences.
    parts = reason.split(". ")
    if len(parts) > 2:
        reason = ". ".join(parts[:2])
        if not reason.endswith("."):
            reason += "."
    return reason, what, sources


def _scam_reason(signals: list[Signal], text: str, event: dict | None = None, likely: bool = False) -> str:
    lookalike = next((s for s in signals if s.code in {"lookalike_domain", "mismatched_brand_domain"}), None)
    if lookalike:
        detail = lookalike.detail
        if detail.endswith("."):
            detail = detail[:-1]
        if likely:
            return f"This looks like a scam. {detail}."
        return f"This is a scam. {detail}."
    if any(s.code == "pay_to_rebook" for s in signals):
        return "This is a scam. Airlines don't ask you to pay a rebooking fee by text."
    if any(s.code == "package_fee" for s in signals):
        return "This is a scam. Carriers don't ask you to pay a delivery fee through a text link."
    if any(s.code in {"gift_cards", "crypto", "wire"} for s in signals):
        return "This is a scam. Real companies won't ask you to pay with gift cards or crypto."
    if any(s.code == "secrets" for s in signals):
        return "This is a scam. Don't send a password, code, or PIN from this message."
    if event and event.get("summary"):
        return event["summary"]
    if likely:
        return "This has common scam warning signs. Treat it as unsafe until you confirm it yourself."
    return "This looks like a scam, not a real message from the company it names."


def _result(
    verdict: str,
    query: str,
    reason: str,
    what_to_do: str,
    sources: list[dict],
    signals: list[Signal],
    event: dict | None,
) -> dict:
    meta = VERDICT_META[verdict]
    return {
        "verdict": verdict,
        "label": meta["label"],
        "display": meta.get("display") or meta["label"],
        "sublabel": meta.get("sublabel") or "",
        "headline": meta["short"],
        "reason": reason,
        "what_to_do": what_to_do,
        "sources": sources,
        "signals": [{"code": s.code, "label": s.label, "detail": s.detail} for s in signals],
        "event_id": event["id"] if event else None,
        "query": query,
        "used_llm": False,
    }


def maybe_polish_with_llm(result: dict) -> dict:
    """Optionally polish reason/what_to_do. Never adds sources or changes a
    high-confidence verdict from rules/KB."""
    if result["verdict"] in {"scam", "hoax", "real_outage", "real_attack"} and result.get("sources"):
        # Wording from the KB is already careful; polishing is optional.
        pass

    provider, key, model, url = _llm_config()
    if not key:
        return result

    system = (
        "You help a consumer fact-checking app. Rewrite the provided reason and "
        "what-to-do in calm, plain English (no fear-mongering, no hype). "
        "Reason must be at most two short sentences. What-to-do must be one short line. "
        "Keep every factual claim that is already in the text. "
        "Do not add new facts, statistics, dates, or sources. "
        "Do not change the verdict. "
        "Return strict JSON with keys reason and what_to_do only."
    )
    user = json.dumps(
        {
            "verdict": result["verdict"],
            "reason": result["reason"],
            "what_to_do": result["what_to_do"],
        },
        ensure_ascii=False,
    )
    try:
        polished = _call_llm(provider, key, model, url, system, user)
        if isinstance(polished, dict):
            if isinstance(polished.get("reason"), str) and polished["reason"].strip():
                result["reason"] = polished["reason"].strip()
            if isinstance(polished.get("what_to_do"), str) and polished["what_to_do"].strip():
                result["what_to_do"] = polished["what_to_do"].strip()
            result["used_llm"] = True
    except Exception:
        # Rules + KB must work alone; a failed LLM call is a no-op.
        result["used_llm"] = False
    return result


def _llm_config() -> tuple[str | None, str | None, str | None, str | None]:
    if os.environ.get("OPENAI_API_KEY"):
        return (
            "openai",
            os.environ["OPENAI_API_KEY"],
            os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions"),
        )
    if os.environ.get("XAI_API_KEY"):
        return (
            "xai",
            os.environ["XAI_API_KEY"],
            os.environ.get("XAI_MODEL", "grok-4-fast"),
            os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1/chat/completions"),
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        return (
            "anthropic",
            os.environ["ANTHROPIC_API_KEY"],
            os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            "https://api.anthropic.com/v1/messages",
        )
    return None, None, None, None


def _call_llm(provider: str, key: str, model: str, url: str, system: str, user: str) -> dict:
    if provider in {"openai", "xai"}:
        body = {
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    # Anthropic
    body = {
        "model": model,
        "max_tokens": 500,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode())
    content = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    return json.loads(content)


_EVENTS = None


def get_events() -> list[dict]:
    global _EVENTS
    if _EVENTS is None:
        _EVENTS = load_knowledge_base()
    return _EVENTS


def check(text: str, use_llm: bool = True, use_live: bool = True) -> dict:
    text = (text or "").strip()
    if not text:
        return _result(
            "unclear",
            "",
            "Paste a text, link, or question so we can check it.",
            "Add the message or rumor you want us to look at.",
            [],
            [],
            None,
        )
    if len(text) > 8000:
        text = text[:8000]
    live = None
    if use_live:
        try:
            live = gather_evidence(text, budget=5.5)
        except Exception:
            live = None
    result = decide(text, get_events(), live=live)
    if live:
        result["entity"] = live.parsed.entity_name
        result["topic"] = live.parsed.topic
    if use_llm:
        result = maybe_polish_with_llm(result)
    return result
