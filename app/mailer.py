"""E-Mail-Versand über SMTP (Zugangsdaten aus Umgebungsvariablen, z. B. aus der .env auf dem Server)."""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "starttls").lower()  # starttls | ssl | none
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Bissfest")

log = logging.getLogger("mail")


def configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def send(to: str, subject: str, text: str, html: str | None = None, reply_to: str | None = None,
         attachments: list[tuple[str, bytes, str]] | None = None):
    """attachments: Liste von (Dateiname, Inhalt, MIME-Typ wie "image/jpeg")."""
    msg = EmailMessage()
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Message-ID"] = make_msgid(domain=SMTP_FROM.split("@")[-1] or None)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for filename, data, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

    ctx = ssl.create_default_context()
    if SMTP_SECURITY == "ssl":
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    with server:
        if SMTP_SECURITY == "starttls":
            server.starttls(context=ctx)
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    log.info("Mail an %s gesendet: %s", to, subject)
