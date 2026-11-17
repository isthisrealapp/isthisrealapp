"""Concrete 'What to do now' steps for each verdict. Never invents facts."""

from __future__ import annotations

from catalog import official_home, official_home_label, status_page_url
from live_sources import STATUS_PAGES

FTC_REPORT = "https://reportfraud.ftc.gov"


def build_steps(result: dict, live=None) -> list[dict]:
    verdict = result.get("verdict") or ""
    entity_key = result.get("entity_key") or _key_from_live(live)
    entity_name = result.get("entity") or _name_from_live(live) or _name_from_key(entity_key)
    eta = None
    status_url = status_page_url(entity_key) if entity_key else None
    if live and getattr(live, "status", None):
        status_url = status_url or live.status.public_url
        eta = getattr(live.status, "eta", None)
        entity_name = entity_name or live.status.name

    if verdict in {"scam", "likely_scam"}:
        return _scam_steps(entity_key, entity_name)
    if verdict == "real_outage":
        return _outage_steps(entity_key, entity_name, status_url, eta, resolved=False)
    if verdict == "working_fine":
        return _working_fine_steps(entity_key, entity_name, status_url)
    if verdict == "no_outage_found":
        return _no_outage_steps(entity_name)
    if verdict == "real_attack":
        return _hack_steps(entity_key, entity_name)
    if verdict == "no_hack_found":
        return _no_hack_steps(entity_key, entity_name)
    if verdict in {"hoax", "probably_not_real"}:
        return _rumor_steps()
    if verdict == "looks_legit":
        return _legit_steps()
    return _generic_steps()


def _key_from_live(live) -> str | None:
    if live and getattr(live, "parsed", None):
        return live.parsed.entity_key
    return None


def _name_from_live(live) -> str | None:
    if live and getattr(live, "parsed", None):
        return live.parsed.entity_name
    return None


def _name_from_key(key: str | None) -> str | None:
    if not key:
        return None
    if key in STATUS_PAGES:
        return STATUS_PAGES[key]["name"]
    return None


def _step(text: str, url: str | None = None, link_label: str | None = None) -> dict:
    item = {"text": text}
    if url:
        item["url"] = url
        item["link_label"] = link_label or url.replace("https://", "").replace("http://", "")
    return item


def _contact_step(entity_key: str | None, entity_name: str | None, verb: str = "Contact") -> dict | None:
    url = official_home(entity_key)
    if not url:
        return None
    label = official_home_label(entity_key)
    name = entity_name or "the company"
    return _step(f"{verb} {name} only at {label}.", url, label)


def _scam_steps(entity_key, entity_name) -> list[dict]:
    steps = [
        _step("Don't click the link, call a number in the message, or pay anything."),
        _step("Forward the text to 7726 (SPAM), the U.S. number for reporting spam."),
        _step("Report it at reportfraud.ftc.gov.", FTC_REPORT, "reportfraud.ftc.gov"),
    ]
    contact = _contact_step(entity_key, entity_name, verb="If you still need to reach them, use")
    if contact:
        # Tighten wording
        url = official_home(entity_key)
        label = official_home_label(entity_key)
        name = entity_name or "the company"
        steps.append(_step(f"Contact {name} only through its official site, not a link in the message.", url, label))
    return steps


def _outage_steps(entity_key, entity_name, status_url, eta, resolved: bool) -> list[dict]:
    name = entity_name or "the service"
    steps: list[dict] = []
    if status_url:
        steps.append(_step(f"Check {name}'s official status page for the current picture.", status_url, "Official status page"))
    if eta:
        steps.append(_step(f"Official status gives this timing: {eta}."))
    if resolved:
        steps.append(_step(f"The official page says this is resolved. If {name} still fails, try restarting the app or signing in again."))
    else:
        steps.append(
            _step(
                "If you can wait, that's safest. Try the official app or site, or switch between Wi-Fi and cellular — and ignore texts that claim to 'fix' it."
            )
        )
    if not status_url:
        home = official_home(entity_key)
        if home:
            steps.insert(0, _step(f"Use {name}'s official site or app only.", home, official_home_label(entity_key)))
    return steps[:3]


def _working_fine_steps(entity_key, entity_name, status_url) -> list[dict]:
    name = entity_name or "the service"
    steps = [
        _step(f"If {name} still looks broken for you, it's probably local. Try restarting the app or your device."),
        _step("Switch between Wi-Fi and cellular, or try another browser."),
    ]
    if status_url:
        steps.append(_step(f"You can recheck {name}'s official status page if it still fails.", status_url, "Official status page"))
    return steps[:3]


def _no_outage_steps(entity_name) -> list[dict]:
    name = entity_name or "the service"
    return [
        _step(f"Nothing trusted shows a widespread {name} outage, so it's probably on your end. Try restarting your device."),
        _step("Toggle airplane mode, or switch between Wi-Fi and cellular."),
        _step("If people around you are affected too, wait a few minutes and check again."),
    ]


def _hack_steps(entity_key, entity_name) -> list[dict]:
    name = entity_name or "the company"
    home = official_home(entity_key)
    if home:
        change = _step(
            f"Change your {name} password on the official site, and sign out of sessions you don't recognize.",
            home,
            official_home_label(entity_key),
        )
    else:
        change = _step(f"Change your {name} password on the official site or app — not a link from a message.")
    return [
        change,
        _step("Watch your bank and card statements for charges you don't recognize."),
        _step("Turn on two-factor authentication if it isn't already on."),
    ]


def _no_hack_steps(entity_key, entity_name) -> list[dict]:
    name = entity_name or "the company"
    steps = [
        _step(f"No trusted reports that {name} was hacked. Ignore messages that claim otherwise and ask you to click."),
    ]
    home = official_home(entity_key)
    if home:
        steps.append(
            _step(
                f"If you got a 'breach' message, go directly to {official_home_label(entity_key)} — don't use a link in the text.",
                home,
                official_home_label(entity_key),
            )
        )
    else:
        steps.append(_step("If a message claimed there was a breach, go directly to the company's official website."))
    steps.append(_step("You can still change your password if you want extra peace of mind."))
    return steps[:3]


def _rumor_steps() -> list[dict]:
    return [
        _step("Don't share the claim until a trusted outlet or official source confirms it."),
        _step("Official alerts come from the company or agency itself, not viral posts."),
        _step("If you already shared it, follow up with a trusted source so others aren't misled."),
    ]


def _legit_steps() -> list[dict]:
    return [
        _step("If you're unsure, contact them using a number or site you already trust — not a new link in the message."),
        _step("Don't send codes, passwords, or payments unless you started the conversation."),
        _step("When in doubt, wait and confirm later rather than acting in a hurry."),
    ]


def _generic_steps() -> list[dict]:
    return [
        _step("If you need to reach a company, use its official app or website — not a link from a message."),
        _step("Don't pay, share a code, or click through until you can confirm it yourself."),
    ]
