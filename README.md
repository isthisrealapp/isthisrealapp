# IsThisRealApp

IsThisRealApp is a consumer micro-SaaS that checks a suspicious text, email, link, or rumor and returns a shareable verdict: Scam, Likely scam, Hoax, Real outage (not a hack), Real attack, Working fine, No outage found, No hack found, Probably not real, or Looks legit.

Built to be helpful, not alarming. The page has three parts: a rumor checker, a short verified “What's real right now” feed, and a $5/month Alerts plan (early access — payments are not live yet).

## Run locally

```bash
cd /workspace/isthisreal
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:8787

Optional: set `OPENAI_API_KEY`, `XAI_API_KEY`, or `ANTHROPIC_API_KEY` to polish the wording. The checker works without an LLM — rules plus the curated knowledge base are enough. The model is never allowed to invent sources.

Early-access Alerts signups and the verified feed are stored in `data/isthisreal.db` (SQLite). The feed is seeded from `feed.json`.


## Feed and alerts

List the latest verified items:

```bash
curl http://127.0.0.1:8787/api/feed
```

Add a new verified item. A source URL is required; never invent one:

```bash
python add_feed_item.py \
  --headline "One-line headline" \
  --tag outage \
  --date 2026-11-17 \
  --source-name CNBC \
  --source-url "https://example.com/official-story"
```

`--tag` must be `outage`, `attack`, or `hoax`. Use `--notify` to also stub-send an alert.

When a new item is confirmed, stub-notify early-access subscribers (logs to `data/alert_log.jsonl`; nothing is actually emailed or texted):

```bash
python send_alert.py --id apple-outage-2026-11-17
python send_alert.py --latest
```

## What’s in a check

1. Rule-based scam signals (urgency, pay-to-rebook fees, package fees, gift cards, crypto, lookalike domains, shortened links, mismatched brand domains, requests for codes or passwords).
2. A curated knowledge base of verified events, used as a high-confidence layer when a known event matches.
3. Live lookups (no API key required) when you ask about any company, outage, attack, or rumor:
   - Official status pages (Atlassian Statuspage plus Apple, Slack, AWS, Google Cloud, Azure, and Heroku)
   - Google News RSS, with recency (last ~72 hours) and trust weighting for reputable and fact-checking outlets
   Fetches run in parallel, time out so a check stays under ~6 seconds, are cached for a few minutes, and fail soft if a source is down. Sources are never invented.
4. A 1080×1920 share image for TikTok and Instagram stories, at `/api/share/<id>.png`.

## Going live

- A domain (for example, isthisrealapp.com) and TLS
- Hosting such as a small VPS, Render, or Fly (this is a Flask app; a static+serverless port to Vercel would need the API moved to a serverless function or a separate host)
- Stripe Checkout for the $5/month Alerts plan
- Transactional email and SMS (wire `send_alert.py` to a provider)
- A review process so new events are added only with official sources
- Privacy policy and terms, plus a clear “informational, not a guarantee” disclaimer
