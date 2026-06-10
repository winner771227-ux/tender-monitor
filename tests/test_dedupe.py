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
