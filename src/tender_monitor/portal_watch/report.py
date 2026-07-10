"""
Sestavení a odeslání e-mailového hlášení.

Používá stejné SMTP údaje jako hlavní monitoring — načítá je
z proměnných prostředí (v GitHub Actions se plní ze Secrets):
SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM,
SMTP_TO, SMTP_USE_TLS.

E-mail se posílá POUZE když je co hlásit — žádný šum.
"""

import os
import smtplib
from email.message import EmailMessage


def build_email_body(structure_issues: list, opportunities: list, errors: list) -> str:
    lines = ["Hlídač portálů veřejných zakázek zjistil následující:\n"]

    if structure_issues:
        lines.append("=" * 50)
        lines.append("⚠️  ZMĚNY STRUKTURY — hrozí rozbití scraperů")
        lines.append("=" * 50)
        for issue in structure_issues:
            lines.append(f"\n● Portál: {issue['portal']} ({issue['page']})")
            lines.append(f"  Adresa: {issue['url']}")
            for p in issue["problems"]:
                lines.append(f"  - {p}")
            lines.append(
                "  → Doporučení: zkontrolovat příslušný soubor v "
                "src/tender_monitor/scrapers/ a ověřit ranní e-mail se zakázkami."
            )

    if opportunities:
        lines.append("\n" + "=" * 50)
        lines.append("✅  PŘÍLEŽITOSTI — portál možná znovu funguje")
        lines.append("=" * 50)
        for opp in opportunities:
            lines.append(f"\n● {opp['portal']}: {opp['detail']}")
            lines.append("  → Stojí za to zkusit znovu zapojit příslušný scraper.")

    if errors:
        lines.append("\n" + "=" * 50)
        lines.append("❌  CHYBY PŘI KONTROLE")
        lines.append("=" * 50)
        for err in errors:
            lines.append(f"\n● {err['portal']}: {err['detail']}")

    lines.append(
        "\n---\nToto je automatická zpráva hlídače portálů (portal_watch). "
        "Referenční otisky jsou uloženy v src/tender_monitor/portal_snapshots/. "
        "Po opravě scraperu spusť workflow s volbou 'Aktualizovat otisky', "
        "aby se nová podoba portálu uložila jako nová reference."
    )
    return "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ.get("SMTP_FROM", username)
    recipient = os.environ["SMTP_TO"]
    # Pojistka: prázdná hodnota (chybějící secret) NESMÍ vypnout TLS
    use_tls = (os.environ.get("SMTP_USE_TLS") or "true").lower() in ("1", "true", "yes")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    # Stejný postup jako v hlavním emailer.py (ehlo → starttls → ehlo → login)
    with smtplib.SMTP(host, port, timeout=60) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        server.login(username, password)
        server.send_message(msg)
