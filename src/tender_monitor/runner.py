"""Main orchestration: run all scrapers, save to DB, export reports, send email."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import async_playwright

from tender_monitor.config import Settings
from tender_monitor.database import TenderDatabase
from tender_monitor.dedupe import remove_duplicates
from tender_monitor.emailer import send_report
from tender_monitor.exporters import export_excel, export_html
from tender_monitor.models import ScrapeResult
from tender_monitor.scrapers import SCRAPER_CLASSES

logger = logging.getLogger(__name__)


async def run_monitor_async(settings: Settings) -> tuple[Path, Path, int]:
    settings.report_dir.mkdir(parents=True, exist_ok=True)

    db = TenderDatabase(settings.db_path)
    db.initialize()
    run_id = db.start_run()

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=settings.headless)
            try:
                scrapers = [cls(settings.keywords, settings.timeout_ms) for cls in SCRAPER_CLASSES]
                results: list[ScrapeResult] = list(
                    await asyncio.gather(*[s.scrape(browser) for s in scrapers])
                )
            finally:
                await browser.close()
    except Exception as exc:
        db.finish_run(run_id, "failed", 0, str(exc))
        raise

    # De-duplicate across portals
    all_tenders = remove_duplicates([t for r in results for t in r.tenders])

    logger.info("Total after dedup: %s", len(all_tenders))

    db.save_results(run_id, [ScrapeResult(source="deduplicated", tenders=all_tenders)])

    errors = "; ".join(f"{r.source}: {r.error}" for r in results if r.error) or None
    db.finish_run(run_id, "completed_with_errors" if errors else "completed", len(all_tenders), errors)

    # Report contains ONLY today's findings (not the whole historical DB)
    rows = db.list_tenders_for_run(run_id)

    excel_path = export_excel(rows, settings.report_dir / "tenders.xlsx")
    html_path = export_html(rows, settings.report_dir / "tenders.html")

    send_report(
        settings.email,
        subject=f"Monitoring zakázek – {len(rows)} nových výsledků",
        body=f"Monitoring dokončen. Počet zakázek v dnešním reportu: {len(rows)}.\n\nChyby scraperů: {errors or 'žádné'}",
        attachments=[excel_path, html_path],
    )

    logger.info("Report: excel=%s html=%s rows=%s", excel_path, html_path, len(rows))
    return excel_path, html_path, len(rows)


def run_monitor(settings: Settings) -> tuple[Path, Path, int]:
    return asyncio.run(run_monitor_async(settings))
