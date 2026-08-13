from __future__ import annotations

import importlib.util
import sys
import types
from urllib.parse import quote

if importlib.util.find_spec("playwright") is None:
    playwright_module = types.ModuleType("playwright")
    async_api_module = types.ModuleType("playwright.async_api")
    async_api_module.Browser = object
    async_api_module.Page = object
    async_api_module.Locator = object
    async_api_module.APIRequestContext = object
    sys.modules["playwright"] = playwright_module
    sys.modules["playwright.async_api"] = async_api_module

from tender_monitor.models import Tender
from tender_monitor.scrapers.josephine import _SEARCH_URL, JosephineScraper


def test_build_tender_from_josephine_table_cells() -> None:
    # Pořadí sloupců odpovídá reálné tabulce na josephine.proebiz.com:
    # ID | Číslo spisu VZ | Název zakázky | (ikona) | Zadavatel |
    # Předpokládaná hodnota | Lhůta pro podávání | (odkaz na detail)
    cells = [
        "77782",
        "SITB-OO3-2026/001743",
        "Odstranění stavby bývalé kotelny\n45110000-1",
        "",
        "Město Test\nCZ064",
        "1 000 000,00 Kč\nVZMR",
        "27.05.2026 10:00:00\nProbíhající",
        "",
    ]

    tender = JosephineScraper._build(
        cells,
        "/cs/tender/77782/summary",
        "https://josephine.proebiz.com/cs/public-tenders/all",
    )

    assert tender is not None
    assert tender.source == "JOSEPHINE"
    assert tender.title == "Odstranění stavby bývalé kotelny"
    assert tender.url == "https://josephine.proebiz.com/cs/tender/77782/summary"
    assert tender.authority == "Město Test"
    assert tender.deadline_at == "27.05.2026 10:00:00"
    assert tender.external_id == "77782"


def test_build_tender_without_href_falls_back_to_summary_url() -> None:
    cells = [
        "80404",
        "37/2026/sir",
        "Šternberk, odstranění komplexu budov v areálu zimního stadionu",
        "",
        "Město Šternberk\nCZ071",
        "8 000 000,00 Kč\nVZMR",
        "26.08.2026 10:00:00\nProbíhající",
        "",
    ]

    tender = JosephineScraper._build(
        cells, None, "https://josephine.proebiz.com/cs/public-tenders/all",
    )

    assert tender is not None
    assert tender.url == "https://josephine.proebiz.com/cs/tender/80404/summary"
    assert tender.title == "Šternberk, odstranění komplexu budov v areálu zimního stadionu"
    assert tender.authority == "Město Šternberk"
    assert tender.deadline_at == "26.08.2026 10:00:00"


def test_keyword_matches_is_accent_insensitive() -> None:
    scraper = JosephineScraper(("demoliční práce", "odstranění komplexu"), 30_000)
    tender = Tender(
        source="JOSEPHINE",
        title="DEMOLICNI PRACE objektu skladu",
        url="https://example.test/tender/1",
    )

    assert scraper._keyword_matches(tender) == ["demoliční práce"]


def test_search_url_uses_urlencoded_keyword_and_running_state_filter() -> None:
    # Toto je jádro opravy: scraper už neprochází neřazený výpis "Všechny
    # soutěže" (stovky stránek, jen zlomek se kdy prohledal), ale posílá
    # dotaz přímo přes portálový fulltext filtr pro každé klíčové slovo.
    keyword = "odstranění komplexu"
    url = _SEARCH_URL.format(keyword=quote(keyword))

    assert url.startswith("https://josephine.proebiz.com/cs/public-tenders/all?")
    assert quote(keyword) in url
    assert "filter[state]=executed" in url


def test_date_and_line_helpers() -> None:
    assert JosephineScraper._date("26.08.2026 10:00:00\nProbíhající") == "26.08.2026 10:00:00"
    assert JosephineScraper._date("bez data") is None
    assert JosephineScraper._line("  Město Test  \nCZ064") == "Město Test"
