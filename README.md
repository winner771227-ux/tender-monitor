# Tender Monitor

Python projekt pro monitoring veřejných zakázek na českých portálech se zaměřením na demoliční práce.

## Funkce

- Python 3.12
- Playwright scrapery pro všechny požadované portály
- SQLite úložiště s historií běhů a výsledků
- odstranění duplicit podle normalizovaného klíče zakázky
- filtrování podle hledaných slov:
  - `demolice`
  - `bourání`
  - `odstranění stavby`
  - `odstranění staveb`
  - `demoliční práce`
  - `odstranění objektu`
  - `likvidace stavby`
- Excel export (`.xlsx`)
- HTML report
- odeslání reportu e-mailem přes SMTP
- Dockerfile
- GitHub Actions workflow

## Monitorované portály

| Název | URL | Scraper |
| --- | --- | --- |
| JOSEPHINE | <https://josephine.proebiz.com/cs/public-tenders/all> | `JosephineScraper` |
| NEN | <https://nen.nipez.cz/verejne-zakazky> | `NenScraper` |
| eVeZa | <https://www.eveza.cz> | `EvezaScraper` |
| Tender Market | <https://tendermarket.cz> | `TenderMarketScraper` |
| Tender Arena | <https://tenderarena.cz> | `TenderArenaScraper` |
| E-ZAKAZKY | <https://www.e-zakazky.cz> | `EZakazkyScraper` |
| Profily PROEBIZ | <https://profily.proebiz.com/verejne-zakazky> | `ProfilyProebizScraper` |
| Profil zadavatele VZ | <https://www.profilzadavatele-vz.cz> | `ProfilZadavateleVzScraper` |

## Lokální instalace

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

## Spuštění

```bash
python -m tender_monitor run
```

Výchozí výstupy:

- SQLite databáze: `data/tenders.sqlite3`
- Excel report: `reports/tenders.xlsx`
- HTML report: `reports/tenders.html`

## Konfigurace

Konfigurace probíhá přes proměnné prostředí:

| Proměnná | Výchozí hodnota | Popis |
| --- | --- | --- |
| `TENDER_DB_PATH` | `data/tenders.sqlite3` | cesta k SQLite databázi |
| `TENDER_REPORT_DIR` | `reports` | adresář pro reporty |
| `TENDER_HEADLESS` | `true` | headless režim Playwrightu |
| `TENDER_TIMEOUT_MS` | `30000` | timeout načítání stránek |
| `SMTP_HOST` | prázdné | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USERNAME` | prázdné | SMTP uživatel |
| `SMTP_PASSWORD` | prázdné | SMTP heslo |
| `SMTP_FROM` | prázdné | odesílatel |
| `SMTP_TO` | prázdné | příjemci oddělení čárkou |
| `SMTP_USE_TLS` | `true` | zapnutí STARTTLS |

Pokud není nastaven `SMTP_HOST` nebo příjemci, e-mailové odeslání se přeskočí.

## Docker

```bash
docker build -t tender-monitor .
docker run --rm -v "$(pwd)/data:/app/data" -v "$(pwd)/reports:/app/reports" tender-monitor
```

## GitHub Actions

Workflow `.github/workflows/monitor.yml` lze spustit ručně přes `workflow_dispatch` a automaticky se spouští každý den v 8:00 časového pásma `Europe/Prague`. Protože GitHub Actions plánování používá UTC, workflow má dvě UTC plánované hodnoty a kontrolní krok pustí běh pouze tehdy, když je v Praze skutečně 8:00.

Po dokončení běhu se report odešle e-mailem přímo z aplikace pomocí SMTP konfigurace a zároveň se Excel, HTML a SQLite soubory uloží jako GitHub Actions artifact.

Pro SMTP odeslání v GitHub Actions nastavte repository secrets:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_TO`
- `SMTP_USE_TLS` (volitelné, výchozí hodnota je `true`)

## Scrapery

Každý scraper dědí z `BaseScraper` a implementuje metodu `scrape_page`. Scrapery používají Playwright selektory pro tabulkové a kartové seznamy veřejných zakázek, extrahují název, zadavatele, datum zveřejnění, lhůtu a URL detailu a následně aplikují filtr demoličních klíčových slov. Pokud portál neposkytuje datum zveřejnění nebo lhůtu, příslušná hodnota zůstane prázdná (`None`).

## Struktura projektu

```text
.
├── .github/workflows/monitor.yml
├── AGENTS.md
├── Dockerfile
├── README.md
├── requirements.txt
├── data/.gitkeep
├── reports/.gitkeep
├── src/tender_monitor/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── database.py
│   ├── dedupe.py
│   ├── emailer.py
│   ├── exporters.py
│   ├── models.py
│   ├── runner.py
│   └── scrapers/
│       ├── __init__.py
│       ├── base.py
│       ├── e_zakazky.py
│       ├── eveza.py
│       ├── josephine.py
│       ├── nen.py
│       ├── profil_zadavatele_vz.py
│       ├── profily_proebiz.py
│       ├── tender_arena.py
│       └── tender_market.py
└── tests/
    └── test_dedupe.py
```
