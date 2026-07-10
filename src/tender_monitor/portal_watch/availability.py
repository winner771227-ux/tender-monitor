"""
Šetrný test dostupnosti nefunkčních portálů (TenderArena, VVZ, ...).

Neprovádí žádný scraping — jen jeden dotaz na úvodní adresu a záznam,
jak portál odpověděl. Pokud se odpověď oproti minule ZLEPŠÍ
(portál přestal blokovat), nahlásí to jako příležitost portál
znovu zapojit. Aktivní obcházení blokací neprovádíme.
"""

from .fingerprint import fetch


# Slova, která napovídají, že stránka vyžaduje přihlášení nebo blokuje roboty
_LOGIN_MARKERS = ["přihlásit", "prihlaseni", "login", "heslo", "password"]
_BLOCK_MARKERS = ["captcha", "access denied", "forbidden", "cloudflare", "blocked"]


def check_availability(url: str, expect: str) -> dict:
    """Vrátí jednoduchý popis toho, jak portál odpověděl."""
    try:
        resp = fetch(url)
    except Exception as exc:  # timeout, DNS, spojení...
        return {"reachable": False, "error": type(exc).__name__, "ok": False}

    text_sample = resp.text[:20000].lower()
    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()

    is_json = False
    if expect == "json_api":
        try:
            resp.json()
            is_json = True
        except ValueError:
            is_json = False

    looks_blocked = (
        resp.status_code in (401, 403, 429, 503)
        or any(m in text_sample for m in _BLOCK_MARKERS)
    )
    needs_login = any(m in text_sample for m in _LOGIN_MARKERS)

    # "ok" = portál odpovídá tak, jak bychom pro scraping potřebovali
    if expect == "json_api":
        ok = resp.status_code == 200 and is_json
    else:
        ok = resp.status_code == 200 and not looks_blocked

    return {
        "reachable": True,
        "status": resp.status_code,
        "content_type": content_type,
        "is_json": is_json,
        "looks_blocked": looks_blocked,
        "needs_login": needs_login,
        "ok": ok,
    }


def availability_changed_for_better(old: dict | None, new: dict) -> bool:
    """
    True, pokud portál dřív nefungoval a teď vypadá použitelně.

    Při prvním měření (old is None) se jen uloží výchozí stav a nic
    se nehlásí — odpověď 200 totiž sama o sobě neznamená, že portál
    je pro scraping použitelný (např. VVZ vrací 200, ale obsah se
    vykresluje až JavaScriptem). Hlásíme až ZMĚNU oproti minule.
    """
    if not new.get("ok"):
        return False
    if old is None:
        return False
    return not old.get("ok")
