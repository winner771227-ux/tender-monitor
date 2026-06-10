# AGENTS.md

## Projekt: Tender Monitor

- Jazyk: Python 3.12.
- Preferovaný styl: type hints, malé moduly, jasné názvy funkcí.
- Scraper skeletony držte v `src/tender_monitor/scrapers/`.
- Databázová logika patří do `src/tender_monitor/database.py`.
- Exporty patří do `src/tender_monitor/exporters.py`.
- Nepřidávejte tajné údaje do repozitáře; e-mailové přihlašovací údaje používejte pouze přes proměnné prostředí.
- Před commitem spusťte alespoň `python -m compileall src`.
