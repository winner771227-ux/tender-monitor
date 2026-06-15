"""Registry of all active scraper classes."""
from tender_monitor.scrapers.e_zakazky import EZakazkyScraper
from tender_monitor.scrapers.eveza import EvezaScraper
from tender_monitor.scrapers.josephine import JosephineScraper
from tender_monitor.scrapers.nen import NenScraper
from tender_monitor.scrapers.podo_fen import PodoFenScraper
from tender_monitor.scrapers.profil_zadavatele_vz import ProfilZadavateleVzScraper
from tender_monitor.scrapers.profily_proebiz import ProfilyProebizScraper
from tender_monitor.scrapers.tender_arena import TenderArenaScraper
from tender_monitor.scrapers.tender_market import TenderMarketScraper
from tender_monitor.scrapers.vhodne_uverejneni import VhodneUverejneniScraper
from tender_monitor.scrapers.vvz import VvzScraper

SCRAPER_CLASSES = (
    # ✅ FUNGUJE
    JosephineScraper,       # JOSEPHINE – centrální seznam, vyhledávání ✅
    ProfilyProebizScraper,  # Profily PROEBIZ – centrální seznam ✅

    # ⚠️ ČÁSTEČNĚ – přidáváme ale mohou vracet 0
    EvezaScraper,           # eVeZa – vyhledávání funguje, výsledky se ověřují
    # VhodneUverejneniScraper,  # Vhodné uveřejnění – timeout na GitHub Actions

    # ❌ DEAKTIVOVÁNO – nemají centrální vyhledávání nebo timeout
    # TenderArenaScraper,   # Tender Arena – pouze profily zadavatelů, ne centrální vyhledávání
    # EZakazkyScraper,      # E-ZAKAZKY – pouze profily zadavatelů, ne centrální vyhledávání
    # TenderMarketScraper,  # Tender Market – načte se prázdné
    # ProfilZadavateleVzScraper,  # Profil VZ – prázdná odpověď
    # PodoFenScraper,       # FEN – timeout
    # NenScraper,           # NEN – timeout
    # VvzScraper,           # VVZ – React SPA, nadlimitní zakázky
)

__all__ = [
    "SCRAPER_CLASSES",
    "EZakazkyScraper",
    "EvezaScraper",
    "JosephineScraper",
    "NenScraper",
    "PodoFenScraper",
    "ProfilZadavateleVzScraper",
    "ProfilyProebizScraper",
    "TenderArenaScraper",
    "TenderMarketScraper",
    "VhodneUverejneniScraper",
    "VvzScraper",
]
