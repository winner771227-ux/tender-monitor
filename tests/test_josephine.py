from __future__ import annotations

import importlib.util
import sys
import types

if importlib.util.find_spec("playwright") is None:
    playwright_module = types.ModuleType("playwright")
    async_api_module = types.ModuleType("playwright.async_api")
    async_api_module.Browser = object
    async_api_module.Page = object
    async_api_module.Locator = object
    sys.modules["playwright"] = playwright_module
    sys.modules["playwright.async_api"] = async_api_module

from tender_monitor.models import Tender
from tender_monitor.scrapers.josephine import JosephineScraper


def test_build_tender_from_josephine_table_cells() -> None:
    cells = [
        "77782\nSITB-OO3-2026/001743",
        "",
        "Odstranění stavby bývalé kotelny\n45110000-1",
        "",
        "Město Test\nCZ064",
        "1 000 000,00 Kč\nVZMR",
        "27.05.2026 10:00:00\nProbíhající",
        "",
    ]

    tender = JosephineScraper._build_tender_from_cells(
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


def test_keyword_matching_is_accent_insensitive() -> None:
    scraper = JosephineScraper(("demoliční práce", "odstranění stavby"), 30_000)
    tender = Tender(
        source="JOSEPHINE",
        title="DEMOLICNI PRACE objektu skladu",
        url="https://example.test/tender/1",
    )

    assert scraper.keyword_matches(tender) == ["demoliční práce"]


def test_publication_date_uses_earliest_document_date() -> None:
    detail_text = """
Informace
Název zakázky
Demolice objektu
Dokumenty
Název dokumentu Typ Velikost Datum a čas
Výzva.pdf Dokument 1 MB 20.05.2026 09:45:36
Příloha.xlsx Dokument 1 MB 18.05.2026 14:00:00
"""

    document_section = JosephineScraper._text_after_first_heading(detail_text, ("Dokumenty", "Documents"))
    dates = [JosephineScraper._first_date(line) for line in document_section.splitlines()]
    dates = [date for date in dates if date]

    assert min(dates, key=JosephineScraper._date_sort_key) == "18.05.2026 14:00:00"
