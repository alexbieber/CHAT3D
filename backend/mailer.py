"""Transactional email via SMTP. Without SMTP settings, emails are logged instead."""
import logging
import smtplib
import threading
from email.message import EmailMessage

from . import config

log = logging.getLogger("chat3d.mail")


def send(to: str, subject: str, body: str) -> None:
    if not config.SMTP_HOST:
        log.warning("SMTP not configured; email to %s not sent.\nSubject: %s\n%s", to, subject, body)
        return

    def _go():
        try:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
            msg.set_content(body)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        except Exception:
            log.exception("Failed to send email to %s", to)

    threading.Thread(target=_go, daemon=True).start()
