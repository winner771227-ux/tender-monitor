from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from tender_monitor.config import EmailConfig


def send_report(email_config: EmailConfig, subject: str, body: str, attachments: list[Path]) -> bool:
    if not email_config.enabled:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_config.sender
    message["To"] = ", ".join(email_config.recipients)
    message.set_content(body)

    for attachment in attachments:
        message.add_attachment(
            attachment.read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename=attachment.name,
        )

    with smtplib.SMTP(email_config.host, email_config.port) as smtp:
        if email_config.use_tls:
            smtp.starttls()
        if email_config.username and email_config.password:
            smtp.login(email_config.username, email_config.password)
        smtp.send_message(message)
    return True
