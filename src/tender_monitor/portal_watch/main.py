"""
Hlavní běh hlídače portálů.

Co dělá:
1. U funkčních portálů vytvoří aktuální otisk struktury a porovná ho
   s referenčním otiskem uloženým v portal_snapshots/.
   - Pokud reference neexistuje (první běh), vytvoří ji a uloží.
   - Pokud se struktura vážně změnila, přidá to do hlášení.
     Referenci NEPŘEPISUJE — přepsala by se až po opravě scraperu,
     spuštěním s proměnnou UPDATE_SNAPSHOTS=1.
2. U nefunkčních portálů provede šetrný test dostupnosti a porovná
   ho s minulým stavem. Zlepšení nahlásí jako příležitost.
   (Stav dostupnosti se ukládá vždy — je to průběžný záznam, ne reference.)
3. Pokud je co hlásit, pošle jeden souhrnný e-mail. Jinak mlčí.

Spuštění:  python -m tender_monitor.portal_watch.main
"""

import json
import os
import re
import sys
from pathlib import Path

from .availability import availability_changed_for_better, check_availability
from .config import BLOCKED_PORTALS, SNAPSHOT_DIR, WATCHED_PORTALS
from .fingerprint import build_fingerprint, compare_fingerprints
from .report import build_email_body, send_email


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _snapshot_path(root: Path, portal: str, label: str) -> Path:
    return root / f"{_slug(portal)}__{_slug(label)}.json"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    snap_dir = repo_root / SNAPSHOT_DIR
    snap_dir.mkdir(parents=True, exist_ok=True)

    update_snapshots = os.environ.get("UPDATE_SNAPSHOTS", "").lower() in ("1", "true", "yes")

    structure_issues = []
    opportunities = []
    errors = []
    snapshots_written = False

    # Paměť neúspěšných stažení z minulého běhu — jednorázové zaškobrtnutí
    # portálu (např. pomalý NEN) se jen zaloguje; e-mailem se hlásí až
    # chyba, která se opakuje dva běhy po sobě.
    fetch_errors_path = snap_dir / "_fetch_errors.json"
    prev_fetch_errors = set()
    if fetch_errors_path.exists():
        prev_fetch_errors = set(json.loads(fetch_errors_path.read_text(encoding="utf-8")))
    new_fetch_errors = set()

    # ---------- 1) Funkční portály: kontrola struktury ----------
    for portal in WATCHED_PORTALS:
        for page in portal["pages"]:
            path = _snapshot_path(snap_dir, portal["name"], page["label"])
            page_key = f"{portal['name']}::{page['label']}"
            print(f"Kontroluji {portal['name']} — {page['label']} ({page['url']})")
            try:
                new_fp = build_fingerprint(page["url"], page["kind"])
            except Exception as exc:
                new_fetch_errors.add(page_key)
                detail = (f"Stránku '{page['label']}' se nepodařilo stáhnout "
                          f"({type(exc).__name__}: {exc}).")
                if page_key in prev_fetch_errors:
                    print(f"  ❌ {detail} (opakovaně — jde do hlášení)")
                    errors.append({
                        "portal": portal["name"],
                        "detail": detail + " Chyba se opakuje druhý běh po sobě.",
                    })
                else:
                    print(f"  (info) {detail} Poprvé — nehlásím, počkám na příští běh.")
                continue

            if not path.exists():
                path.write_text(json.dumps(new_fp, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                snapshots_written = True
                print("  → První běh, vytvořen referenční otisk.")
                continue

            old_fp = json.loads(path.read_text(encoding="utf-8"))
            result = compare_fingerprints(old_fp, new_fp)

            for note in result["info"]:
                print(f"  (info) {note}")

            if result["serious"]:
                print(f"  ⚠️  Zjištěny vážné změny ({len(result['problems'])}).")
                structure_issues.append({
                    "portal": portal["name"],
                    "page": page["label"],
                    "url": page["url"],
                    "problems": result["problems"],
                })
                if update_snapshots:
                    path.write_text(json.dumps(new_fp, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
                    snapshots_written = True
                    print("  → Reference přepsána novou podobou (UPDATE_SNAPSHOTS=1).")
            else:
                print("  ✓ Struktura beze změn.")

    # ---------- 2) Nefunkční portály: test dostupnosti ----------
    status_path = snap_dir / "_availability_status.json"
    old_statuses = {}
    if status_path.exists():
        old_statuses = json.loads(status_path.read_text(encoding="utf-8"))

    new_statuses = {}
    for portal in BLOCKED_PORTALS:
        print(f"Testuji dostupnost: {portal['name']} ({portal['url']})")
        status = check_availability(portal["url"], portal["expect"])
        new_statuses[portal["name"]] = status
        old = old_statuses.get(portal["name"])
        if availability_changed_for_better(old, status):
            opportunities.append({
                "portal": portal["name"],
                "detail": f"Portál nově odpovídá v pořádku (stav {status.get('status')}, "
                          f"typ {status.get('content_type')}). Dříve: {portal['note']}",
            })
            print("  ✅ Vypadá to na zlepšení!")
        else:
            print(f"  Stav beze změny (ok={status.get('ok')}).")

    status_path.write_text(json.dumps(new_statuses, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    fetch_errors_path.write_text(json.dumps(sorted(new_fetch_errors), ensure_ascii=False,
                                            indent=2), encoding="utf-8")
    snapshots_written = True

    # Informace pro workflow, jestli má uložit nové otisky — zapisujeme
    # PŘED odesláním e-mailu, aby se otisky uložily i při chybě e-mailu.
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"snapshots_written={'true' if snapshots_written else 'false'}\n")

    # ---------- 3) Hlášení ----------
    if structure_issues or opportunities or errors:
        body = build_email_body(structure_issues, opportunities, errors)
        print("\n--- Souhrn hlášení ---\n" + body)
        try:
            send_email("⚠️ Hlídač portálů: zjištěny změny", body)
            print("E-mail odeslán.")
        except Exception as exc:
            print(f"CHYBA: e-mail se nepodařilo odeslat: {exc}", file=sys.stderr)
            return 1
    else:
        print("\nVše v pořádku — žádné změny, e-mail se neposílá.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
