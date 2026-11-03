"""Web-Push: VAPID-Schlüssel, Versand an registrierte Geräte und Minuten-Takt für Erinnerungen."""
import base64
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

KEY_FILE = Path(os.environ.get(
    "VAPID_KEY_FILE", Path(os.environ.get("DB_PATH", "/data/abnehm.db")).parent / "vapid_private.pem"))
# Kontaktadresse für die Push-Dienste (Apple/Google/Mozilla), Pflichtangabe im VAPID-Token
SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:abnehm-app@example.org")

log = logging.getLogger("push")
_vapid: Vapid | None = None


def vapid() -> Vapid:
    """Schlüsselpaar beim ersten Start erzeugen und im Datenordner ablegen (bleibt über Updates erhalten)."""
    global _vapid
    if _vapid is None:
        if not KEY_FILE.exists():
            v = Vapid()
            v.generate_keys()
            v.save_key(str(KEY_FILE))
            log.info("Neues VAPID-Schlüsselpaar erzeugt: %s", KEY_FILE)
        _vapid = Vapid.from_file(str(KEY_FILE))
    return _vapid


def public_key() -> str:
    raw = vapid().public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def send(db, user_id: int, payload: dict) -> int:
    """Nachricht an alle Geräte eines Benutzers. Abgelaufene Abos werden entfernt. Gibt Anzahl Erfolge zurück."""
    with db() as conn:
        subs = conn.execute("SELECT * FROM push_subscriptions WHERE user_id=?", (user_id,)).fetchall()
    sent = 0
    for s in subs:
        try:
            webpush(
                {"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                json.dumps(payload, ensure_ascii=False),
                vapid_private_key=vapid(),
                vapid_claims={"sub": SUBJECT},  # wird von webpush ergänzt, daher jedes Mal neu
                ttl=3600,
                timeout=10,
            )
            sent += 1
        except WebPushException as e:
            code = e.response.status_code if e.response is not None else None
            if code in (403, 404, 410):  # Abo abgelaufen, abgemeldet oder zu altem Schlüssel
                with db() as conn:
                    conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (s["endpoint"],))
                log.info("Push-Abo entfernt (HTTP %s)", code)
            else:
                log.warning("Push fehlgeschlagen (HTTP %s): %s", code, e)
        except Exception as e:
            log.warning("Push fehlgeschlagen: %s", e)
    return sent


def start_scheduler(tick):
    """Ruft tick(minute) für jede volle Minute genau einmal auf."""
    def loop():
        last = datetime.now().replace(second=0, microsecond=0)
        while True:
            time.sleep(15)
            now = datetime.now().replace(second=0, microsecond=0)
            while last < now:
                last += timedelta(minutes=1)
                if now - last > timedelta(minutes=10):  # nach langer Pause keine alten Erinnerungen nachholen
                    continue
                try:
                    tick(last)
                except Exception:
                    log.exception("Fehler beim Erinnerungs-Check")

    threading.Thread(target=loop, name="push", daemon=True).start()
