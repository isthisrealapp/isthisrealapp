"""Generate a 1080x1920 share image for TikTok / Instagram stories."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "fonts"

W, H = 1080, 1920

# Light, calm cards. Color lives in the big verdict word only.
PALETTE = {
    "scam": "#C42B2B",
    "likely_scam": "#C42B2B",
    "hoax": "#6B3FA0",
    "real_outage": "#1F7A4D",
    "real_attack": "#C47A12",
    "working_fine": "#1B8A52",
    "unclear": "#5C5C5C",
}

BG = "#F6F4EF"
INK = "#1A1916"
MUTED = "#6B675F"

ET = ZoneInfo("America/New_York")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / name
    if not path.exists():
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    return ImageFont.truetype(str(path), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = current + " " + word
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts: list[str] = []
    rest = text
    while rest:
        cut = None
        for sep in (". ", "! ", "? "):
            i = rest.find(sep)
            if i >= 0 and (cut is None or i < cut):
                cut = i + 1
        if cut is None:
            parts.append(rest.strip())
            break
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return parts


_GENERIC_WHY = {
    "this is a scam.",
    "this looks like a scam.",
    "this is a hoax.",
    "this looks like a hoax.",
    "this was a confirmed attack.",
    "this is a confirmed attack.",
}


def _first_sentence(text: str) -> str:
    """Pick a glanceable why-line that doesn't just repeat the verdict word."""
    parts = _split_sentences(text)
    if not parts:
        return ""
    if parts[0].lower() in _GENERIC_WHY and len(parts) > 1:
        parts = parts[1:]
    # Keep a short two-sentence answer (e.g. Working fine) instead of clipping it.
    if len(parts) >= 2 and len(" ".join(parts[:2])) <= 160:
        return " ".join(parts[:2])
    return parts[0]


def render_share_card(result: dict) -> bytes:
    verdict = result.get("verdict") or "unclear"
    color = PALETTE.get(verdict, PALETTE["unclear"])
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    margin = 100
    max_w = W - margin * 2
    y = 140

    wordmark = _font("Outfit-SemiBold.ttf", 36)
    draw.text((margin, y), "IsThisRealApp", font=wordmark, fill=INK)
    y += 160

    display = (result.get("display") or result.get("label") or verdict).upper()
    # Huge verdict word, wrapping only if needed (e.g. LIKELY SCAM)
    verdict_font = _font("Newsreader-Semibold.ttf", 200 if " " not in display else 150)
    v_lines = _wrap(draw, display, verdict_font, max_w)
    # If a long label still overflows at 150, drop further.
    if any(draw.textlength(line, font=verdict_font) > max_w for line in v_lines):
        verdict_font = _font("Newsreader-Semibold.ttf", 130)
        v_lines = _wrap(draw, display, verdict_font, max_w)
    for line in v_lines[:3]:
        draw.text((margin, y), line, font=verdict_font, fill=color)
        bbox = draw.textbbox((0, 0), line, font=verdict_font)
        y += (bbox[3] - bbox[1]) + 10
    y += 28

    sublabel = result.get("sublabel") or ""
    if sublabel:
        sub_font = _font("Outfit-Medium.ttf", 36)
        draw.text((margin, y), sublabel, font=sub_font, fill=MUTED)
        y += 70

    y += 20
    draw.line((margin, y, W - margin, y), fill="#DED9CF", width=2)
    y += 70

    claim = (result.get("query") or "").strip().replace("\n", " ")
    if len(claim) > 90:
        claim = claim[:87].rstrip() + "…"
    if claim:
        claim_font = _font("Newsreader-Medium.ttf", 52)
        for line in _wrap(draw, claim, claim_font, max_w)[:3]:
            draw.text((margin, y), line, font=claim_font, fill=INK)
            y += 66
        y += 28

    why = _first_sentence(result.get("reason") or result.get("headline") or "")
    if why:
        why_font = _font("Inter-Regular.ttf", 40)
        for line in _wrap(draw, why, why_font, max_w)[:4]:
            draw.text((margin, y), line, font=why_font, fill=MUTED)
            y += 56

    footer_y = H - 160
    brand = _font("Outfit-Medium.ttf", 32)
    draw.text((margin, footer_y), "isthisrealapp.com", font=brand, fill=INK)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
