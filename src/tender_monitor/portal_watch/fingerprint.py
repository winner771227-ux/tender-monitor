"""
Vytváření a porovnávání "otisků" struktury stránek portálů.

Otisk NENÍ celý obsah stránky (ten se mění pořád — nové zakázky atd.),
ale jen kostra, na které závisí scrapery:
  - jaké kombinace HTML značek a CSS tříd stránka obsahuje,
  - jaká ID prvků,
  - jaké formuláře a jaká pole v nich jsou,
  - u JSON odpovědí: jaké klíče a jejich zanoření.

Když z otisku něco ZMIZÍ, je to varovný signál — scraper se možná
odkazuje na prvek, který už neexistuje. Když něco jen PŘIBUDE,
zpravidla to nevadí (weby přidávají prvky běžně).
"""

import json
import re

import requests
from bs4 import BeautifulSoup

from .config import REQUEST_TIMEOUT, USER_AGENT


def fetch(url: str):
    """
    Stáhne stránku a vrátí odpověď (nebo vyhodí výjimku).
    Při selhání (timeout apod.) to zkusí ještě jednou —
    NEN a spol. občas jen chvíli nestíhají.
    """
    import time

    last_exc = None
    for attempt in (1, 2):
        try:
            return requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "cs,en;q=0.8"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
        except Exception as exc:
            last_exc = exc
            if attempt == 1:
                time.sleep(10)  # krátká pauza a druhý pokus
    raise last_exc


def _html_fingerprint(html_text: str) -> dict:
    """Z HTML vytáhne strukturální kostru."""
    soup = BeautifulSoup(html_text, "html.parser")

    tag_class = set()
    ids = set()
    forms = set()

    for el in soup.find_all(True):
        classes = el.get("class") or []
        for c in classes:
            # Ignoruj třídy s čísly/hashovanými částmi (mění se při každém
            # nasazení webu a vyvolávaly by falešné poplachy), např. "css-1ab2cd"
            if re.search(r"\d{2,}|[a-f0-9]{6,}", c):
                continue
            tag_class.add(f"{el.name}.{c}")
        el_id = el.get("id")
        if el_id and not re.search(r"\d{3,}", el_id):
            ids.add(el_id)

    for form in soup.find_all("form"):
        action = form.get("action") or ""
        fields = sorted(
            inp.get("name") for inp in form.find_all(["input", "select", "textarea"])
            if inp.get("name")
        )
        forms.add(f"form[{action}] pole: {', '.join(fields)}")

    return {
        "tag_class": sorted(tag_class),
        "ids": sorted(ids),
        "forms": sorted(forms),
    }


def _json_key_paths(data, prefix="") -> set:
    """Rekurzivně posbírá cesty klíčů v JSON (např. 'results[].title')."""
    paths = set()
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths |= _json_key_paths(value, path)
    elif isinstance(data, list):
        for item in data[:5]:  # stačí ochutnávka, klíče se opakují
            paths |= _json_key_paths(item, prefix + "[]")
    return paths


def build_fingerprint(url: str, kind: str) -> dict:
    """Stáhne stránku a vytvoří její otisk."""
    resp = fetch(url)
    fp = {
        "url": url,
        "status": resp.status_code,
        "content_type": (resp.headers.get("Content-Type") or "").split(";")[0].strip(),
        "final_url_path": re.sub(r"https?://[^/]+", "", resp.url) or "/",
    }
    if kind == "json":
        try:
            fp["json_keys"] = sorted(_json_key_paths(resp.json()))
            fp["parses_as_json"] = True
        except ValueError:
            fp["parses_as_json"] = False
            fp["json_keys"] = []
    elif kind == "xml":
        fp.update(_xml_fingerprint(resp.text))
    else:
        fp.update(_html_fingerprint(resp.text))
    return fp


def _xml_fingerprint(xml_text: str) -> dict:
    """
    Z XML feedu (např. ASPO na NEN) vytáhne cesty značek,
    např. 'zakazky/zakazka/nazev'. Obsah (konkrétní zakázky)
    se ignoruje — sledujeme jen kostru feedu.
    """
    import xml.etree.ElementTree as ET

    paths = set()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"xml_paths": [], "parses_as_xml": False}

    def walk(el, prefix):
        tag = el.tag.split("}")[-1]  # odstraní případný namespace
        path = f"{prefix}/{tag}" if prefix else tag
        paths.add(path)
        for child in list(el)[:20]:
            walk(child, path)

    walk(root, "")
    return {"xml_paths": sorted(paths), "parses_as_xml": True}


def compare_fingerprints(old: dict, new: dict) -> dict:
    """
    Porovná starý (referenční) a nový otisk.
    Vrací slovník s popisem změn. Klíč 'serious' říká, jestli jde
    o změnu, kvůli které má smysl poslat e-mail.
    """
    problems = []   # vážné změny → e-mail
    info = []       # jen pro zajímavost do logu

    if old.get("status") != new.get("status"):
        problems.append(
            f"Stavový kód se změnil z {old.get('status')} na {new.get('status')}."
        )
    if old.get("content_type") != new.get("content_type"):
        problems.append(
            f"Typ obsahu se změnil z '{old.get('content_type')}' na '{new.get('content_type')}'."
        )
    if old.get("final_url_path") != new.get("final_url_path"):
        problems.append(
            "Stránka nově přesměrovává jinam: "
            f"'{old.get('final_url_path')}' → '{new.get('final_url_path')}'. "
            "Možná se změnilo schéma adres (URL)."
        )
    if old.get("parses_as_json") and not new.get("parses_as_json"):
        problems.append("Odpověď už není platný JSON — API se pravděpodobně změnilo.")
    if old.get("parses_as_xml") and not new.get("parses_as_xml"):
        problems.append("Odpověď už není platné XML — feed se pravděpodobně změnil.")

    for field, label in [
        ("tag_class", "HTML prvky/třídy"),
        ("ids", "ID prvků"),
        ("forms", "formuláře"),
        ("json_keys", "JSON klíče"),
        ("xml_paths", "XML značky"),
    ]:
        old_set = set(old.get(field) or [])
        new_set = set(new.get(field) or [])
        removed = sorted(old_set - new_set)
        added = sorted(new_set - old_set)
        if removed:
            shown = removed[:15]
            more = f" (a dalších {len(removed) - 15})" if len(removed) > 15 else ""
            problems.append(
                f"Zmizely {label} ({len(removed)}): {', '.join(shown)}{more}"
            )
        if added:
            info.append(f"Přibyly {label}: {len(added)}")

    return {"serious": bool(problems), "problems": problems, "info": info}
