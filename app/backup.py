"""Nächtliche Sicherung: SQLite-Datenbank + JSON-Export je Benutzer als .tar.gz ins Backup-Verzeichnis.

Manuell ausführen:  docker exec abnehm-app python backup.py
"""
import io
import json
import logging
import os
import sqlite3
import tarfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", "/data/abnehm.db")
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backup"))
BACKUP_TIME = os.environ.get("BACKUP_TIME", "03:00")  # leer = automatische Sicherung aus
BACKUP_KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "30"))
# Diese Datei muss im Backup-Ziel liegen. Fehlt sie (z. B. weil das NFS-Laufwerk nicht
# gemountet ist), wird nicht gesichert, statt still auf die lokale Platte zu schreiben.
MARKER = ".abnehm-backup-ziel"
PREFIX = "abnehm-backup-"

log = logging.getLogger("backup")


def _user_export(conn: sqlite3.Connection, user: sqlite3.Row) -> dict:
    uid = user["id"]
    rows = lambda sql: [dict(r) for r in conn.execute(sql, (uid,))]
    return {
        "benutzer": user["username"],
        "email": user["email"] if "email" in user.keys() else None,
        "erstellt": datetime.fromtimestamp(user["created"]).isoformat(),
        "einstellungen": {
            "mahlzeiten_pro_tag": user["meals_per_day"],
            "kcal_ziel": user["kcal_goal"],
            "wasser_ziel_ml": user["water_goal_ml"],
            "zielgewicht": user["target_weight"],
            **{k: user[k] for k in ("sex", "birth_year", "height_cm", "activity", "weekly_loss") if k in user.keys()},
        },
        "gewicht": rows("SELECT day, kg FROM weights WHERE user_id=? ORDER BY day"),
        "mahlzeiten": rows(
            "SELECT day, meal, name, brand, grams, kcal_100g, ROUND(kcal_100g*grams/100) AS kcal, "
            "protein_100g, carbs_100g, sugar_100g, fat_100g, source, code FROM food_entries "
            "WHERE user_id=? ORDER BY day, meal, created"),
        "wasser": rows("SELECT day, SUM(ml) AS ml FROM water_entries WHERE user_id=? GROUP BY day ORDER BY day"),
        **{name: rows("SELECT day, SUM(ml = 150) AS tassen, SUM(ml = 250) AS poette, SUM(ml) AS ml "
                      f"FROM coffee_entries WHERE user_id=? AND kind='{kind}' GROUP BY day ORDER BY day")
           for name, kind in (("kaffee", "coffee"), ("tee", "tea"))},
        "gerichte": [
            {"name": t["name"], "zutaten": [dict(i) for i in conn.execute(
                "SELECT name, brand, grams, kcal_100g, protein_100g, carbs_100g, sugar_100g, fat_100g "
                "FROM meal_template_items WHERE template_id=? ORDER BY id", (t["id"],))]}
            for t in conn.execute("SELECT id, name FROM meal_templates WHERE user_id=? ORDER BY name", (uid,))
        ],
    }


def run_backup() -> Path:
    if not (BACKUP_DIR / MARKER).exists():
        raise RuntimeError(f"{BACKUP_DIR}/{MARKER} fehlt – ist das Backup-Laufwerk gemountet?")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    target = BACKUP_DIR / f"{PREFIX}{stamp}.tar.gz"
    tmp_db = Path(DB_PATH).with_name("backup-tmp.db")

    # Konsistente Kopie über die SQLite-Backup-API (funktioniert auch während die App schreibt)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp_db)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    try:
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        exports = {f"benutzer/{u['username']}.json": _user_export(conn, u) for u in conn.execute("SELECT * FROM users")}
        # Gemeinsame Lebensmittel-Datenbank (von allen Nutzern angelegt)
        exports["lebensmittel-datenbank.json"] = [dict(r) for r in conn.execute(
            "SELECT f.name, f.manufacturer, f.brand, f.code, f.kcal_100g, f.protein_100g, f.carbs_100g, f.sugar_100g, "
            "f.fat_100g, u.username AS angelegt_von FROM custom_foods f LEFT JOIN users u ON u.id = f.created_by "
            "ORDER BY f.name")]
        conn.close()

        partial = target.with_suffix(".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(tmp_db, arcname="abnehm.db")
            vapid_key = Path(DB_PATH).with_name("vapid_private.pem")  # Push-Schlüssel, sonst müssen Geräte neu aktivieren
            if vapid_key.exists():
                tar.add(vapid_key, arcname="vapid_private.pem")
            for arcname, data in exports.items():
                blob = json.dumps(data, ensure_ascii=False, indent=1).encode()
                info = tarfile.TarInfo(arcname)
                info.size, info.mtime = len(blob), int(time.time())
                tar.addfile(info, io.BytesIO(blob))
        partial.rename(target)
    finally:
        tmp_db.unlink(missing_ok=True)

    _cleanup()
    (BACKUP_DIR / "letztes-backup.txt").write_text(
        f"{datetime.now().isoformat(timespec='seconds')}  {target.name}  {len(exports) - 1} Benutzer\n")
    log.info("Backup erstellt: %s (%d Benutzer)", target, len(exports) - 1)
    return target


def _cleanup():
    cutoff = time.time() - BACKUP_KEEP_DAYS * 86400
    for f in BACKUP_DIR.glob(f"{PREFIX}*"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            log.info("Altes Backup gelöscht: %s", f.name)


def _next_run(now: datetime) -> datetime:
    h, m = map(int, BACKUP_TIME.split(":"))
    run = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def _loop():
    while True:
        due = _next_run(datetime.now())
        log.info("Nächstes Backup: %s", due.isoformat(timespec="minutes"))
        while datetime.now() < due:
            time.sleep(min(60, max(1, (due - datetime.now()).total_seconds())))
        for attempt in range(3):  # NFS kann kurz weg sein
            try:
                run_backup()
                break
            except Exception as e:
                log.error("Backup fehlgeschlagen (Versuch %d/3): %s", attempt + 1, e)
                time.sleep(600)


def start_scheduler():
    if not BACKUP_TIME:
        log.info("Automatisches Backup deaktiviert (BACKUP_TIME leer)")
        return
    threading.Thread(target=_loop, name="backup", daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(run_backup())
