"""
Konfigurace hlídače změn portálů.

Adresy níže jsou převzaté PŘÍMO ze scraperů v src/tender_monitor/scrapers/
(stav k červenci 2026) — hlídač tedy kontroluje přesně ty stránky,
na kterých scrapery závisí.

Poznámka: scrapery NEN, JOSEPHINE a eVeZa používají Playwright (skutečný
prohlížeč), zatímco hlídač stahuje stránky jednoduše bez prohlížeče.
U portálů, které se vykreslují až JavaScriptem (hlavně NEN), proto hlídač
vidí jen "kostru" stránky — velké změny (redesign, přesměrování, změna
adres) ale zachytí spolehlivě i tak.

Zakázky GOV (zakazky.gov.cz) — scraper zakazky_gov.py vůbec nenačítá
stránku prohlížečem, volá přímo JSON API (api.isd.nipez.cz). Hlídač proto
posílá stejný POST dotaz jako scraper a sleduje strukturu (klíče) JSON
odpovědi, plus navíc úvodní stránku portálu jako pojistku proti úplnému
výpadku nebo redesignu.
"""

# ---------------------------------------------------------------
# FUNKČNÍ PORTÁLY — u nich hlídáme změny struktury stránek
# (seznam odpovídá aktivním (nezakomentovaným) třídám v
#  SCRAPER_CLASSES, src/tender_monitor/scrapers/__init__.py)
# ---------------------------------------------------------------
WATCHED_PORTALS = [
    {
        "name": "JOSEPHINE",
        # zdroj: scrapers/josephine.py
        "pages": [
            {"label": "výpis zakázek",
             "url": "https://josephine.proebiz.com/cs/public-tenders/all",
             "kind": "html"},
        ],
    },
    {
        "name": "ASPO",
        # zdroj: scrapers/aspo.py — XML feed + záložní NEN profil
        "pages": [
            # XML feed je "nepovinný": scraper aspo.py mu dává jen 10 sekund
            # a při nedostupnosti automaticky přepne na zálohu přes prohlížeč
            # (NEN profil níže). Nedostupnost feedu proto NENÍ chyba a hlídač
            # kvůli ní neposílá e-mail — jen si ji zapíše do logu.
            # Když se feed stáhnout podaří, jeho strukturu normálně hlídáme.
            {"label": "XML feed",
             "url": "https://nen.nipez.cz/profil/ASPO/xmldatavz?Typ=1",
             "kind": "xml",
             "optional": True},
            {"label": "NEN profil",
             "url": "https://nen.nipez.cz/profil/ASPO",
             "kind": "html"},
        ],
    },
    {
        "name": "Profily PROEBIZ",
        # zdroj: scrapers/profily_proebiz.py
        "pages": [
            {"label": "výpis zakázek",
             "url": "https://profily.proebiz.com/verejne-zakazky",
             "kind": "html"},
        ],
    },
    {
        "name": "NEN",
        # zdroj: scrapers/nen.py
        "pages": [
            {"label": "výpis zakázek",
             "url": "https://nen.nipez.cz/verejne-zakazky",
             "kind": "html"},
        ],
    },
    {
        "name": "eVeZa",
        # zdroj: scrapers/eveza.py — fulltext formulář na úvodní stránce
        # POZOR: bez "www" — scraper používá https://eveza.cz/
        "pages": [
            {"label": "úvod + fulltext formulář",
             "url": "https://eveza.cz/",
             "kind": "html"},
        ],
    },
    {
        "name": "Zakázky GOV",
        # zdroj: scrapers/zakazky_gov.py — centrální portál NIPEZ, scraper
        # volá přímo JSON API (bez prohlížeče a bez načtení webové stránky).
        "pages": [
            {"label": "vyhledávací API",
             "url": "https://api.isd.nipez.cz/isd/seznam/zakazek/hlavni-seznam",
             "kind": "json",
             "method": "post",
             # Stejný tvar dotazu jako v _search_keyword() ve scraperu,
             # jen s jedním klíčovým slovem a jedním záznamem — hlídáme
             # tvar odpovědi (klíče jako identifikator_NIPEZ,
             # nazev_verejne_zakazky, lhuta_pro_podani...), ne data.
             "json_body": {
                 "filtr": {"klicova_slova": ["demolice"], "skupinaZakazek": "VSE"},
                 "strankovani": {"stranka": 1, "pocet_zaznamu": 1},
                 "razeni": {
                     "atribut": "DATUM_UVEREJNENI_NA_ZAKAZKY_GOV",
                     "typ_razeni": "SESTUPNE",
                 },
             }},
            {"label": "úvodní stránka",
             "url": "https://zakazky.gov.cz/",
             "kind": "html"},
        ],
    },
]

# ---------------------------------------------------------------
# NEFUNKČNÍ / VYPNUTÉ PORTÁLY — jen test dostupnosti
# (adresy převzaté z jejich scraperů, které jsou v __init__.py
#  zakomentované)
# ---------------------------------------------------------------
BLOCKED_PORTALS = [
    {"name": "TenderArena",
     "url": "https://www.tenderarena.cz/dodavatel/chytre-vyhledavani",
     "expect": "html",
     "note": "Blokuje IP adresy GitHub Actions; API vrací HTML místo JSON."},
    {"name": "E-ZAKAZKY",
     "url": "https://www.e-zakazky.cz/Vyhledavani",
     "expect": "html",
     "note": "Pouze profily zadavatelů, ne centrální vyhledávání."},
    {"name": "Vhodné uveřejnění",
     "url": "https://vhodne-uverejneni.cz/katalog/zakazky",
     "expect": "html",
     "note": "Timeout na GitHub Actions."},
    {"name": "Portál Dodavatele",
     "url": "https://portaldodavatele.cz/verejne-zakazky",
     "expect": "html",
     "note": "Vyžaduje přihlášení, blokuje roboty."},
    {"name": "FEN (podo.fen.cz)",
     "url": "https://podo.fen.cz/verejne-zakazky",
     "expect": "html",
     "note": "Timeout."},
    {"name": "VVZ",
     "url": "https://vvz.nipez.cz/profil/uredni-deska/",
     "expect": "html",
     "note": "React SPA, nadlimitní zakázky — scraper deaktivován."},
    {"name": "Tender Market",
     "url": "https://tendermarket.cz/zakazky.html",
     "expect": "html",
     "note": "Načítá se prázdné."},
    {"name": "Profil zadavatele VZ",
     "url": "https://www.profilzadavatele-vz.cz/vyhledavani/",
     "expect": "html",
     "note": "Prázdná odpověď."},
]

# Kolik sekund čekat na odpověď portálu
REQUEST_TIMEOUT = 60  # NEN (včetně ASPO XML feedu) bývá pomalý

# Hlavička prohlížeče — stejný princip jako u scraperů
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Kam se ukládají referenční otisky (relativně ke kořeni repozitáře)
SNAPSHOT_DIR = "src/tender_monitor/portal_snapshots"
