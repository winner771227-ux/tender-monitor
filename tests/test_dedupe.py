from tender_monitor.dedupe import normalize_text, remove_duplicates
from tender_monitor.models import Tender


def test_normalize_text_removes_accents_and_extra_spaces() -> None:
    assert normalize_text("  Demoliční   práce  ") == "demolicni prace"


def test_remove_duplicates_uses_stable_fingerprint() -> None:
    tenders = [
        Tender(source="NEN", title="Demolice objektu", url="https://example.test/1"),
        Tender(source="NEN", title="Demolice objektu", url="https://example.test/1"),
    ]

    assert len(remove_duplicates(tenders)) == 1


def test_default_keywords_include_extended_demolition_terms() -> None:
    import importlib.util
    import sys
    import types

    if importlib.util.find_spec("dotenv") is None:
        dotenv_module = types.ModuleType("dotenv")
        dotenv_module.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv_module

    from tender_monitor.config import DEFAULT_KEYWORDS

    assert "odstranění objektu" in DEFAULT_KEYWORDS
    assert "likvidace stavby" in DEFAULT_KEYWORDS
