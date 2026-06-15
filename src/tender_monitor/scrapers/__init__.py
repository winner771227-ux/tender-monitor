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
from tender_monitor.scrapers.vvz import VvzScraper

SCRAPER_CLASSES = (
    JosephineScraper,       # JOSEPHINE – funguje ✅
    ProfilyProebizScraper,  # Profily PROEBIZ – funguje ✅
    PodoFenScraper,         # Portál Dodavatele FEN – agregátor všech CZ portálů ✅ (nový)
    TenderArenaScraper,     # Tender Arena – opraveno
    EZakazkyScraper,        # E-ZAKAZKY – opraveno
    TenderMarketScraper,    # Tender Market – opraveno
    ProfilZadavateleVzScraper,  # Profil zadavatele VZ – opraveno
    EvezaScraper,           # eVeZa – omezeno na 1 stránku (JS stránkování)
    # NenScraper,           # NEN – dočasně vypnuto (timeout)
    # VvzScraper,           # VVZ – nadlimitní zakázky, uživatel nezajímá
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
    "VvzScraper",
]
