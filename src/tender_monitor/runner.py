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

    database = TenderDatabase(settings.db_path)
    database.initialize()

    run_id = database.start_run()
    results: list[ScrapeResult] = []

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=settings.headless
            )

            try:
                scrapers = [
                    scraper_class(
                        settings.keywords,
                        settings.timeout_ms,
                    )
                    for scraper_class in SCRAPER_CLASSES
                ]

                results = await asyncio.gather(
                    *(scraper.scrape(browser) for scraper in scrapers)
                )

                print("\n=== SCRAPER RESULTS ===")

                for result in results:
                    logger.warning(
                        "SCRAPER=%s TENDERS=%s ERROR=%s",
                        result.source,
                        len(result.tenders),
                        result.error,
                    )

            finally:
                await browser.close()

        total_before_dedupe = sum(
            len(result.tenders)
            for result in results
        )

        all_tenders = remove_duplicates(
            [
                tender
                for result in results
                for tender in result.tenders
            ]
        )

        print("\n=== SUMMARY ===")
        print(f"TOTAL_BEFORE_DEDUPE={total_before_dedupe}")
        print(f"TOTAL_AFTER_DEDUPE={len(all_tenders)}")

        deduped_results = [
            ScrapeResult(
                source="deduplicated",
                tenders=all_tenders,
            )
        ]

        database.save_results(
            run_id,
            deduped_results,
        )

        scraper_errors = "; ".join(
            f"{result.source}: {result.error}"
            for result in results
            if result.error
        ) or None

        database.finish_run(
            run_id=run_id,
            status=(
                "completed_with_errors"
                if scraper_errors
                else "completed"
            ),
            total_found=len(all_tenders),
            error=scraper_errors,
        )

    except Exception as exc:
        database.finish_run(
            run_id=run_id,
            status="failed",
            total_found=0,
            error=str(exc),
        )
        raise

    rows = database.list_tenders()

    excel_path = export_excel(
        rows,
        settings.report_dir / "tenders.xlsx",
    )

    html_path = export_html(
        rows,
        settings.report_dir / "tenders.html",
    )

    send_report(
        settings.email,
        subject="Monitoring veřejných zakázek",
        body=(
            f"Monitoring dokončen. "
            f"Počet zakázek v reportu: {len(rows)}."
        ),
        attachments=[
            excel_path,
            html_path,
        ],
    )

    print("\n=== REPORT ===")
    print(f"ROWS_IN_DATABASE={len(rows)}")
    print(f"EXCEL={excel_path}")
    print(f"HTML={html_path}")

    return excel_path, html_path, len(rows)


def run_monitor(settings: Settings) -> tuple[Path, Path, int]:
    return asyncio.run(run_monitor_async(settings))
