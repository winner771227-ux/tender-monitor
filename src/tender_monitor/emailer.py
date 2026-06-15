from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from tender_monitor.config import EmailConfig

logger = logging.getLogger(__name__)


def send_report(email_config: EmailConfig, subject: str, body: str, attachments: list[Path]) -> bool:
    if not email_config.enabled:
        logger.warning(
            "Email VYPNUT – chybí konfigurace. "
            "host=%s sender=%s recipients=%s",
            email_config.host, email_config.sender, email_config.recipients,
        )
        return False

    logger.info(
        "Odesílám email: host=%s port=%s from=%s to=%s subject=%s",
        email_config.host, email_config.port,
        email_config.sender, email_config.recipients, subject,
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_config.sender
    message["To"] = ", ".join(email_config.recipients)
    message.set_content(body)

    for attachment in attachments:
        if attachment.exists():
            message.add_attachment(
                attachment.read_bytes(),
                maintype="application",
                subtype="octet-stream",
                filename=attachment.name,
            )
            logger.info("Příloha přidána: %s", attachment.name)

    try:
        with smtplib.SMTP(email_config.host, email_config.port, timeout=30) as smtp:
            smtp.ehlo()
            if email_config.use_tls:
                smtp.starttls()
                smtp.ehlo()
            if email_config.username and email_config.password:
                smtp.login(email_config.username, email_config.password)
            smtp.send_message(message)
        logger.info("Email úspěšně odeslán na %s", email_config.recipients)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("Email – chyba autentizace (špatné heslo nebo App Password): %s", exc)
    except smtplib.SMTPException as exc:
        logger.error("Email – SMTP chyba: %s", exc)
    except OSError as exc:
        logger.error("Email – síťová chyba (host=%s port=%s): %s", email_config.host, email_config.port, exc)
    except Exception as exc:
        logger.error("Email – neočekávaná chyba: %s", exc)
    return False
