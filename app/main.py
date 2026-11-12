"""Bissfest – mit Biss zum Wunschgewicht: kleines Multi-User-Backend (FastAPI + SQLite)."""
import asyncio
import base64
import hashlib
import json
import hmac
import logging
import os
import re
import secrets
import shutil
import sqlite3
import time
from urllib.parse import urlparse
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import backup
import food_import
import mailer
import push
import sports
import trophies

DB_PATH = os.environ.get("DB_PATH", "/data/abnehm.db")
ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "true").lower() == "true"
# Höchstzahl an Konten (0 = unbegrenzt); im Admin-Dashboard zur Laufzeit änderbar
MAX_USERS_DEFAULT = int(os.environ.get("MAX_USERS", "0"))
# Admins für /admin (Benutzernamen, kommagetrennt) – bewusst nur per Konfiguration, nicht über die Oberfläche
ADMIN_USERS = {n.strip().lower() for n in os.environ.get("ADMIN_USERS", "").split(",") if n.strip()}
FDC_API_KEY = os.environ.get("FDC_API_KEY", "DEMO_KEY")  # USDA FoodData Central, leer = aus
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx schreibt sonst den API-Key mit der URL ins Log
OFF_COUNTRY = os.environ.get("OFF_COUNTRY", "de")
# Öffentliche Adresse für Links in E-Mails. Pflicht für den Mailversand: Aus dem Host-Header der Anfrage
# ließen sich Links zum Zurücksetzen des Passworts auf eine fremde Domain umleiten.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
# Ohne Angabe automatisch an, sobald PUBLIC_URL mit https:// beginnt
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true" if PUBLIC_URL.startswith("https://") else "false").lower() == "true"
VERIFY_HOURS = 24
RESET_HOURS = 1
# "Spendier mir einen Kaffee": Wero-Bezahllink (leer = Karte ausgeblendet)
DONATE_WERO_URL = "https://share.wero-wallet.eu/p/1/c/0iOnTczDCg"  # fest vorgegeben, siehe NOTICE.md
# Android-App (Trusted Web Activity): Paketname + SHA-256 des Signaturzertifikats für assetlinks.json,
# die APK selbst liegt im Datenordner (nicht im Image/git) und wird unter /download/bissfest.apk angeboten
# Standard = offizielle Bissfest-App: Dadurch läuft sie auf jedem Bissfest-Server im Vollbild. Nur wer eine
# eigene APK baut, setzt hier eigene Werte.
ANDROID_PACKAGE = os.environ.get("ANDROID_PACKAGE", "de.facgooder.bissfest")
ANDROID_CERT_SHA256 = os.environ.get(
    "ANDROID_CERT_SHA256", "6E:84:21:EC:C2:27:3B:9C:4F:24:CD:2D:5F:2B:C9:B9:55:C7:96:4C:5E:3D:BA:91:1A:35:11:10:A8:13:27:3E")
OFFICIAL_APK_URL = "https://bissfest.facgooder.de/download/bissfest.apk"
APK_PATH = Path(DB_PATH).parent / "downloads" / "bissfest.apk"
# Bei jeder neuen APK anpassen (VERSION_NAME in android/build-apk.mjs); wer eine eigene APK anbietet, setzt die Variable
ANDROID_APP_VERSION = os.environ.get("ANDROID_APP_VERSION", "2.7.1")
# Empfänger für Feature Requests aus der App
FEEDBACK_TO = "service@facgooder.de"  # fest vorgegeben, siehe NOTICE.md
# Quellcode-Link in den Einstellungen (AGPL-3.0 § 13: Pflicht beim Betrieb geänderter Fassungen)
SOURCE_URL = os.environ.get("SOURCE_URL", "")
UNVERIFIED_KEEP_DAYS = 7
SESSION_DAYS = 60
USER_AGENT = "Bissfest/1.4 (self-hosted)"
STATIC_DIR = Path(__file__).parent / "static"

APP_VERSION = "2.19.1"

# Feste Mahlzeiten in fester Reihenfolge: bei 3 Mahlzeiten pro Tag sind es die drei Hauptmahlzeiten,
# jede weitere ist ein Snack. Je Mahlzeit: (Name, Emoji für die Push-Nachricht, Text der Push-Nachricht)
MEALS = [
    ("Frühstück", "🍳", "Hey, trage jetzt bitte dein Frühstück ein."),
    ("Mittagessen", "🍝", "Hey, trage jetzt bitte dein Mittagessen ein."),
    ("Abendessen", "🍲", "Hey, trage jetzt bitte dein Abendessen ein."),
    ("Snack 1", "🍎", "Hey, trage jetzt bitte deinen Snack ein."),
    ("Snack 2", "🍎", "Hey, trage jetzt bitte deinen Snack ein."),
    ("Snack 3", "🍎", "Hey, trage jetzt bitte deinen Snack ein."),
    ("Snack 4", "🍎", "Hey, trage jetzt bitte deinen Snack ein."),
]
MEAL_NAMES = [m[0] for m in MEALS]
# Reihenfolge und Namen bis Version 1.6 (für die Umstellung bestehender Konten)
OLD_MEAL_NAMES = ["Frühstück", "Snack 1", "Mittagessen", "Snack 2", "Abendessen", "Snack 3", "Spätmahlzeit"]

# Keine Inline-Skripte (Theme-Startcode liegt in /static/theme-init.js); Inline-Styles nutzt die Oberfläche
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; font-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
MAX_BODY_BYTES = 8 * 1024 * 1024  # größter erlaubter Upload: Screenshot 3 MB (als Base64 etwa 4 MB)


@asynccontextmanager
async def lifespan(_app):
    init_db()
    if mailer.configured() and not PUBLIC_URL:
        logging.getLogger("mail").warning("SMTP ist eingerichtet, aber PUBLIC_URL fehlt: Registrierung und "
                                          "Passwort-Mails bleiben aus, bis PUBLIC_URL gesetzt ist.")
    yield


app = FastAPI(title="Bissfest", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Anfrage zu groß"}, status_code=413)
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if not request.url.path.startswith(("/docs", "/redoc")):  # API-Doku lädt Skripte von einem CDN
        headers.setdefault("Content-Security-Policy", CSP)
    return response


# ---------------------------------------------------------------- Datenbank

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    meals_per_day INTEGER NOT NULL DEFAULT 5,
    meal_names TEXT NOT NULL DEFAULT '',
    kcal_goal INTEGER NOT NULL DEFAULT 2000,
    water_goal_ml INTEGER NOT NULL DEFAULT 2500,
    target_weight REAL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS food_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    meal INTEGER NOT NULL,
    name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    grams REAL NOT NULL,
    kcal_100g REAL NOT NULL,
    protein_100g REAL,
    carbs_100g REAL,
    fat_100g REAL,
    source TEXT NOT NULL DEFAULT 'manuell',
    code TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_food_user_day ON food_entries(user_id, day);
CREATE TABLE IF NOT EXISTS water_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    ml INTEGER NOT NULL,
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_water_user_day ON water_entries(user_id, day);
-- Heißgetränke (Kaffee und Tee, Spalte "kind" per Migration); Tabellenname historisch
CREATE TABLE IF NOT EXISTS coffee_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    ml INTEGER NOT NULL,
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coffee_user_day ON coffee_entries(user_id, day);
CREATE TABLE IF NOT EXISTS weights (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    kg REAL NOT NULL,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS custom_foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    manufacturer TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL DEFAULT '',
    kcal_100g REAL NOT NULL,
    protein_100g REAL,
    carbs_100g REAL,
    sugar_100g REAL,
    fat_100g REAL,
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_custom_foods_code ON custom_foods(code);
CREATE TABLE IF NOT EXISTS meal_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meal_template_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES meal_templates(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    grams REAL NOT NULL,
    kcal_100g REAL NOT NULL,
    protein_100g REAL,
    carbs_100g REAL,
    sugar_100g REAL,
    fat_100g REAL,
    source TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS email_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS food_portions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    food_key TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    grams REAL NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created INTEGER NOT NULL,
    UNIQUE (food_key, name)
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    text TEXT NOT NULL,
    mailed INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    created INTEGER NOT NULL,
    last_used INTEGER
);
CREATE TABLE IF NOT EXISTS sport_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    sport TEXT NOT NULL,
    name TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    kcal INTEGER NOT NULL,
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sport_user_day ON sport_entries(user_id, day);
CREATE TABLE IF NOT EXISTS user_trophies (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    earned INTEGER NOT NULL,
    PRIMARY KEY (user_id, key)
);
-- Favoriten: Momentaufnahme des Lebensmittels als JSON, eindeutig nach Name + Marke
CREATE TABLE IF NOT EXISTS favorite_foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    data TEXT NOT NULL,
    created INTEGER NOT NULL,
    UNIQUE (user_id, key)
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created INTEGER NOT NULL
);
"""

# Später ergänzte Spalten (per Migration): Körperdaten für die Defizit-Berechnung und
# Erinnerungszeiten als JSON {"<Mahlzeit-Index>": "HH:MM"}
PROFILE_COLUMNS = {"pw_iterations": "INTEGER", "sex": "TEXT", "birth_year": "INTEGER", "height_cm": "REAL", "activity": "REAL", "weekly_loss": "REAL",
                   "reminders": "TEXT", "email": "TEXT",
                   # bestehende Konten gelten als bestätigt; neue Registrierungen setzen 0
                   "email_verified": "INTEGER NOT NULL DEFAULT 1",
                   # Kaffee-Tracker (Extra, standardmäßig aus) und Koffein pro Tasse
                   "coffee_enabled": "INTEGER NOT NULL DEFAULT 0", "caffeine_mg": "INTEGER NOT NULL DEFAULT 80",
                   "tea_enabled": "INTEGER NOT NULL DEFAULT 0", "tea_caffeine_mg": "INTEGER NOT NULL DEFAULT 40",
                   "espresso_caffeine_mg": "INTEGER NOT NULL DEFAULT 65",
                   "sport_enabled": "INTEGER NOT NULL DEFAULT 1",
                   # 1 = Hinweis "Kalorienziel ist nur ein Beispielwert" bewusst ausgeblendet ("Mein Ziel passt so")
                   "goal_hint_dismissed": "INTEGER NOT NULL DEFAULT 0",
                   # Profilbild: 0 = keins, sonst Zeitstempel des Uploads (auch für den Browser-Cache)
                   "avatar_version": "INTEGER NOT NULL DEFAULT 0"}
# Später ergänzte Spalten für Einträge und Gericht-Zutaten: Zucker und die gewählte Portion ("2 × Scheibe")
ENTRY_COLUMNS = {"sugar_100g": "REAL", "portion_name": "TEXT", "portion_count": "REAL"}
# Kaffee-Größen in ml; das eingestellte Koffein (caffeine_mg) gilt für eine Tasse
COFFEE_SIZES = {"cup": 150, "mug": 250, "espresso": 30}
ACTIVITY_FACTORS = (1.2, 1.375, 1.55, 1.725, 1.9)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate_meal_order(conn: sqlite3.Connection):
    """Einmalige Umstellung (1.7.0) von frei benennbaren Mahlzeiten auf die feste Reihenfolge MEALS.
    Einträge und Erinnerungen wandern anhand des Namens auf die neue Position. Läuft nur, solange
    PRAGMA user_version < 1 ist, und setzt sie danach auf 1."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 1:
        return
    for u in conn.execute("SELECT id, meals_per_day, meal_names, reminders FROM users").fetchall():
        stored = (u["meal_names"] or "").split("|")
        mapping = {}
        for i in range(7):
            name = stored[i].strip() if i < len(stored) and stored[i].strip() else OLD_MEAL_NAMES[i]
            name = "Snack 4" if name == "Spätmahlzeit" else name
            mapping[i] = MEAL_NAMES.index(name) if name in MEAL_NAMES else i
        if any(old != new for old, new in mapping.items()):
            case = " ".join(f"WHEN {old} THEN {new}" for old, new in mapping.items())
            conn.execute(f"UPDATE food_entries SET meal = CASE meal {case} ELSE meal END WHERE user_id=?", (u["id"],))
        reminders = {str(mapping.get(int(k), int(k))): v for k, v in json.loads(u["reminders"] or "{}").items()}
        used = [mapping[i] for i in range(u["meals_per_day"])] + [int(k) for k in reminders]
        meals_per_day = min(7, max(3, max(used) + 1))
        conn.execute("UPDATE users SET meal_names='', reminders=?, meals_per_day=? WHERE id=?",
                     (json.dumps(reminders), meals_per_day, u["id"]))
        logging.getLogger("migration").info("Mahlzeiten von Benutzer %s umgestellt: %s", u["id"], mapping)
    conn.execute("PRAGMA user_version = 1")


def init_db():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     [%(name)s] %(message)s")
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        for col, typ in PROFILE_COLUMNS.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        for table in ("food_entries", "meal_template_items"):
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in ENTRY_COLUMNS.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        conn.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
        # Bis 2.18 lagen Sitzungen im Klartext; Hashes sind 64 Zeichen lang, alte Tokens 43
        for r in conn.execute("SELECT token FROM sessions WHERE LENGTH(token) != 64").fetchall():
            conn.execute("UPDATE sessions SET token=? WHERE token=?", (session_hash(r["token"]), r["token"]))
        migrate_meal_order(conn)
        # 1.13 zählte nur Tassen (Tabelle "coffee"): als 150-ml-Tassen übernehmen, dann alte Tabelle entfernen
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coffee'").fetchone():
            for r in conn.execute("SELECT user_id, day, cups FROM coffee WHERE cups > 0").fetchall():
                conn.executemany("INSERT INTO coffee_entries (user_id, day, ml, created) VALUES (?,?,?,?)",
                                 [(r["user_id"], r["day"], COFFEE_SIZES["cup"], 0)] * r["cups"])
            conn.execute("DROP TABLE coffee")
        if "payload" not in {r["name"] for r in conn.execute("PRAGMA table_info(email_tokens)")}:
            conn.execute("ALTER TABLE email_tokens ADD COLUMN payload TEXT")
        have = {r["name"] for r in conn.execute("PRAGMA table_info(feedback)")}
        for col, typ in (("kind", "TEXT NOT NULL DEFAULT 'feature'"), ("meta", "TEXT"), ("attachment", "TEXT"),
                         ("status", "TEXT NOT NULL DEFAULT 'offen'")):
            if col not in have:
                conn.execute(f"ALTER TABLE feedback ADD COLUMN {col} {typ}")
        if "kind" not in {r["name"] for r in conn.execute("PRAGMA table_info(coffee_entries)")}:
            conn.execute("ALTER TABLE coffee_entries ADD COLUMN kind TEXT NOT NULL DEFAULT 'coffee'")
        # Einträge aus der Bissfest-DB ohne Barcode bekommen "custom:<id>" als Code (stabiler Portionsschlüssel)
        for table in ("food_entries", "meal_template_items"):
            conn.execute(f"""UPDATE {table} SET code = 'custom:' || (SELECT cf.id FROM custom_foods cf
                             WHERE cf.name = {table}.name AND cf.code = '' ORDER BY cf.id LIMIT 1)
                             WHERE code = '' AND source = 'Bissfest-DB'
                             AND EXISTS (SELECT 1 FROM custom_foods cf WHERE cf.name = {table}.name AND cf.code = '')""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email COLLATE NOCASE) "
                     "WHERE email IS NOT NULL")
        cleanup_unverified(conn)
    backup.start_scheduler()
    push.vapid()
    push.start_scheduler(send_meal_reminders)


# ---------------------------------------------------------------- Auth

PW_ITERATIONS = 600_000  # OWASP-Empfehlung für PBKDF2-SHA256
PW_ITERATIONS_LEGACY = 200_000  # Konten ohne gespeicherte Rundenzahl, beim nächsten Login hochgestuft


def hash_pw(password: str, salt: str, iterations: int = PW_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()


def pw_iter(u: sqlite3.Row) -> int:
    return u["pw_iterations"] or PW_ITERATIONS_LEGACY


def session_hash(token: str) -> str:
    """Sitzungen liegen nur als Hash in der Datenbank, ein Backup taugt so nicht zum Anmelden."""
    return hashlib.sha256(token.encode()).hexdigest()


_login_attempts: dict[str, list[float]] = {}
THROTTLE_MAX_KEYS = 10_000
THROTTLE_MAX_WINDOW = 3600


def throttle(key: str, limit: int = 10, window: int = 300, count: bool = True):
    now = time.time()
    if len(_login_attempts) > THROTTLE_MAX_KEYS:  # Speicher begrenzen: abgelaufene Einträge entfernen
        for k in [k for k, v in _login_attempts.items() if not v or now - v[-1] > THROTTLE_MAX_WINDOW]:
            del _login_attempts[k]
    hits = [t for t in _login_attempts.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Zu viele Versuche, bitte später erneut probieren.")
    if count:
        hits.append(now)
    _login_attempts[key] = hits


def note_attempt(key: str):
    _login_attempts.setdefault(key, []).append(time.time())


def current_user(request: Request) -> sqlite3.Row:
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(401, "Nicht angemeldet")
    with db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires > ?",
            (session_hash(token), int(time.time())),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Sitzung abgelaufen")
    return row


def start_session(response: Response, user_id: int):
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires) VALUES (?, ?, ?)",
            (session_hash(token), user_id, int(time.time()) + SESSION_DAYS * 86400),
        )
    response.set_cookie(
        "session", token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=COOKIE_SECURE
    )


def user_public(u: sqlite3.Row) -> dict:
    meal_names = MEAL_NAMES[:u["meals_per_day"]]
    return {
        "username": u["username"],
        "email": u["email"],
        "meals_per_day": u["meals_per_day"],
        "meal_names": meal_names,
        "kcal_goal": u["kcal_goal"],
        "water_goal_ml": u["water_goal_ml"],
        "target_weight": u["target_weight"],
        "sex": u["sex"],
        "birth_year": u["birth_year"],
        "height_cm": u["height_cm"],
        "activity": u["activity"],
        "weekly_loss": u["weekly_loss"],
        "latest_weight": latest_weight(u["id"]),
        "reminders": json.loads(u["reminders"] or "{}"),
        "push_devices": push_device_count(u["id"]),
        "is_admin": is_admin(u),
        "goal_hint_dismissed": bool(u["goal_hint_dismissed"]),
        "avatar_version": u["avatar_version"],
        "coffee_enabled": bool(u["coffee_enabled"]),
        "espresso_caffeine_mg": u["espresso_caffeine_mg"],
        "sport_enabled": bool(u["sport_enabled"]),
        "caffeine_mg": u["caffeine_mg"],
        "tea_enabled": bool(u["tea_enabled"]),
        "tea_caffeine_mg": u["tea_caffeine_mg"],
    }


def latest_weight(user_id: int) -> float | None:
    with db() as conn:
        row = conn.execute("SELECT kg FROM weights WHERE user_id=? ORDER BY day DESC LIMIT 1", (user_id,)).fetchone()
    return row["kg"] if row else None


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=200)


def get_setting(key: str, default: str) -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def max_users() -> int:
    return int(get_setting("max_users", str(MAX_USERS_DEFAULT)))


def user_count() -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def registration_full() -> bool:
    """Alle Plätze belegt? Unbestätigte Konten zählen mit, sie werden nach UNVERIFIED_KEEP_DAYS Tagen gelöscht."""
    limit = max_users()
    return limit > 0 and user_count() >= limit


def registration_open() -> bool:
    """Registrierung: per Konfiguration erlaubt, im Admin-Dashboard nicht gesperrt, Mailversand eingerichtet, Platz frei."""
    return (ALLOW_REGISTRATION and get_setting("registration_open", "1") == "1" and mail_ready()
            and not registration_full())


def is_admin(u: sqlite3.Row) -> bool:
    return u["username"].lower() in ADMIN_USERS


@app.get("/api/config")
def config():
    return {"allow_registration": registration_open(), "registration_full": registration_full(),
            "version": APP_VERSION, "mail_enabled": mail_ready(),
            "donate_wero": DONATE_WERO_URL, "source_url": SOURCE_URL,
            # eigene APK im Datenordner, sonst die offizielle (fragt beim ersten Start nach dem Server)
            "android_apk_url": "/download/bissfest.apk" if APK_PATH.exists() else OFFICIAL_APK_URL,
            "android_apk_version": ANDROID_APP_VERSION, "android_package": ANDROID_PACKAGE}


class RegisterIn(Credentials):
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def cleanup_unverified(conn: sqlite3.Connection):
    """Nie bestätigte Konten nach UNVERIFIED_KEEP_DAYS Tagen und abgelaufene Links löschen."""
    conn.execute("DELETE FROM users WHERE email_verified = 0 AND created < ?",
                 (int(time.time()) - UNVERIFIED_KEEP_DAYS * 86400,))
    conn.execute("DELETE FROM email_tokens WHERE expires < ?", (int(time.time()),))


def mail_ready() -> bool:
    """Mails mit Links nur mit fester PUBLIC_URL, nie aus dem Host-Header der Anfrage."""
    return mailer.configured() and bool(PUBLIC_URL)


def public_base(request: Request) -> str:
    if not PUBLIC_URL:
        raise HTTPException(503, "PUBLIC_URL ist nicht gesetzt")
    return PUBLIC_URL


def send_verification(request: Request, user_id: int, username: str, email: str):
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("DELETE FROM email_tokens WHERE user_id=? AND purpose='verify'", (user_id,))
        conn.execute("INSERT INTO email_tokens (token_hash, user_id, purpose, expires) VALUES (?,?,?,?)",
                     (hashlib.sha256(token.encode()).hexdigest(), user_id, "verify",
                      int(time.time()) + VERIFY_HOURS * 3600))
    base = public_base(request)
    link = f"{base}/verify?token={token}"
    text = (f"Hallo {username},\n\nwillkommen bei Bissfest – mit Biss zum Wunschgewicht!\n\n"
            f"Bitte bestätige deine E-Mail-Adresse, damit du dich anmelden kannst:\n{link}\n\n"
            f"Der Link ist {VERIFY_HOURS} Stunden gültig. Wenn du dich nicht bei Bissfest registriert hast, "
            f"kannst du diese Mail ignorieren – das Konto wird dann automatisch gelöscht.\n")
    html = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px;margin:auto;color:#17201b">
  <img src="{base}/static/logo.png" alt="Bissfest" width="160" style="display:block;margin:0 auto 16px;border-radius:16px">
  <p>Hallo {username},</p>
  <p>willkommen bei <b>Bissfest</b> – mit Biss zum Wunschgewicht!</p>
  <p>Bitte bestätige deine E-Mail-Adresse, damit du dich anmelden kannst:</p>
  <p style="text-align:center;margin:28px 0">
    <a href="{link}" style="background:#2e8b5f;color:#fff;padding:14px 24px;border-radius:12px;text-decoration:none;font-weight:600">E-Mail-Adresse bestätigen</a>
  </p>
  <p style="font-size:13px;color:#66736b">Der Link ist {VERIFY_HOURS} Stunden gültig. Wenn du dich nicht bei Bissfest
    registriert hast, kannst du diese Mail ignorieren – das Konto wird dann automatisch gelöscht.</p>
</div>"""
    mailer.send(email, "Bitte bestätige deine E-Mail-Adresse für Bissfest", text, html)


@app.post("/api/register")
def register(creds: RegisterIn, request: Request):
    if not ALLOW_REGISTRATION or get_setting("registration_open", "1") != "1":
        raise HTTPException(403, "Registrierung ist deaktiviert")
    if not mail_ready():
        raise HTTPException(503, "Registrierung ist gerade nicht möglich: Der E-Mail-Versand ist noch nicht eingerichtet.")
    throttle("reg:" + (request.client.host if request.client else "?"), limit=5, window=3600)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", creds.username):
        raise HTTPException(400, "Benutzername: nur Buchstaben, Zahlen, _ . -")
    if creds.username.lower() in ADMIN_USERS:
        # Admin-Rechte hängen am Namen: Ein freier Admin-Name (noch nicht registriert, gelöscht) wäre sonst übernehmbar
        logging.getLogger("auth").warning("Registrierung mit Admin-Namen %s abgelehnt. Erst registrieren, "
                                          "dann in ADMIN_USERS eintragen.", creds.username)
        raise HTTPException(409, "Benutzername ist bereits vergeben")
    email = creds.email.strip().lower()
    salt = secrets.token_hex(16)
    with db() as conn:
        cleanup_unverified(conn)
    if registration_full():
        raise HTTPException(403, "Auf diesem Server sind alle Plätze belegt")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (creds.username,)).fetchone():
            raise HTTPException(409, "Benutzername ist bereits vergeben")
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Für diese E-Mail-Adresse gibt es schon ein Konto")
        user_id = conn.execute(
            "INSERT INTO users (username, pw_hash, salt, pw_iterations, created, email, email_verified) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (creds.username, hash_pw(creds.password, salt), salt, PW_ITERATIONS, int(time.time()), email),
        ).lastrowid
    try:
        send_verification(request, user_id, creds.username, email)
    except Exception as e:
        logging.getLogger("mail").error("Bestätigungsmail an %s fehlgeschlagen: %s", email, e)
        with db() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        raise HTTPException(502, "Die Bestätigungsmail konnte nicht verschickt werden. Bitte später erneut versuchen.")
    return {"verify": True, "email": email}


@app.post("/api/resend-verification")
def resend_verification(creds: Credentials, request: Request):
    """Bestätigungsmail erneut senden – nur mit korrektem Passwort, damit niemand fremde Postfächer zuspammt."""
    throttle("resend:" + creds.username.lower(), limit=3, window=3600)
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE username = ?", (creds.username,)).fetchone()
    if not u or not hmac.compare_digest(u["pw_hash"], hash_pw(creds.password, u["salt"], pw_iter(u))):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    if u["email_verified"]:
        return {"ok": True, "already": True}
    try:
        send_verification(request, u["id"], u["username"], u["email"])
    except Exception as e:
        logging.getLogger("mail").error("Bestätigungsmail an %s fehlgeschlagen: %s", u["email"], e)
        raise HTTPException(502, "Die Mail konnte nicht verschickt werden. Bitte später erneut versuchen.")
    return {"ok": True, "email": u["email"]}


class EmailCorrection(Credentials):
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/api/correct-email")
def correct_email(c: EmailCorrection, request: Request):
    """Tippfehler bei der Registrierung beheben: nur mit Passwort und nur für unbestätigte Konten.
    send_verification() ersetzt dabei den alten Link, die Mail an die falsche Adresse wird also wertlos."""
    throttle("fixmail:" + c.username.lower(), limit=5, window=3600)
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE username = ?", (c.username,)).fetchone()
    if not u or not hmac.compare_digest(u["pw_hash"], hash_pw(c.password, u["salt"], pw_iter(u))):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    if u["email_verified"]:
        raise HTTPException(400, "Dieses Konto ist bereits bestätigt")
    email = c.email.strip().lower()
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=? AND id!=?", (email, u["id"])).fetchone():
            raise HTTPException(409, "Für diese E-Mail-Adresse gibt es schon ein Konto")
        conn.execute("UPDATE users SET email=? WHERE id=?", (email, u["id"]))
    try:
        send_verification(request, u["id"], u["username"], email)
    except Exception as e:
        logging.getLogger("mail").error("Bestätigungsmail an %s fehlgeschlagen: %s", email, e)
        raise HTTPException(502, "Die Mail konnte nicht verschickt werden. Bitte später erneut versuchen.")
    return {"ok": True, "email": email}


@app.get("/verify")
def verify_email(token: str = ""):
    """Link aus der Bestätigungsmail: Konto freischalten und direkt anmelden."""
    with db() as conn:
        row = conn.execute(
            "SELECT user_id FROM email_tokens WHERE token_hash=? AND purpose='verify' AND expires > ?",
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        if row:
            conn.execute("UPDATE users SET email_verified=1 WHERE id=?", (row["user_id"],))
            conn.execute("DELETE FROM email_tokens WHERE user_id=? AND purpose='verify'", (row["user_id"],))
    if not row:
        return RedirectResponse("/?verified=0", status_code=303)
    response = RedirectResponse("/?verified=1", status_code=303)
    start_session(response, row["user_id"])
    return response


@app.post("/api/login")
def login(creds: Credentials, request: Request, response: Response):
    name = creds.username.lower()
    throttle("login:" + (request.client.host if request.client else "?") + ":" + name)
    # Zusätzlich je Konto, unabhängig von der IP: Fehlversuche aus vielen Netzen bremsen
    throttle("login-fail:" + name, limit=30, window=900, count=False)
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE username = ?", (creds.username,)).fetchone()
    if not u or not hmac.compare_digest(u["pw_hash"], hash_pw(creds.password, u["salt"], pw_iter(u))):
        note_attempt("login-fail:" + name)
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    if pw_iter(u) < PW_ITERATIONS:
        salt = secrets.token_hex(16)
        with db() as conn:
            conn.execute("UPDATE users SET pw_hash=?, salt=?, pw_iterations=? WHERE id=?",
                         (hash_pw(creds.password, salt), salt, PW_ITERATIONS, u["id"]))
    if not u["email_verified"]:
        raise HTTPException(403, f"Bitte bestätige zuerst deine E-Mail-Adresse – wir haben dir einen Link an {u['email']} geschickt.")
    start_session(response, u["id"])
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (session_hash(token),))
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def me(u=Depends(current_user)):
    return user_public(u)


class Settings(BaseModel):
    meals_per_day: int = Field(ge=3, le=7)
    meal_names: list[str] = []  # veraltet (seit 1.7.0 feste Namen), wird ignoriert
    kcal_goal: int = Field(ge=800, le=6000)
    water_goal_ml: int = Field(ge=500, le=8000)
    target_weight: float | None = Field(default=None, ge=30, le=400)
    sex: Literal["m", "w"] | None = None
    birth_year: int | None = Field(default=None, ge=1920, le=date.today().year - 14)
    height_cm: float | None = Field(default=None, ge=120, le=230)
    activity: float | None = None
    weekly_loss: float | None = Field(default=None, ge=0, le=1)
    reminders: dict[str, str] | None = None
    coffee_enabled: bool | None = None
    caffeine_mg: int | None = Field(default=None, ge=0, le=500)
    tea_enabled: bool | None = None
    tea_caffeine_mg: int | None = Field(default=None, ge=0, le=500)
    espresso_caffeine_mg: int | None = Field(default=None, ge=0, le=500)
    sport_enabled: bool | None = None


@app.put("/api/settings")
def save_settings(s: Settings, u=Depends(current_user)):
    if s.activity is not None and s.activity not in ACTIVITY_FACTORS:
        raise HTTPException(400, "Ungültiger Aktivitätsfaktor")
    if s.reminders is not None:
        clean = {}
        for meal, hhmm in s.reminders.items():
            if not hhmm:
                continue
            if not (meal.isdigit() and int(meal) < s.meals_per_day and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hhmm)):
                raise HTTPException(400, "Ungültige Erinnerungszeit")
            clean[meal] = hhmm
    with db() as conn:
        if s.reminders is not None:
            conn.execute("UPDATE users SET reminders=? WHERE id=?", (json.dumps(clean), u["id"]))
        conn.execute(
            "UPDATE users SET meals_per_day=?, kcal_goal=?, water_goal_ml=?, target_weight=?, "
            "sex=?, birth_year=?, height_cm=?, activity=?, weekly_loss=?, "
            "coffee_enabled=COALESCE(?, coffee_enabled), caffeine_mg=COALESCE(?, caffeine_mg), "
            "tea_enabled=COALESCE(?, tea_enabled), tea_caffeine_mg=COALESCE(?, tea_caffeine_mg), "
            "espresso_caffeine_mg=COALESCE(?, espresso_caffeine_mg), sport_enabled=COALESCE(?, sport_enabled) WHERE id=?",
            (s.meals_per_day, s.kcal_goal, s.water_goal_ml, s.target_weight,
             s.sex, s.birth_year, s.height_cm, s.activity, s.weekly_loss,
             None if s.coffee_enabled is None else int(s.coffee_enabled), s.caffeine_mg,
             None if s.tea_enabled is None else int(s.tea_enabled), s.tea_caffeine_mg, s.espresso_caffeine_mg,
             None if s.sport_enabled is None else int(s.sport_enabled), u["id"]),
        )
        u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    return user_public(u)


@app.post("/api/goal-hint/dismiss")
def dismiss_goal_hint(u=Depends(current_user)):
    """Nutzer behält sein Kalorienziel bewusst ohne Berechnung – Hinweis nicht mehr anzeigen."""
    with db() as conn:
        conn.execute("UPDATE users SET goal_hint_dismissed=1 WHERE id=?", (u["id"],))
    return {"ok": True}


# ---------------------------------------------------------------- Passwort vergessen / E-Mail ändern

def new_mail_token(user_id: int, purpose: str, hours: int, payload: str | None = None) -> str:
    """Einmal-Token für Mail-Links; ein neuer Token ersetzt ältere gleichen Zwecks, gespeichert wird nur der Hash."""
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("DELETE FROM email_tokens WHERE user_id=? AND purpose=?", (user_id, purpose))
        conn.execute("INSERT INTO email_tokens (token_hash, user_id, purpose, expires, payload) VALUES (?,?,?,?,?)",
                     (hashlib.sha256(token.encode()).hexdigest(), user_id, purpose,
                      int(time.time()) + hours * 3600, payload))
    return token


def take_mail_token(token: str, purpose: str) -> sqlite3.Row | None:
    """Gültigen Token einlösen (danach ist er verbraucht)."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db() as conn:
        row = conn.execute("SELECT * FROM email_tokens WHERE token_hash=? AND purpose=? AND expires > ?",
                           (digest, purpose, int(time.time()))).fetchone()
        if row:
            conn.execute("DELETE FROM email_tokens WHERE token_hash=?", (digest,))
    return row


def mail_html(base: str, paragraphs: list[str], button: str | None = None, link: str | None = None,
              small: str = "") -> str:
    """Einheitliches Mail-Layout; Texte werden hier nicht escaped, nur eigene Inhalte übergeben."""
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    cta = (f'''<p style="text-align:center;margin:28px 0"><a href="{link}" style="background:#2e8b5f;color:#fff;
        padding:14px 24px;border-radius:12px;text-decoration:none;font-weight:600">{button}</a></p>''' if link else "")
    return (f'''<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px;margin:auto;color:#17201b">
  <img src="{base}/static/logo.png" alt="Bissfest" width="160" style="display:block;margin:0 auto 16px;border-radius:16px">
  {body}{cta}<p style="font-size:13px;color:#66736b">{small}</p></div>''')


def send_quietly(to: str, subject: str, text: str, html: str):
    """Für Hintergrund-Aufgaben: Fehler nur loggen (die Antwort an den Nutzer ist schon raus)."""
    try:
        mailer.send(to, subject, text, html)
    except Exception as e:
        logging.getLogger("mail").error("Mail an %s fehlgeschlagen: %s", to, e)


class ResetRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=200)  # Benutzername oder E-Mail-Adresse


@app.post("/api/password-reset/request")
def password_reset_request(r: ResetRequest, request: Request, tasks: BackgroundTasks):
    """Antwortet immer gleich und verschickt im Hintergrund – so lässt sich nicht herausfinden, wer ein Konto hat."""
    throttle("pwreset-ip:" + (request.client.host if request.client else "?"), limit=10, window=3600)
    ident = r.identifier.strip()
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE username = ? OR email = ?", (ident, ident.lower())).fetchone()
    if u and u["email"] and mail_ready():
        try:
            throttle(f"pwreset-user:{u['id']}", limit=3, window=3600)
        except HTTPException:
            return {"ok": True}
        base = public_base(request)
        link = f"{base}/?reset={new_mail_token(u['id'], 'reset', RESET_HOURS)}"
        small = (f"Der Link ist {RESET_HOURS} Stunde gültig und nur einmal nutzbar. Wenn du das nicht warst, "
                 f"kannst du diese Mail ignorieren – dein Passwort bleibt unverändert.")
        tasks.add_task(send_quietly, u["email"], "Bissfest: Passwort zurücksetzen",
                       f"Hallo {u['username']},\n\nüber diesen Link kannst du ein neues Passwort für Bissfest festlegen:\n"
                       f"{link}\n\n{small}\n",
                       mail_html(base, [f"Hallo {u['username']},", "du kannst jetzt ein neues Passwort für Bissfest festlegen:"],
                                 "Neues Passwort festlegen", link, small))
    return {"ok": True}


class ResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=8, max_length=200)


@app.post("/api/password-reset/confirm")
def password_reset_confirm(c: ResetConfirm, response: Response):
    row = take_mail_token(c.token, "reset")
    if not row:
        raise HTTPException(400, "Der Link ist abgelaufen oder wurde schon benutzt. Bitte fordere einen neuen an.")
    salt = secrets.token_hex(16)
    with db() as conn:
        # Wer die Mail bekommt, hat die Adresse damit auch bestätigt; alle bisherigen Anmeldungen enden
        conn.execute("UPDATE users SET pw_hash=?, salt=?, pw_iterations=?, email_verified=1 WHERE id=?",
                     (hash_pw(c.password, salt), salt, PW_ITERATIONS, row["user_id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
    start_session(response, row["user_id"])
    return {"ok": True}


class EmailChange(BaseModel):
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1, max_length=200)


@app.post("/api/email-change")
def email_change(c: EmailChange, request: Request, u=Depends(current_user)):
    """Neue Adresse gilt erst nach Klick auf den Link, der an sie geschickt wird."""
    throttle(f"emailchange:{u['id']}", limit=5, window=3600)
    if not hmac.compare_digest(u["pw_hash"], hash_pw(c.password, u["salt"], pw_iter(u))):
        raise HTTPException(400, "Das Passwort ist falsch")
    if not mail_ready():
        raise HTTPException(503, "Der E-Mail-Versand ist auf diesem Server nicht eingerichtet")
    email = c.email.strip().lower()
    if email == (u["email"] or ""):
        raise HTTPException(400, "Das ist bereits deine E-Mail-Adresse")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Für diese E-Mail-Adresse gibt es schon ein Konto")
    base = public_base(request)
    link = f"{base}/verify-email-change?token={new_mail_token(u['id'], 'email', VERIFY_HOURS, email)}"
    small = (f"Der Link ist {VERIFY_HOURS} Stunden gültig. Wenn du das nicht warst, kannst du diese Mail ignorieren.")
    try:
        mailer.send(email, "Bissfest: Neue E-Mail-Adresse bestätigen",
                    f"Hallo {u['username']},\n\nbitte bestätige deine neue E-Mail-Adresse für Bissfest:\n{link}\n\n{small}\n",
                    mail_html(base, [f"Hallo {u['username']},", "bitte bestätige deine neue E-Mail-Adresse für Bissfest:"],
                              "Neue Adresse bestätigen", link, small))
    except Exception as e:
        logging.getLogger("mail").error("Bestätigungsmail an %s fehlgeschlagen: %s", email, e)
        raise HTTPException(502, "Die Mail konnte nicht verschickt werden. Bitte später erneut versuchen.")
    return {"ok": True, "email": email}


@app.get("/verify-email-change")
def verify_email_change(request: Request, tasks: BackgroundTasks, token: str = ""):
    row = take_mail_token(token, "email")
    if not row:
        return RedirectResponse("/?email_changed=0", status_code=303)
    email = row["payload"]
    with db() as conn:
        old = conn.execute("SELECT username, email FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if conn.execute("SELECT 1 FROM users WHERE email=? AND id!=?", (email, row["user_id"])).fetchone():
            return RedirectResponse("/?email_changed=0", status_code=303)  # inzwischen anderweitig vergeben
        conn.execute("UPDATE users SET email=?, email_verified=1 WHERE id=?", (email, row["user_id"]))
    if old["email"] and old["email"] != email:  # Sicherheitshinweis an die bisherige Adresse
        base = public_base(request)
        text = (f"die E-Mail-Adresse deines Bissfest-Kontos „{old['username']}“ wurde geändert. "
                f"Wenn du das nicht warst, antworte bitte auf diese Mail oder wende dich an den Betreiber.")
        tasks.add_task(send_quietly, old["email"], "Bissfest: Deine E-Mail-Adresse wurde geändert",
                       f"Hallo,\n\n{text}\n", mail_html(base, ["Hallo,", text]))
    return RedirectResponse("/?email_changed=1", status_code=303)


# ---------------------------------------------------------------- Profilbild
# Der Browser schneidet das Bild quadratisch zu und verkleinert es; abrufbar nur für den Nutzer selbst.

AVATAR_MAX_BYTES = 500 * 1024


def avatar_path(user_id: int) -> Path:
    return Path(DB_PATH).parent / "avatars" / f"{user_id}.jpg"


class AvatarIn(BaseModel):
    image: str = Field(max_length=800_000)  # data:image/jpeg;base64,…


@app.put("/api/avatar")
def save_avatar(a: AvatarIn, u=Depends(current_user)):
    throttle(f"avatar:{u['id']}", limit=20, window=3600)
    try:
        raw = base64.b64decode(a.image.split(",", 1)[1], validate=True)
    except Exception:
        raise HTTPException(422, "Bild konnte nicht gelesen werden")
    if len(raw) > AVATAR_MAX_BYTES:
        raise HTTPException(422, "Bild ist zu groß")
    if not raw.startswith(b"\xff\xd8\xff"):
        raise HTTPException(422, "Bild muss ein JPEG sein")
    path = avatar_path(u["id"])
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(raw)
    version = int(time.time())
    with db() as conn:
        conn.execute("UPDATE users SET avatar_version=? WHERE id=?", (version, u["id"]))
    return {"avatar_version": version}


@app.get("/api/avatar")
def get_avatar(u=Depends(current_user)):
    path = avatar_path(u["id"])
    if not path.is_file():
        raise HTTPException(404, "Kein Profilbild vorhanden")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@app.delete("/api/avatar")
def delete_avatar(u=Depends(current_user)):
    avatar_path(u["id"]).unlink(missing_ok=True)
    with db() as conn:
        conn.execute("UPDATE users SET avatar_version=0 WHERE id=?", (u["id"],))
    return {"ok": True}


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=200)


@app.put("/api/password")
def change_password(p: PasswordChange, request: Request, u=Depends(current_user)):
    if not hmac.compare_digest(u["pw_hash"], hash_pw(p.old_password, u["salt"], pw_iter(u))):
        raise HTTPException(400, "Altes Passwort ist falsch")
    salt = secrets.token_hex(16)
    with db() as conn:
        conn.execute("UPDATE users SET pw_hash=?, salt=?, pw_iterations=? WHERE id=?",
                     (hash_pw(p.new_password, salt), salt, PW_ITERATIONS, u["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=? AND token != ?",
                     (u["id"], session_hash(request.cookies.get("session") or "")))
    return {"ok": True}


# ---------------------------------------------------------------- Tagesdaten

def check_day(day: str) -> str:
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError:
        raise HTTPException(400, "Ungültiges Datum")


NUTRIENTS = ("protein", "carbs", "sugar", "fat")  # je 100 g gespeichert als <name>_100g


def entry_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    factor = d["grams"] / 100
    d["kcal"] = int(d["kcal_100g"] * factor + 0.5)  # kaufmännisch runden wie in der App-Vorschau
    for k in NUTRIENTS:
        v = d.pop(f"{k}_100g", None)
        d[k] = round(v * factor, 1) if v is not None else None
    d.pop("user_id", None)
    d.pop("template_id", None)
    d["kcal_100g"] = r["kcal_100g"]
    return d


@app.get("/api/day/{day}")
def get_day(day: str, u=Depends(current_user)):
    day = check_day(day)
    with db() as conn:
        foods = [entry_dict(r) for r in conn.execute(
            "SELECT * FROM food_entries WHERE user_id=? AND day=? ORDER BY meal, created", (u["id"], day))]
        water = conn.execute(
            "SELECT COALESCE(SUM(ml),0) AS ml, COUNT(*) AS n FROM water_entries WHERE user_id=? AND day=?",
            (u["id"], day)).fetchone()
        w = conn.execute("SELECT kg FROM weights WHERE user_id=? AND day=?", (u["id"], day)).fetchone()
        coffee, tea = drink_day(conn, u["id"], day, "coffee"), drink_day(conn, u["id"], day, "tea")
        sport = sport_day(conn, u, day)
    totals = {k: round(sum((f[k] or 0) for f in foods), 1) for k in ("kcal", *NUTRIENTS)}
    return {"day": day, "foods": foods, "water_ml": water["ml"], "water_count": water["n"],
            "weight": w["kg"] if w else None, "coffee": coffee, "tea": tea, "sport": sport, "totals": totals}


class FoodIn(BaseModel):
    day: str
    meal: int = Field(ge=0, le=6)
    name: str = Field(min_length=1, max_length=200)
    brand: str = Field(default="", max_length=200)
    grams: float = Field(gt=0, le=5000)
    kcal_100g: float = Field(ge=0, le=1000)
    protein_100g: float | None = Field(default=None, ge=0, le=100)
    carbs_100g: float | None = Field(default=None, ge=0, le=100)
    sugar_100g: float | None = Field(default=None, ge=0, le=100)
    fat_100g: float | None = Field(default=None, ge=0, le=100)
    source: str = Field(default="manuell", max_length=40)
    code: str = Field(default="", max_length=60)
    portion_name: str | None = Field(default=None, max_length=40)
    portion_count: float | None = Field(default=None, gt=0, le=100)


@app.post("/api/food")
def add_food(f: FoodIn, u=Depends(current_user)):
    day = check_day(f.day)
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO food_entries (user_id, day, meal, name, brand, grams, kcal_100g, protein_100g,
               carbs_100g, sugar_100g, fat_100g, source, code, portion_name, portion_count, created)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (u["id"], day, f.meal, f.name.strip(), f.brand.strip(), f.grams, f.kcal_100g, f.protein_100g,
             f.carbs_100g, f.sugar_100g, f.fat_100g, f.source, f.code, f.portion_name,
             f.portion_count if f.portion_name else None, int(time.time())),
        )
    return {"id": cur.lastrowid}


class FoodPatch(BaseModel):
    grams: float = Field(gt=0, le=5000)
    meal: int | None = Field(default=None, ge=0, le=6)
    portion_name: str | None = Field(default=None, max_length=40)
    portion_count: float | None = Field(default=None, gt=0, le=100)


@app.patch("/api/food/{entry_id}")
def edit_food(entry_id: int, p: FoodPatch, u=Depends(current_user)):
    with db() as conn:
        cur = conn.execute(
            "UPDATE food_entries SET grams=?, meal=COALESCE(?, meal), portion_name=?, portion_count=? "
            "WHERE id=? AND user_id=?",
            (p.grams, p.meal, p.portion_name, p.portion_count if p.portion_name else None, entry_id, u["id"]))
    if not cur.rowcount:
        raise HTTPException(404, "Eintrag nicht gefunden")
    return {"ok": True}


@app.delete("/api/food/{entry_id}")
def delete_food(entry_id: int, u=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM food_entries WHERE id=? AND user_id=?", (entry_id, u["id"]))
    return {"ok": True}


@app.get("/api/foods/recent")
def recent_foods(u=Depends(current_user)):
    """Zuletzt gegessene Lebensmittel (eindeutig nach Name+Marke) für schnelles Wiederholen."""
    with db() as conn:
        rows = conn.execute(
            """SELECT name, brand, kcal_100g, protein_100g, carbs_100g, sugar_100g, fat_100g, source, code,
                      portion_name, portion_count,
                      grams, MAX(created) AS last
               FROM food_entries WHERE user_id=? GROUP BY name, brand ORDER BY last DESC LIMIT 30""",
            (u["id"],)).fetchall()
    return [dict(r) for r in rows]


FAVORITES_MAX = 20


class PortionHint(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    grams: float = Field(gt=0, le=5000)


class FavoriteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str = Field(default="", max_length=250)
    kcal_100g: float = Field(ge=0, le=1000)
    protein_100g: float | None = Field(default=None, ge=0, le=100)
    carbs_100g: float | None = Field(default=None, ge=0, le=100)
    sugar_100g: float | None = Field(default=None, ge=0, le=100)
    fat_100g: float | None = Field(default=None, ge=0, le=100)
    source: str = Field(default="manuell", max_length=40)
    code: str = Field(default="", max_length=60)
    serving_g: float | None = Field(default=None, ge=0, le=5000)
    source_portions: list[PortionHint] = Field(default_factory=list, max_length=10)
    custom_id: int | None = None


def favorite_key(name: str, brand: str | None) -> str:
    """Gleiche Regel wie favoriteKey() in app.js und wie "Zuletzt" (Name + Marke)."""
    return f"{name.strip().lower()}|{(brand or '').strip().lower()}"


@app.get("/api/favorites")
def list_favorites(u=Depends(current_user)):
    with db() as conn:
        rows = conn.execute("SELECT id, key, data FROM favorite_foods WHERE user_id=? ORDER BY key",
                            (u["id"],)).fetchall()
        out = []
        for r in rows:
            food = json.loads(r["data"])
            if food.get("custom_id"):
                # Eigene Lebensmittel mit aktuellen Werten, falls sie inzwischen bearbeitet wurden
                custom = conn.execute("SELECT * FROM custom_foods WHERE id=?", (food["custom_id"],)).fetchone()
                food = custom_food_dict(custom, u["id"]) if custom else {**food, "custom_id": None}
            out.append({**food, "favorite_id": r["id"], "key": r["key"]})
    return out


@app.post("/api/favorites")
def add_favorite(f: FavoriteIn, u=Depends(current_user)):
    key = favorite_key(f.name, f.brand)
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM favorite_foods WHERE user_id=? AND key=?", (u["id"], key)).fetchone()
        if not exists and conn.execute("SELECT COUNT(*) FROM favorite_foods WHERE user_id=?",
                                       (u["id"],)).fetchone()[0] >= FAVORITES_MAX:
            raise HTTPException(400, f"Höchstens {FAVORITES_MAX} Favoriten, bitte erst einen entfernen")
        conn.execute("INSERT INTO favorite_foods (user_id, key, data, created) VALUES (?,?,?,?) "
                     "ON CONFLICT (user_id, key) DO UPDATE SET data=excluded.data",
                     (u["id"], key, json.dumps(f.model_dump(), ensure_ascii=False), int(time.time())))
    return list_favorites(u)


@app.delete("/api/favorites/{favorite_id}")
def delete_favorite(favorite_id: int, u=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM favorite_foods WHERE id=? AND user_id=?", (favorite_id, u["id"]))
    return list_favorites(u)


class WaterIn(BaseModel):
    day: str
    ml: int = Field(ge=-3000, le=3000)


@app.post("/api/water")
def add_water(wi: WaterIn, u=Depends(current_user)):
    day = check_day(wi.day)
    with db() as conn:
        if wi.ml < 0:  # negativer Wert = letzte Eingabe(n) korrigieren, nie unter 0
            total = conn.execute("SELECT COALESCE(SUM(ml),0) FROM water_entries WHERE user_id=? AND day=?",
                                 (u["id"], day)).fetchone()[0]
            ml = -min(total, -wi.ml)
        else:
            ml = wi.ml
        if ml:
            conn.execute("INSERT INTO water_entries (user_id, day, ml, created) VALUES (?,?,?,?)",
                         (u["id"], day, ml, int(time.time())))
        total = conn.execute("SELECT COALESCE(SUM(ml),0) FROM water_entries WHERE user_id=? AND day=?",
                             (u["id"], day)).fetchone()[0]
    return {"water_ml": total}


def weight_on_day(conn: sqlite3.Connection, user_id: int, day: str) -> float | None:
    r = conn.execute("SELECT kg FROM weights WHERE user_id=? AND day<=? ORDER BY day DESC LIMIT 1", (user_id, day)).fetchone()
    return r["kg"] if r else None


def sport_day(conn: sqlite3.Connection, u: sqlite3.Row, day: str) -> dict:
    entries = [dict(r) for r in conn.execute(
        "SELECT id, sport, name, minutes, kcal FROM sport_entries WHERE user_id=? AND day=? ORDER BY created, id", (u["id"], day))]
    # Ist die Sport-Sektion aus, bleiben die Einträge erhalten, zählen aber nicht zum Tagesziel
    return {"entries": entries, "kcal": sum(e["kcal"] for e in entries) if u["sport_enabled"] else 0,
            "weight_kg": weight_on_day(conn, u["id"], day)}


@app.get("/api/sports")
def list_sports(u=Depends(current_user)):
    return [{"key": key, "icon": icon, "name": name, "met": met} for key, icon, name, met in sports.SPORTS]


class SportIn(BaseModel):
    day: str
    sport: str = Field(max_length=30)
    minutes: int = Field(ge=1, le=600)
    name: str | None = Field(default=None, max_length=60)  # nur bei eigener Sportart
    kcal: int | None = Field(default=None, ge=1, le=3000)  # nur bei eigener Sportart, z. B. von der Sportuhr


@app.post("/api/sport")
def add_sport(s: SportIn, u=Depends(current_user)):
    day = check_day(s.day)
    with db() as conn:
        if s.sport == sports.CUSTOM:
            name = (s.name or "").strip()
            if not name or not s.kcal:
                raise HTTPException(400, "Bitte Sportart und Kalorien eingeben")
            kcal = s.kcal
        elif s.sport in sports.BY_KEY:
            _, name, met = sports.BY_KEY[s.sport]
            kg = weight_on_day(conn, u["id"], day)
            if not kg:
                raise HTTPException(400, "Trag zuerst dein Gewicht ein, damit Bissfest die Kalorien berechnen kann")
            kcal = sports.earned_kcal(met, kg, s.minutes)
        else:
            raise HTTPException(400, "Unbekannte Sportart")
        conn.execute("INSERT INTO sport_entries (user_id, day, sport, name, minutes, kcal, created) VALUES (?,?,?,?,?,?,?)",
                     (u["id"], day, s.sport, name, s.minutes, kcal, int(time.time())))
        return sport_day(conn, u, day)


@app.delete("/api/sport/{entry_id}")
def delete_sport(entry_id: int, u=Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT day FROM sport_entries WHERE id=? AND user_id=?", (entry_id, u["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Eintrag nicht gefunden")
        conn.execute("DELETE FROM sport_entries WHERE id=?", (entry_id,))
        return sport_day(conn, u, row["day"])


DrinkKind = Literal["coffee", "tea"]


def drink_day(conn: sqlite3.Connection, user_id: int, day: str, kind: str) -> dict:
    r = conn.execute(
        "SELECT COALESCE(SUM(ml = ?), 0) AS cups, COALESCE(SUM(ml = ?), 0) AS mugs, COALESCE(SUM(ml = ?), 0) AS espressos, "
        "COALESCE(SUM(ml), 0) AS ml FROM coffee_entries WHERE user_id=? AND day=? AND kind=?",
        (COFFEE_SIZES["cup"], COFFEE_SIZES["mug"], COFFEE_SIZES["espresso"], user_id, day, kind)).fetchone()
    return dict(r)


def drink_caffeine(c: dict, cup_mg: int, espresso_mg: int) -> float:
    """Espresso ist viel konzentrierter als Filterkaffee und zählt deshalb pro Stück, nicht nach Menge."""
    filter_ml = c["ml"] - c["espressos"] * COFFEE_SIZES["espresso"]
    return filter_ml / COFFEE_SIZES["cup"] * cup_mg + c["espressos"] * espresso_mg


class DrinkIn(BaseModel):
    day: str
    size: Literal["cup", "mug", "espresso"] = "cup"
    kind: DrinkKind = "coffee"


@app.post("/api/coffee")
def add_drink(c: DrinkIn, u=Depends(current_user)):
    """Tasse (150 ml) oder Pott (250 ml) Kaffee bzw. Tee eintragen, beim Kaffee auch Espresso (30 ml)."""
    day = check_day(c.day)
    if c.size == "espresso" and c.kind != "coffee":
        raise HTTPException(400, "Espresso gibt es nur beim Kaffee")
    with db() as conn:
        conn.execute("INSERT INTO coffee_entries (user_id, day, ml, kind, created) VALUES (?,?,?,?,?)",
                     (u["id"], day, COFFEE_SIZES[c.size], c.kind, int(time.time())))
        return drink_day(conn, u["id"], day, c.kind)


@app.delete("/api/coffee/last")
def undo_drink(day: str, kind: DrinkKind = "coffee", u=Depends(current_user)):
    """Den zuletzt eingetragenen Kaffee bzw. Tee des Tages zurücknehmen."""
    day = check_day(day)
    with db() as conn:
        conn.execute("DELETE FROM coffee_entries WHERE id = (SELECT id FROM coffee_entries "
                     "WHERE user_id=? AND day=? AND kind=? ORDER BY created DESC, id DESC LIMIT 1)",
                     (u["id"], day, kind))
        return drink_day(conn, u["id"], day, kind)


class WeightIn(BaseModel):
    kg: float | None = Field(default=None, ge=20, le=400)


@app.put("/api/weight/{day}")
def set_weight(day: str, wi: WeightIn, u=Depends(current_user)):
    day = check_day(day)
    with db() as conn:
        if wi.kg is None:
            conn.execute("DELETE FROM weights WHERE user_id=? AND day=?", (u["id"], day))
        else:
            conn.execute("INSERT INTO weights (user_id, day, kg) VALUES (?,?,?) "
                         "ON CONFLICT(user_id, day) DO UPDATE SET kg=excluded.kg", (u["id"], day, wi.kg))
    return {"ok": True}


@app.get("/api/trophies")
def get_trophies(u=Depends(current_user)):
    with db() as conn:
        return trophies.evaluate(conn, u)


@app.get("/api/history")
def history(days: int = 30, u=Depends(current_user)):
    days = max(7, min(days, 730))
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with db() as conn:
        weights = conn.execute("SELECT day, kg FROM weights WHERE user_id=? AND day>=? ORDER BY day",
                               (u["id"], start)).fetchall()
        kcal = conn.execute(
            "SELECT day, ROUND(SUM(kcal_100g*grams/100)) AS kcal FROM food_entries WHERE user_id=? AND day>=? "
            "GROUP BY day ORDER BY day", (u["id"], start)).fetchall()
        water = conn.execute("SELECT day, SUM(ml) AS ml FROM water_entries WHERE user_id=? AND day>=? "
                             "GROUP BY day ORDER BY day", (u["id"], start)).fetchall()
        drinks = {kind: [dict(r) for r in conn.execute(
            "SELECT day, SUM(ml) AS ml, SUM(ml = ?) AS espressos FROM coffee_entries "
            "WHERE user_id=? AND day>=? AND kind=? GROUP BY day ORDER BY day",
            (COFFEE_SIZES["espresso"], u["id"], start, kind))] for kind in ("coffee", "tea")}
        sport = conn.execute("SELECT day, SUM(kcal) AS kcal FROM sport_entries WHERE user_id=? AND day>=? GROUP BY day",
                             (u["id"], start)).fetchall() if u["sport_enabled"] else []
    return {"weights": [dict(r) for r in weights], "kcal": [dict(r) for r in kcal], "water": [dict(r) for r in water],
            "sport": [dict(r) for r in sport], **drinks}


# ---------------------------------------------------------------- Lebensmittel-Datenbanken

def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _off_product(p: dict) -> dict | None:
    n = p.get("nutriments") or {}
    kcal = _num(n.get("energy-kcal_100g"))
    if kcal is None and _num(n.get("energy_100g")) is not None:
        kcal = round(_num(n.get("energy_100g")) / 4.184, 1)  # kJ -> kcal
    name = p.get("product_name_de") or p.get("product_name") or ""
    if kcal is None or not name:
        return None
    brands = p.get("brands") or ""
    if isinstance(brands, str):
        brands = brands.split(",")
    brands = ", ".join(b.strip() for b in brands[:2] if b.strip())[:60]  # OFF-Marken sind teils sehr lang
    return {
        "name": name.strip(), "brand": brands.strip(), "kcal_100g": kcal,
        "protein_100g": _num(n.get("proteins_100g")), "carbs_100g": _num(n.get("carbohydrates_100g")),
        "sugar_100g": _num(n.get("sugars_100g")), "fat_100g": _num(n.get("fat_100g")), "source": "Open Food Facts", "code": str(p.get("code") or ""),
        "serving_g": _num(p.get("serving_quantity")),
        "source_portions": _off_portion(p),
    }


def _off_portion(p: dict) -> list[dict]:
    """Packungsportion aus Open Food Facts, z. B. serving_size "1 Scheibe (25 g)" -> Scheibe = 25 g."""
    grams = _num(p.get("serving_quantity"))
    if not grams or (p.get("serving_quantity_unit") or "g") not in ("g", "ml"):
        return []
    label = re.sub(r"\(.*?\)", "", p.get("serving_size") or "").strip()
    label = re.sub(r"^\d+([.,]\d+)?\s*", "", label).strip()       # führende Anzahl entfernen
    if not label or re.fullmatch(r"(g|ml|gramm|gr)\.?", label, re.I):
        label = "Portion"
    return [{"name": label[:30].capitalize(), "grams": grams}]


async def search_off(client: httpx.AsyncClient, q: str) -> list[dict]:
    fields = "code,product_name,product_name_de,brands,nutriments,serving_quantity,serving_quantity_unit,serving_size"
    try:  # neue Such-API (search-a-licious)
        r = await client.get("https://search.openfoodfacts.org/search",
                             params={"q": q, "page_size": 25, "fields": fields, "langs": "de,en"})
        r.raise_for_status()
        hits = r.json().get("hits", [])
    except Exception:
        r = await client.get(f"https://{OFF_COUNTRY}.openfoodfacts.org/cgi/search.pl",
                             params={"search_terms": q, "json": 1, "page_size": 25, "fields": fields})
        r.raise_for_status()
        hits = r.json().get("products", [])
    return [x for x in (_off_product(p) for p in hits) if x]


FDC_NUTRIENTS = {"208": "kcal_100g", "203": "protein_100g", "205": "carbs_100g", "269": "sugar_100g", "204": "fat_100g"}


async def search_fdc(client: httpx.AsyncClient, q: str) -> list[dict]:
    if not FDC_API_KEY:
        return []
    # POST statt GET: Die Klammern in "Survey (FNDDS)" führen als URL-Parameter immer wieder zu 400 Bad Request
    r = await client.post("https://api.nal.usda.gov/fdc/v1/foods/search", params={"api_key": FDC_API_KEY},
                          json={"query": q, "pageSize": 15, "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)"]})
    r.raise_for_status()
    out = []
    for f in r.json().get("foods", []):
        item = {"name": f.get("description", "").capitalize(), "brand": "", "source": "USDA",
                "code": str(f.get("fdcId", "")), "serving_g": None,
                "kcal_100g": None, "protein_100g": None, "carbs_100g": None, "sugar_100g": None, "fat_100g": None}
        for nut in f.get("foodNutrients", []):
            key = FDC_NUTRIENTS.get(str(nut.get("nutrientNumber")))
            if key and (key != "kcal_100g" or nut.get("unitName", "").upper() == "KCAL"):
                item[key] = _num(nut.get("value"))
        item["source_portions"] = _fdc_portions(f.get("foodMeasures") or [])
        if item["kcal_100g"] is not None:
            out.append(item)
    return out


FDC_UNITS = [(r"\bslices?\b", "Scheibe"), (r"\bcups?\b", "Tasse"), (r"\b(tbsp|tablespoons?)\b", "EL"),
             (r"\b(tsp|teaspoons?)\b", "TL"), (r"\b(pieces?|each)\b", "Stück"), (r"\bmedium\b", "Stück (mittel)"),
             (r"\bsmall\b", "Stück (klein)"), (r"\blarge\b", "Stück (groß)"), (r"\bservings?\b", "Portion")]


def _fdc_portions(measures: list[dict]) -> list[dict]:
    """USDA-Haushaltsmaße ("1 slice", 25 g) -> deutsche Portionsnamen, nur Angaben für genau 1 Einheit."""
    out, seen = [], set()
    for m in measures:
        text, grams = (m.get("disseminationText") or "").lower(), _num(m.get("gramWeight"))
        if not grams or "not specified" in text or not re.match(r"^1\s", text):
            continue
        name = next((de for pat, de in FDC_UNITS if re.search(pat, text)), None)
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "grams": grams})
    return out[:4]


@app.get("/api/foods/search")
async def food_search(q: str, u=Depends(current_user)):
    q = q.strip()
    if len(q) < 2:
        return {"results": [], "errors": []}
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": USER_AGENT}) as client:
        res = await asyncio.gather(search_off(client, q), search_fdc(client, q), return_exceptions=True)
    results, errors = search_custom(q, u["id"]), []
    for name, r in zip(("Open Food Facts", "USDA"), res):
        if isinstance(r, httpx.HTTPStatusError) and r.response.status_code == 429:
            errors.append(f"{name}: Abfragelimit erreicht")
        elif isinstance(r, Exception):
            errors.append(f"{name} nicht erreichbar")
        else:
            results.extend(r)
    return {"results": results, "errors": errors}


@app.get("/api/foods/barcode/{code}")
async def food_barcode(code: str, u=Depends(current_user)):
    if not re.fullmatch(r"\d{6,14}", code):
        raise HTTPException(400, "Ungültiger Barcode")
    with db() as conn:
        own = conn.execute("SELECT * FROM custom_foods WHERE code=? ORDER BY id DESC LIMIT 1", (code,)).fetchone()
    if own:
        return custom_food_dict(own, u["id"])
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": USER_AGENT}) as client:
        try:
            r = await client.get(f"https://world.openfoodfacts.org/api/v2/product/{code}.json",
                                 params={"fields": "code,product_name,product_name_de,brands,nutriments,"
                                                   "serving_quantity,serving_quantity_unit,serving_size"})
        except httpx.HTTPError:
            raise HTTPException(502, "Open Food Facts nicht erreichbar")
    data = r.json() if r.status_code in (200, 404) else {}
    prod = _off_product({**(data.get("product") or {}), "code": code}) if data.get("product") else None
    if not prod:
        raise HTTPException(404, "Produkt nicht gefunden oder ohne Kalorienangabe")
    return prod


# ---------------------------------------------------------------- Gemeinsame Lebensmittel-Datenbank

def custom_food_dict(r: sqlite3.Row, user_id: int) -> dict:
    return {
        "custom_id": r["id"], "mine": r["created_by"] == user_id,
        "name": r["name"], "manufacturer": r["manufacturer"], "brand_name": r["brand"],
        "brand": " · ".join(dict.fromkeys(x for x in (r["brand"], r["manufacturer"]) if x)),  # gleiche Angabe nur einmal
        "kcal_100g": r["kcal_100g"], "protein_100g": r["protein_100g"], "carbs_100g": r["carbs_100g"],
        "sugar_100g": r["sugar_100g"], "fat_100g": r["fat_100g"],
        # ohne Barcode dient die eigene ID als Code – so bleiben Portionen auch bei Namensänderungen zugeordnet
        "source": "Bissfest-DB", "code": r["code"] or f"custom:{r['id']}", "serving_g": None,
    }


def search_custom(q: str, user_id: int) -> list[dict]:
    words = q.lower().split()[:5]
    where = " AND ".join(["LOWER(name || ' ' || manufacturer || ' ' || brand) LIKE ?"] * len(words))
    with db() as conn:
        rows = conn.execute(f"SELECT * FROM custom_foods WHERE {where} ORDER BY name LIMIT 20",
                            [f"%{w}%" for w in words]).fetchall()
    return [custom_food_dict(r, user_id) for r in rows]


class CustomFoodIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    manufacturer: str = Field(default="", max_length=120)
    brand: str = Field(default="", max_length=120)
    code: str = Field(default="", pattern=r"^(\d{6,14})?$")
    kcal_100g: float = Field(ge=0, le=1000)
    protein_100g: float | None = Field(default=None, ge=0, le=100)
    carbs_100g: float | None = Field(default=None, ge=0, le=100)
    sugar_100g: float | None = Field(default=None, ge=0, le=100)
    fat_100g: float | None = Field(default=None, ge=0, le=100)


@app.post("/api/foods/custom")
def create_custom_food(f: CustomFoodIn, u=Depends(current_user)):
    if f.sugar_100g is not None and f.carbs_100g is not None and f.sugar_100g > f.carbs_100g:
        raise HTTPException(400, "Zucker kann nicht mehr sein als Kohlenhydrate")
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO custom_foods (created_by, name, manufacturer, brand, code, kcal_100g, protein_100g,
               carbs_100g, sugar_100g, fat_100g, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (u["id"], f.name.strip(), f.manufacturer.strip(), f.brand.strip(), f.code, f.kcal_100g,
             f.protein_100g, f.carbs_100g, f.sugar_100g, f.fat_100g, int(time.time())))
        row = conn.execute("SELECT * FROM custom_foods WHERE id=?", (cur.lastrowid,)).fetchone()
    return custom_food_dict(row, u["id"])


TEMPLATE_FILENAME = "bissfest-naehrwertvorlage.xlsx"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/api/foods/template")
def food_template(u=Depends(current_user)):
    return Response(food_import.build_template(), media_type=XLSX_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILENAME}"'})


class FoodImportIn(BaseModel):
    data: str = Field(max_length=3_000_000)  # .xlsx als Base64
    commit: bool = False  # false = nur prüfen und Vorschau liefern


def food_identity(name: str, manufacturer: str, brand: str) -> tuple:
    return (name.strip().lower(), manufacturer.strip().lower(), brand.strip().lower())


@app.post("/api/foods/import")
def import_foods(i: FoodImportIn, u=Depends(current_user)):
    """Ausgefüllte Vorlage prüfen oder übernehmen. Vorhandene Lebensmittel werden nie überschrieben,
    ihnen fehlende Portionen aber ergänzt – so lässt sich ein vergessenes Gewicht nachtragen."""
    throttle(f"foodimport:{u['id']}", limit=30, window=3600)
    try:
        foods, errors = food_import.parse_workbook(base64.b64decode(i.data, validate=True))
    except food_import.ImportFileError as e:
        raise HTTPException(422, str(e))
    except ValueError:
        raise HTTPException(422, "Die Datei konnte nicht gelesen werden")

    ready, portions, skipped, now = [], [], [], int(time.time())

    def add_portion(conn, key, f):
        conn.execute(
            "INSERT INTO food_portions (food_key, name, grams, created_by, created) VALUES (?,?,?,?,?) "
            "ON CONFLICT(food_key, name) DO NOTHING",
            (key, f["portion_name"], f["portion_grams"], u["id"], now))

    with db() as conn:
        rows = conn.execute("SELECT id, name, manufacturer, brand, code FROM custom_foods").fetchall()
        # Vorhandene Lebensmittel mit ihrem Portionsschlüssel; None = kommt aus dieser Datei
        known = {food_identity(r["name"], r["manufacturer"], r["brand"]):
                 food_key("Bissfest-DB", r["code"] or f"custom:{r['id']}", r["name"], r["brand"]) for r in rows}
        codes = {r["code"]: r["code"] for r in rows if r["code"]}
        for f in foods:
            identity = food_identity(f["name"], f["manufacturer"], f["brand"])
            if identity in known or (f["code"] and f["code"] in codes):
                key = known.get(identity) or codes.get(f["code"])
                if key and f["portion_name"] and not conn.execute(
                        "SELECT 1 FROM food_portions WHERE food_key=? AND name=?", (key, f["portion_name"])).fetchone():
                    portions.append(f)
                    if i.commit:
                        add_portion(conn, key, f)
                else:
                    skipped.append({"row": f["row"], "name": f["name"]})
                continue
            known[identity] = None
            if f["code"]:
                codes[f["code"]] = None
            ready.append(f)
            if not i.commit:
                continue
            cur = conn.execute(
                """INSERT INTO custom_foods (created_by, name, manufacturer, brand, code, kcal_100g, protein_100g,
                   carbs_100g, sugar_100g, fat_100g, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (u["id"], f["name"], f["manufacturer"], f["brand"], f["code"], f["kcal_100g"], f["protein_100g"],
                 f["carbs_100g"], f["sugar_100g"], f["fat_100g"], now))
            if f["portion_name"]:
                add_portion(conn, food_key("Bissfest-DB", f["code"] or f"custom:{cur.lastrowid}", f["name"], f["brand"]), f)
    return {"ready": ready, "portions": portions, "errors": errors, "skipped": skipped,
            "imported": len(ready) if i.commit else 0, "portions_added": len(portions) if i.commit else 0}


@app.get("/api/foods/custom/resolve")
def resolve_custom_food(code: str, u=Depends(current_user)):
    """Lebensmittel zu einem Eintrag finden; Einträge kennen nur den Code ("custom:12" oder Barcode)."""
    with db() as conn:
        if code.startswith("custom:") and code[7:].isdigit():
            row = conn.execute("SELECT * FROM custom_foods WHERE id=?", (int(code[7:]),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM custom_foods WHERE code=? AND code != '' ORDER BY id DESC LIMIT 1", (code,)).fetchone()
    if not row:
        raise HTTPException(404, "Lebensmittel nicht gefunden")
    return custom_food_dict(row, u["id"])


@app.put("/api/foods/custom/{food_id}")
def update_custom_food(food_id: int, f: CustomFoodIn, u=Depends(current_user)):
    """Nur wer das Lebensmittel angelegt hat oder Admin ist. Eingetragene Mahlzeiten bleiben unverändert."""
    if f.sugar_100g is not None and f.carbs_100g is not None and f.sugar_100g > f.carbs_100g:
        raise HTTPException(400, "Zucker kann nicht mehr sein als Kohlenhydrate")
    with db() as conn:
        old = conn.execute("SELECT * FROM custom_foods WHERE id=?", (food_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Lebensmittel nicht gefunden")
        if old["created_by"] != u["id"] and not is_admin(u):
            raise HTTPException(403, "Nur die Person, die das Lebensmittel angelegt hat, kann es bearbeiten")
        conn.execute(
            """UPDATE custom_foods SET name=?, manufacturer=?, brand=?, code=?, kcal_100g=?, protein_100g=?,
               carbs_100g=?, sugar_100g=?, fat_100g=? WHERE id=?""",
            (f.name.strip(), f.manufacturer.strip(), f.brand.strip(), f.code, f.kcal_100g, f.protein_100g,
             f.carbs_100g, f.sugar_100g, f.fat_100g, food_id))
        old_key, new_key = old["code"] or f"custom:{food_id}", f.code or f"custom:{food_id}"
        if old_key != new_key:  # Portionen hängen am Code und müssen mitwandern
            conn.execute("UPDATE OR IGNORE food_portions SET food_key=? WHERE food_key=?", (new_key, old_key))
        row = conn.execute("SELECT * FROM custom_foods WHERE id=?", (food_id,)).fetchone()
    if old["created_by"] != u["id"]:
        logging.getLogger("admin").info("Lebensmittel #%s bearbeitet von %s", food_id, u["username"])
    return custom_food_dict(row, u["id"])


@app.delete("/api/foods/custom/{food_id}")
def delete_custom_food(food_id: int, u=Depends(current_user)):
    """Nur wer ein Lebensmittel angelegt hat, darf es aus der gemeinsamen Datenbank löschen."""
    with db() as conn:
        cur = conn.execute("DELETE FROM custom_foods WHERE id=? AND created_by=?", (food_id, u["id"]))
    if not cur.rowcount:
        raise HTTPException(403, "Nur die Person, die das Lebensmittel angelegt hat, kann es löschen")
    return {"ok": True}


# ---------------------------------------------------------------- Portionen ("1 Scheibe = 25 g")

# Typische Portionen als Vorschlag, solange für ein Lebensmittel noch keine gespeichert ist.
# (Muster auf den kleingeschriebenen Namen, Portion, Gramm) – die erste passende Regel gewinnt.
PORTION_SUGGESTIONS = [
    (r"saft|schorle|smoothie", "Glas", 200), (r"schokolade", "Stück", 5), (r"riegel", "Riegel", 25),
    (r"keks|cookie", "Stück", 10), (r"toast|sandwich", "Scheibe", 25), (r"brötchen|semmel|schrippe", "Stück", 60),
    (r"\bbrot\b|vollkornbrot|bauernbrot|roggenbrot", "Scheibe", 45), (r"knäcke", "Scheibe", 10),
    (r"\beier?\b", "Stück", 55), (r"apfel", "Stück", 180), (r"banane", "Stück", 120),
    (r"orange|apfelsine", "Stück", 150), (r"birne", "Stück", 170), (r"kiwi", "Stück", 75),
    (r"mandarine|clementine", "Stück", 60), (r"pfirsich|nektarine", "Stück", 140),
    (r"salami|wurst|aufschnitt", "Scheibe", 10), (r"schinken", "Scheibe", 15), (r"käse", "Scheibe", 25),
    (r"joghurt|yoghurt|skyr|quark", "Becher", 150), (r"milch", "Glas", 200),
    (r"kaffee|\btee\b", "Tasse", 150), (r"butter|margarine", "Portion", 10),
]


def food_key(source: str, code: str, name: str, brand: str) -> str:
    """Eindeutiger Schlüssel eines Lebensmittels: Barcode/ID, sonst Name + Marke."""
    if code:
        return f"usda:{code}" if source == "USDA" else code
    return f"n:{name.strip().lower()}|{brand.strip().lower()}"


@app.get("/api/portions")
def get_portions(name: str, brand: str = "", source: str = "", code: str = "", u=Depends(current_user)):
    key = food_key(source, code, name, brand)
    with db() as conn:
        saved = [{"id": r["id"], "name": r["name"], "grams": r["grams"], "mine": r["created_by"] == u["id"]}
                 for r in conn.execute("SELECT * FROM food_portions WHERE food_key=? ORDER BY grams", (key,))]
    lname = name.lower()
    suggested = [{"name": n, "grams": g} for pat, n, g in PORTION_SUGGESTIONS if re.search(pat, lname)][:1]
    spoons = food_import.spoon_grams(name)
    if spoons:
        suggested += [{"name": "TL", "grams": spoons[0]}, {"name": "EL", "grams": spoons[1]}]
    return {"saved": saved, "suggested": suggested}


class PortionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)  # Lebensmittel
    brand: str = Field(default="", max_length=200)
    source: str = Field(default="", max_length=40)
    code: str = Field(default="", max_length=60)
    portion_name: str = Field(min_length=1, max_length=30)
    grams: float = Field(gt=0, le=3000)


@app.post("/api/portions")
def save_portion(p: PortionIn, u=Depends(current_user)):
    """Portion für alle Nutzer speichern. Eine vorhandene Portion ändern nur Ersteller und Admin."""
    key, name = food_key(p.source, p.code, p.name, p.brand), p.portion_name.strip()
    with db() as conn:
        old = conn.execute("SELECT * FROM food_portions WHERE food_key=? AND name=?", (key, name)).fetchone()
        if not old:
            conn.execute("INSERT INTO food_portions (food_key, name, grams, created_by, created) VALUES (?,?,?,?,?)",
                         (key, name, p.grams, u["id"], int(time.time())))
        elif old["grams"] != p.grams:
            check_portion_owner(old, u)
            conn.execute("UPDATE food_portions SET grams=? WHERE id=?", (p.grams, old["id"]))
    return get_portions(p.name, p.brand, p.source, p.code, u)


def check_portion_owner(portion: sqlite3.Row, u: sqlite3.Row):
    if portion["created_by"] != u["id"] and not is_admin(u):
        raise HTTPException(403, f"„{portion['name']}“ gibt es schon mit {portion['grams']:g} g. "
                                 "Ändern kann sie nur, wer sie angelegt hat, oder der Admin.")


class PortionEdit(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    grams: float = Field(gt=0, le=3000)


@app.put("/api/portions/{portion_id}")
def update_portion(portion_id: int, p: PortionEdit, u=Depends(current_user)):
    try:
        with db() as conn:
            old = conn.execute("SELECT * FROM food_portions WHERE id=?", (portion_id,)).fetchone()
            if not old:
                raise HTTPException(404, "Portion nicht gefunden")
            check_portion_owner(old, u)
            conn.execute("UPDATE food_portions SET name=?, grams=? WHERE id=?", (p.name.strip(), p.grams, portion_id))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Dieses Lebensmittel hat schon eine Portion mit diesem Namen")
    return {"ok": True}


@app.delete("/api/portions/{portion_id}")
def delete_portion(portion_id: int, u=Depends(current_user)):
    with db() as conn:
        if is_admin(u):
            cur = conn.execute("DELETE FROM food_portions WHERE id=?", (portion_id,))
        else:
            cur = conn.execute("DELETE FROM food_portions WHERE id=? AND created_by=?", (portion_id, u["id"]))
    if not cur.rowcount:
        raise HTTPException(403, "Nur wer die Portion angelegt hat, kann sie löschen")
    return {"ok": True}


# ---------------------------------------------------------------- Gerichte (gespeicherte Mahlzeiten)

ITEM_COLS = ("name, brand, grams, kcal_100g, protein_100g, carbs_100g, sugar_100g, fat_100g, source, code, "
             "portion_name, portion_count")


def template_dict(conn: sqlite3.Connection, t: sqlite3.Row) -> dict:
    items = [entry_dict(r) for r in conn.execute(
        "SELECT * FROM meal_template_items WHERE template_id=? ORDER BY id", (t["id"],))]
    return {"id": t["id"], "name": t["name"], "items": items,
            "kcal": round(sum(i["kcal"] for i in items)),
            "grams": round(sum(i["grams"] for i in items))}


def own_template(conn: sqlite3.Connection, tpl_id: int, u: sqlite3.Row) -> sqlite3.Row:
    t = conn.execute("SELECT * FROM meal_templates WHERE id=? AND user_id=?", (tpl_id, u["id"])).fetchone()
    if not t:
        raise HTTPException(404, "Gericht nicht gefunden")
    return t


@app.get("/api/templates")
def list_templates(u=Depends(current_user)):
    with db() as conn:
        tpls = conn.execute("SELECT * FROM meal_templates WHERE user_id=? ORDER BY name COLLATE NOCASE",
                            (u["id"],)).fetchall()
        return [template_dict(conn, t) for t in tpls]


class TemplateItemEdit(BaseModel):
    id: int
    grams: float = Field(gt=0, le=5000)


class TemplateEdit(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    items: list[TemplateItemEdit] = Field(max_length=200)  # nicht aufgeführte Zutaten werden entfernt


@app.put("/api/templates/{tpl_id}")
def update_template(tpl_id: int, t: TemplateEdit, u=Depends(current_user)):
    """Bereits eingetragene Mahlzeiten bleiben unverändert."""
    if not t.name.strip():
        raise HTTPException(400, "Bitte einen Namen eingeben")
    if not t.items:
        raise HTTPException(400, "Ein Gericht braucht mindestens eine Zutat")
    with db() as conn:
        own_template(conn, tpl_id, u)
        old = {r["id"]: r for r in conn.execute("SELECT * FROM meal_template_items WHERE template_id=?", (tpl_id,))}
        keep = {i.id: i.grams for i in t.items if i.id in old}
        if not keep:
            raise HTTPException(400, "Ein Gericht braucht mindestens eine Zutat")
        conn.execute("UPDATE meal_templates SET name=? WHERE id=?", (t.name.strip(), tpl_id))
        for item_id, r in old.items():
            if item_id not in keep:
                conn.execute("DELETE FROM meal_template_items WHERE id=?", (item_id,))
                continue
            # Die Portion wächst mit: aus 2 Scheiben (50 g) werden bei 75 g drei Scheiben
            count = r["portion_count"] * keep[item_id] / r["grams"] if r["portion_count"] else None
            conn.execute("UPDATE meal_template_items SET grams=?, portion_count=? WHERE id=?", (keep[item_id], count, item_id))
        return template_dict(conn, own_template(conn, tpl_id, u))


class TemplateItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str = Field(default="", max_length=200)
    grams: float = Field(gt=0, le=5000)
    kcal_100g: float = Field(ge=0, le=1000)
    protein_100g: float | None = Field(default=None, ge=0, le=100)
    carbs_100g: float | None = Field(default=None, ge=0, le=100)
    sugar_100g: float | None = Field(default=None, ge=0, le=100)
    fat_100g: float | None = Field(default=None, ge=0, le=100)
    source: str = Field(default="manuell", max_length=40)
    code: str = Field(default="", max_length=60)
    portion_name: str | None = Field(default=None, max_length=40)
    portion_count: float | None = Field(default=None, gt=0, le=100)


@app.post("/api/templates/{tpl_id}/items")
def add_template_item(tpl_id: int, i: TemplateItemIn, u=Depends(current_user)):
    with db() as conn:
        t = own_template(conn, tpl_id, u)
        conn.execute(
            f"INSERT INTO meal_template_items (template_id, {ITEM_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tpl_id, i.name, i.brand, i.grams, i.kcal_100g, i.protein_100g, i.carbs_100g, i.sugar_100g, i.fat_100g,
             i.source, i.code, i.portion_name, i.portion_count if i.portion_name else None))
        return template_dict(conn, t)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    day: str
    meal: int = Field(ge=0, le=6)


@app.post("/api/templates")
def create_template(t: TemplateIn, u=Depends(current_user)):
    """Speichert alle Einträge einer Mahlzeit eines Tages als wiederverwendbares Gericht."""
    day = check_day(t.day)
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM food_entries WHERE user_id=? AND day=? AND meal=?",
                         (u["id"], day, t.meal)).fetchone()[0]
        if not n:
            raise HTTPException(400, "Diese Mahlzeit hat noch keine Einträge")
        cur = conn.execute("INSERT INTO meal_templates (user_id, name, created) VALUES (?,?,?)",
                           (u["id"], t.name.strip(), int(time.time())))
        conn.execute(f"INSERT INTO meal_template_items (template_id, {ITEM_COLS}) "
                     f"SELECT ?, {ITEM_COLS} FROM food_entries WHERE user_id=? AND day=? AND meal=? ORDER BY created, id",
                     (cur.lastrowid, u["id"], day, t.meal))
    return {"id": cur.lastrowid, "items": n}


class TemplateApply(BaseModel):
    day: str
    meal: int = Field(ge=0, le=6)
    factor: float = Field(default=1, gt=0, le=5)


@app.post("/api/templates/{tpl_id}/apply")
def apply_template(tpl_id: int, a: TemplateApply, u=Depends(current_user)):
    day = check_day(a.day)
    with db() as conn:
        if not conn.execute("SELECT 1 FROM meal_templates WHERE id=? AND user_id=?", (tpl_id, u["id"])).fetchone():
            raise HTTPException(404, "Gericht nicht gefunden")
        cur = conn.execute(
            f"INSERT INTO food_entries (user_id, day, meal, created, {ITEM_COLS}) "
            f"SELECT ?, ?, ?, ?, name, brand, ROUND(grams * ?, 1), kcal_100g, protein_100g, carbs_100g, sugar_100g, "
            f"fat_100g, source, code, portion_name, portion_count * ? FROM meal_template_items "
            f"WHERE template_id=? ORDER BY id",
            (u["id"], day, a.meal, int(time.time()), a.factor, a.factor, tpl_id))
    return {"added": cur.rowcount}


@app.delete("/api/templates/{tpl_id}")
def delete_template(tpl_id: int, u=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM meal_templates WHERE id=? AND user_id=?", (tpl_id, u["id"]))
    return {"ok": True}


# ---------------------------------------------------------------- API für Home Assistant (/api/v1)
# Doku: docs/home-assistant-api.md. Anmeldung per persönlichem Token (Authorization: Bearer bf_…),
# Tokens werden nur als SHA-256 gespeichert und in den Einstellungen verwaltet.

API_RATE_LIMIT = 60  # Anfragen pro Minute und Token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_user(request: Request) -> sqlite3.Row:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token.startswith("bf_"):
        raise HTTPException(401, "API-Token fehlt (Authorization: Bearer bf_…)")
    with db() as conn:
        row = conn.execute("SELECT t.id AS token_id, t.last_used, u.* FROM api_tokens t "
                           "JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?", (_token_hash(token),)).fetchone()
        if not row:
            raise HTTPException(401, "API-Token ungültig oder widerrufen")
        if not row["last_used"] or row["last_used"] < time.time() - 60:
            conn.execute("UPDATE api_tokens SET last_used=? WHERE id=?", (int(time.time()), row["token_id"]))
    throttle(f"api:{row['token_id']}", limit=API_RATE_LIMIT, window=60)
    return row


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@app.get("/api/tokens")
def list_tokens(u=Depends(current_user)):
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, prefix, created, last_used FROM api_tokens WHERE user_id=? ORDER BY created DESC",
            (u["id"],))]


@app.post("/api/tokens")
def create_token(t: TokenIn, u=Depends(current_user)):
    """Neuen Token erzeugen – der Klartext wird nur in dieser Antwort einmal zurückgegeben."""
    token = "bf_" + secrets.token_urlsafe(32)
    with db() as conn:
        if conn.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id=?", (u["id"],)).fetchone()[0] >= 10:
            raise HTTPException(400, "Höchstens 10 Tokens pro Konto – bitte erst einen widerrufen")
        tid = conn.execute("INSERT INTO api_tokens (user_id, name, token_hash, prefix, created) VALUES (?,?,?,?,?)",
                           (u["id"], t.name.strip(), _token_hash(token), token[:7], int(time.time()))).lastrowid
    return {"id": tid, "name": t.name.strip(), "token": token}


@app.delete("/api/tokens/{token_id}")
def revoke_token(token_id: int, u=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM api_tokens WHERE id=? AND user_id=?", (token_id, u["id"]))
    return {"ok": True}


def energy_estimate(u: sqlite3.Row, kg: float | None) -> int | None:
    """Gesamtumsatz nach Mifflin-St Jeor × Aktivität (gleiche Formel wie in der App, app.js energy())."""
    if not (u["sex"] and u["birth_year"] and u["height_cm"] and u["activity"] and kg):
        return None
    age = date.today().year - u["birth_year"]
    bmr = 10 * kg + 6.25 * u["height_cm"] - 5 * age + (5 if u["sex"] == "m" else -161)
    return round(bmr * u["activity"])


def build_status(u: sqlite3.Row, day: str) -> dict:
    d = get_day(day, u)
    with db() as conn:
        latest = conn.execute("SELECT day, kg FROM weights WHERE user_id=? AND day<=? ORDER BY day DESC LIMIT 1",
                              (u["id"], day)).fetchone()
        start30 = (date.fromisoformat(day) - timedelta(days=30)).isoformat()
        first30 = conn.execute("SELECT day, kg FROM weights WHERE user_id=? AND day>=? AND day<=? ORDER BY day LIMIT 1",
                               (u["id"], start30, day)).fetchone()
    if first30 and latest and first30["day"] == latest["day"]:
        first30 = None  # nur eine Wägung im Zeitraum – keine Veränderung berechenbar
    eaten = round(d["totals"]["kcal"])
    sport_kcal = d["sport"]["kcal"]
    burned = energy_estimate(u, latest["kg"] if latest else None)
    if burned is not None:
        burned += sport_kcal
    reminders = json.loads(u["reminders"] or "{}")
    meals = [{"index": i, "name": name,
              "kcal": round(sum(f["kcal"] for f in d["foods"] if f["meal"] == i)),
              "entries": sum(1 for f in d["foods"] if f["meal"] == i),
              "reminder": reminders.get(str(i))}
             for i, name in enumerate(MEAL_NAMES[:u["meals_per_day"]])]
    drinks = {}
    for kind, enabled, mg in (("coffee", u["coffee_enabled"], u["caffeine_mg"]), ("tea", u["tea_enabled"], u["tea_caffeine_mg"])):
        c = d[kind]
        drinks[kind] = {"enabled": bool(enabled), "cups": c["cups"], "mugs": c["mugs"], "espressos": c["espressos"], "ml": c["ml"],
                        "caffeine_mg": round(drink_caffeine(c, mg, u["espresso_caffeine_mg"]))}
    return {
        "app_version": APP_VERSION, "user": u["username"], "date": day,
        "calories": {"eaten": eaten, "goal": u["kcal_goal"], "sport": sport_kcal, "remaining": u["kcal_goal"] + sport_kcal - eaten,
                     "burned_estimate": burned, "deficit": burned - eaten if burned is not None else None},
        "nutrients": {k: d["totals"][k] for k in ("protein", "carbs", "sugar", "fat")},
        "meals": meals,
        "water": {"ml": d["water_ml"], "goal_ml": u["water_goal_ml"],
                  "percent": round(d["water_ml"] / u["water_goal_ml"] * 100) if u["water_goal_ml"] else 0},
        "weight": {"today_kg": d["weight"], "latest_kg": latest["kg"] if latest else None,
                   "latest_date": latest["day"] if latest else None, "target_kg": u["target_weight"],
                   "change_30d_kg": round(latest["kg"] - first30["kg"], 1) if latest and first30 else None},
        **drinks,
        "caffeine_total_mg": sum(v["caffeine_mg"] for v in drinks.values() if v["enabled"]),
    }


@app.get("/api/v1/status")
def api_status(date_: str | None = Query(default=None, alias="date"), u=Depends(token_user)):
    return build_status(u, check_day(date_) if date_ else date.today().isoformat())


class ApiWaterIn(BaseModel):
    ml: int = Field(ge=-3000, le=3000)


@app.post("/api/v1/water")
def api_water(w: ApiWaterIn, u=Depends(token_user)):
    day = date.today().isoformat()
    add_water(WaterIn(day=day, ml=w.ml), u)
    return build_status(u, day)


class ApiWeightIn(BaseModel):
    kg: float = Field(ge=20, le=400)
    date: str | None = None


@app.post("/api/v1/weight")
def api_weight(w: ApiWeightIn, u=Depends(token_user)):
    day = check_day(w.date) if w.date else date.today().isoformat()
    set_weight(day, WeightIn(kg=w.kg), u)
    return build_status(u, day)


class ApiDrinkIn(BaseModel):
    kind: DrinkKind
    size: Literal["cup", "mug", "espresso"] = "cup"


@app.post("/api/v1/drink")
def api_drink(c: ApiDrinkIn, u=Depends(token_user)):
    day = date.today().isoformat()
    add_drink(DrinkIn(day=day, size=c.size, kind=c.kind), u)
    return build_status(u, day)


@app.delete("/api/v1/drink/last")
def api_undo_drink(kind: DrinkKind, u=Depends(token_user)):
    day = date.today().isoformat()
    undo_drink(day, kind, u)
    return build_status(u, day)


# ---------------------------------------------------------------- Feature Requests

FEEDBACK_KINDS = {"feature": "Feature Request", "bug": "Bug Report", "food": "Lebensmittel-Meldung"}
SCREENSHOT_MAX_BYTES = 3 * 1024 * 1024
SCREENSHOT_TYPES = {b"\xff\xd8\xff": ("jpg", "image/jpeg"), b"\x89PNG": ("png", "image/png")}


class FeedbackIn(BaseModel):
    text: str = Field(min_length=5, max_length=2000)
    kind: Literal["feature", "bug", "food"] = "feature"
    # Technische Angaben vom Gerät (nur bei Bug Reports): Version, Browser, Ansicht, letzte JS-Fehler …
    meta: dict[str, str | int | float | bool | None] | None = None
    screenshot: str | None = Field(default=None, max_length=5_000_000)  # data:image/…;base64,…


def _decode_screenshot(data_url: str) -> tuple[bytes, str, str]:
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except Exception:
        raise HTTPException(422, "Screenshot konnte nicht gelesen werden")
    if len(raw) > SCREENSHOT_MAX_BYTES:
        raise HTTPException(422, "Screenshot ist zu groß (max. 3 MB)")
    for magic, (ext, mime) in SCREENSHOT_TYPES.items():
        if raw.startswith(magic):
            return raw, ext, mime
    raise HTTPException(422, "Screenshot muss ein JPEG- oder PNG-Bild sein")


@app.post("/api/feedback")
def send_feedback(f: FeedbackIn, u=Depends(current_user)):
    """Feature Request oder Bug Report speichern und per Mail an FEEDBACK_TO schicken
    (Antwort geht direkt an den Nutzer; Screenshot als Anhang und im Datenordner)."""
    throttle(f"feedback:{u['id']}", limit=5, window=3600)
    text, label = f.text.strip(), FEEDBACK_KINDS[f.kind]
    meta = {k[:40]: (str(v)[:500] if v is not None else None) for k, v in list((f.meta or {}).items())[:30]}
    shot = _decode_screenshot(f.screenshot) if f.screenshot else None
    with db() as conn:
        fid = conn.execute("INSERT INTO feedback (user_id, text, kind, meta, created) VALUES (?,?,?,?,?)",
                           (u["id"], text, f.kind, json.dumps(meta, ensure_ascii=False) if meta else None,
                            int(time.time()))).lastrowid
    attachments = []
    if shot:
        raw, ext, mime = shot
        folder = Path(DB_PATH).parent / "feedback"
        folder.mkdir(exist_ok=True)
        (folder / f"{fid}.{ext}").write_bytes(raw)
        with db() as conn:
            conn.execute("UPDATE feedback SET attachment=? WHERE id=?", (f"feedback/{fid}.{ext}", fid))
        attachments.append((f"bissfest-{f.kind}-{fid}.{ext}", raw, mime))
    mailed = False
    if mailer.configured() and FEEDBACK_TO:
        tech = "".join(f"{k}: {v}\n" for k, v in meta.items())
        body = (f"{text}\n\n—\n{label} #{fid} aus Bissfest {APP_VERSION}\n"
                f"Benutzer: {u['username']}\nE-Mail: {u['email'] or 'keine hinterlegt'}\n"
                f"Zeit: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                + (f"Screenshot: im Anhang\n" if attachments else "")
                + (f"\nTechnische Angaben:\n{tech}" if tech else ""))
        try:
            mailer.send(FEEDBACK_TO, f"Bissfest {label} #{fid} von {u['username']}", body,
                        reply_to=u["email"] or None, attachments=attachments)
            mailed = True
            with db() as conn:
                conn.execute("UPDATE feedback SET mailed=1 WHERE id=?", (fid,))
        except Exception as e:
            logging.getLogger("mail").error("%s #%s nicht gesendet: %s", label, fid, e)
    return {"id": fid, "mailed": mailed}


# ---------------------------------------------------------------- Admin-Dashboard (/admin)
# Zeigt bewusst nur Anzahlen und Aktivität – keine Ernährungstagebücher, Gewichte oder Körperdaten.

def admin_user(u=Depends(current_user)) -> sqlite3.Row:
    if not is_admin(u):
        raise HTTPException(403, "Nur für Admins")
    return u


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


@app.get("/api/admin/overview")
def admin_overview(a=Depends(admin_user)):
    today = date.today().isoformat()
    with db() as conn:
        one = lambda sql, *args: conn.execute(sql, args).fetchone()[0]
        active7 = one("""SELECT COUNT(DISTINCT user_id) FROM (
                           SELECT user_id FROM food_entries WHERE day >= ? UNION
                           SELECT user_id FROM water_entries WHERE day >= ? UNION
                           SELECT user_id FROM coffee_entries WHERE day >= ? UNION
                           SELECT user_id FROM weights WHERE day >= ?)""", *[_ago(6)] * 4)
        return {
            "users": one("SELECT COUNT(*) FROM users"),
            "users_verified": one("SELECT COUNT(*) FROM users WHERE email_verified=1"),
            "users_active_7d": active7,
            "users_new_7d": one("SELECT COUNT(*) FROM users WHERE created >= ?", int(time.time()) - 7 * 86400),
            "entries_today": one("SELECT COUNT(*) FROM food_entries WHERE day=?", today),
            "entries_total": one("SELECT COUNT(*) FROM food_entries"),
            "feedback_open": one("SELECT COUNT(*) FROM feedback WHERE status='offen'"),
            "bugs_open": one("SELECT COUNT(*) FROM feedback WHERE status='offen' AND kind='bug'"),
            "custom_foods": one("SELECT COUNT(*) FROM custom_foods"),
            "templates": one("SELECT COUNT(*) FROM meal_templates"),
            "push_devices": one("SELECT COUNT(*) FROM push_subscriptions"),
            "api_tokens": one("SELECT COUNT(*) FROM api_tokens"),
        }


@app.get("/api/admin/activity")
def admin_activity(days: int = 30, a=Depends(admin_user)):
    days = max(7, min(days, 365))
    with db() as conn:
        rows = conn.execute("""SELECT day, COUNT(*) AS entries, COUNT(DISTINCT user_id) AS users
                               FROM food_entries WHERE day >= ? AND day <= ? GROUP BY day ORDER BY day""",
                            (_ago(days - 1), date.today().isoformat())).fetchall()
    return {"days": days, "rows": [dict(r) for r in rows]}


@app.get("/api/admin/users")
def admin_users(a=Depends(admin_user)):
    with db() as conn:
        rows = conn.execute("""
            SELECT u.id, u.username, u.email, u.email_verified, u.created,
                   (SELECT COUNT(*) FROM food_entries f WHERE f.user_id = u.id) AS entries,
                   MAX(COALESCE((SELECT MAX(created) FROM food_entries f WHERE f.user_id = u.id), 0),
                       COALESCE((SELECT MAX(created) FROM water_entries w WHERE w.user_id = u.id), 0),
                       COALESCE((SELECT MAX(created) FROM coffee_entries c WHERE c.user_id = u.id), 0)) AS last_active,
                   (SELECT COUNT(*) FROM push_subscriptions p WHERE p.user_id = u.id) AS push_devices,
                   (SELECT COUNT(*) FROM api_tokens t WHERE t.user_id = u.id) AS api_tokens
            FROM users u ORDER BY u.created""").fetchall()
    return [{**dict(r), "last_active": r["last_active"] or None, "is_admin": r["username"].lower() in ADMIN_USERS}
            for r in rows]


def _target_user(user_id: int) -> sqlite3.Row:
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    return u


@app.post("/api/admin/users/{user_id}/verify")
def admin_verify_user(user_id: int, a=Depends(admin_user)):
    _target_user(user_id)
    with db() as conn:
        conn.execute("UPDATE users SET email_verified=1 WHERE id=?", (user_id,))
        conn.execute("DELETE FROM email_tokens WHERE user_id=? AND purpose='verify'", (user_id,))
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/resend")
def admin_resend(user_id: int, request: Request, a=Depends(admin_user)):
    u = _target_user(user_id)
    if u["email_verified"]:
        raise HTTPException(400, "Konto ist bereits bestätigt")
    if not u["email"]:
        raise HTTPException(400, "Keine E-Mail-Adresse hinterlegt")
    try:
        send_verification(request, u["id"], u["username"], u["email"])
    except Exception as e:
        raise HTTPException(502, f"Mail konnte nicht verschickt werden: {e}")
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, a=Depends(admin_user)):
    """Konto mit allen Einträgen löschen. Gemeinsame Lebensmittel bleiben (ohne Ersteller) erhalten."""
    if user_id == a["id"]:
        raise HTTPException(400, "Das eigene Konto kann hier nicht gelöscht werden")
    u = _target_user(user_id)
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    avatar_path(user_id).unlink(missing_ok=True)
    logging.getLogger("admin").info("Konto %s (#%s) gelöscht von %s", u["username"], user_id, a["username"])
    return {"ok": True}


@app.get("/api/admin/feedback")
def admin_feedback(a=Depends(admin_user)):
    with db() as conn:
        rows = conn.execute("""SELECT f.id, f.kind, f.text, f.meta, f.attachment, f.status, f.mailed, f.created,
                                      u.username, u.email
                               FROM feedback f LEFT JOIN users u ON u.id = f.user_id ORDER BY f.id DESC""").fetchall()
    return [{**dict(r), "meta": json.loads(r["meta"]) if r["meta"] else None, "has_screenshot": bool(r["attachment"])}
            for r in rows]


class FeedbackStatus(BaseModel):
    status: Literal["offen", "erledigt"]


@app.patch("/api/admin/feedback/{fid}")
def admin_feedback_status(fid: int, s: FeedbackStatus, a=Depends(admin_user)):
    with db() as conn:
        cur = conn.execute("UPDATE feedback SET status=? WHERE id=?", (s.status, fid))
    if not cur.rowcount:
        raise HTTPException(404, "Rückmeldung nicht gefunden")
    return {"ok": True}


@app.get("/api/admin/feedback/{fid}/screenshot")
def admin_feedback_screenshot(fid: int, a=Depends(admin_user)):
    with db() as conn:
        row = conn.execute("SELECT attachment FROM feedback WHERE id=?", (fid,)).fetchone()
    path = (Path(DB_PATH).parent / row["attachment"]) if row and row["attachment"] else None
    if not path or not path.is_file():
        raise HTTPException(404, "Kein Screenshot vorhanden")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/admin/foods")
def admin_foods(a=Depends(admin_user)):
    with db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.manufacturer, c.brand, c.code, c.kcal_100g, c.protein_100g, c.carbs_100g,
                   c.sugar_100g, c.fat_100g, c.created, u.username AS created_by,
                   (SELECT COUNT(*) FROM food_entries f WHERE f.source = 'Bissfest-DB'
                      AND (f.code = 'custom:' || c.id OR (c.code != '' AND f.code = c.code))) AS uses
            FROM custom_foods c LEFT JOIN users u ON u.id = c.created_by ORDER BY c.name COLLATE NOCASE""").fetchall()
    return [dict(r) for r in rows]


@app.delete("/api/admin/foods/{food_id}")
def admin_delete_food(food_id: int, a=Depends(admin_user)):
    """Aus der gemeinsamen Datenbank entfernen; bereits eingetragene Mahlzeiten bleiben unverändert."""
    with db() as conn:
        cur = conn.execute("DELETE FROM custom_foods WHERE id=?", (food_id,))
    if not cur.rowcount:
        raise HTTPException(404, "Lebensmittel nicht gefunden")
    return {"ok": True}


def portion_food_info(conn: sqlite3.Connection, key: str) -> dict:
    """Name zu einem Portionsschlüssel. Portionen speichern nur den Schlüssel, der Name steht in den Einträgen."""
    if key.startswith("n:"):
        name, _, brand = key[2:].partition("|")
        where, params, fallback = "LOWER(name)=? AND LOWER(brand)=?", (name, brand), {"name": name, "brand": brand, "source": ""}
    elif key.startswith("usda:"):
        where, params, fallback = "code=? AND source='USDA'", (key[5:],), {"name": f"USDA {key[5:]}", "brand": "", "source": "USDA"}
    else:
        where, params, fallback = "code=?", (key,), {"name": f"Barcode {key}", "brand": "", "source": ""}
    for table in ("food_entries", "meal_template_items"):
        row = conn.execute(f"SELECT name, brand, source FROM {table} WHERE {where} ORDER BY id DESC LIMIT 1", params).fetchone()
        if row:
            return dict(row)
    return fallback


@app.get("/api/admin/portions")
def admin_portions(a=Depends(admin_user)):
    """Lebensmittel aus anderen Datenbanken, für die Portionen gespeichert sind."""
    foods = {}
    with db() as conn:
        own_codes = {r["code"] for r in conn.execute("SELECT code FROM custom_foods WHERE code != ''")}
        rows = conn.execute("""SELECT p.id, p.food_key, p.name, p.grams, u.username AS created_by FROM food_portions p
                               LEFT JOIN users u ON u.id = p.created_by WHERE p.food_key NOT LIKE 'custom:%'
                               ORDER BY p.grams""").fetchall()
        for r in rows:
            if r["food_key"] in own_codes:  # steht schon in der Liste der Bissfest-Datenbank
                continue
            food = foods.get(r["food_key"])
            if not food:
                food = foods[r["food_key"]] = {"key": r["food_key"], **portion_food_info(conn, r["food_key"]), "portions": []}
            food["portions"].append({"id": r["id"], "name": r["name"], "grams": r["grams"], "created_by": r["created_by"]})
    return sorted(foods.values(), key=lambda f: f["name"].lower())


class AdminPortionNew(PortionEdit):
    food_key: str = Field(min_length=1, max_length=330)


@app.post("/api/admin/portions")
def admin_add_portion(p: AdminPortionNew, a=Depends(admin_user)):
    try:
        with db() as conn:
            conn.execute("INSERT INTO food_portions (food_key, name, grams, created_by, created) VALUES (?,?,?,?,?)",
                         (p.food_key, p.name.strip(), p.grams, a["id"], int(time.time())))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Dieses Lebensmittel hat schon eine Portion mit diesem Namen")
    return {"ok": True}


@app.get("/api/admin/system")
def admin_system(a=Depends(admin_user)):
    data_dir = Path(DB_PATH).parent
    disk = shutil.disk_usage(data_dir)
    last_backup = None
    status_file = backup.BACKUP_DIR / "letztes-backup.txt"
    try:
        if status_file.exists():
            last_backup = status_file.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return {
        "version": APP_VERSION,
        "registration": {"configured": ALLOW_REGISTRATION, "switch": get_setting("registration_open", "1") == "1",
                         "mail": mail_ready(), "open": registration_open(), "full": registration_full(),
                         "max_users": max_users(), "users": user_count()},
        "mail_from": mailer.SMTP_FROM or None,
        "feedback_to": FEEDBACK_TO,
        "backup": {"time": backup.BACKUP_TIME or None, "keep_days": backup.BACKUP_KEEP_DAYS, "last": last_backup,
                   "target_ok": (backup.BACKUP_DIR / backup.MARKER).exists()},
        "db_bytes": Path(DB_PATH).stat().st_size if Path(DB_PATH).exists() else 0,
        "disk_free_bytes": disk.free, "disk_total_bytes": disk.total,
        "push_configured": push.KEY_FILE.exists(),
        "android_apk": APK_PATH.exists(),
        "admins": sorted(ADMIN_USERS),
    }


class MaxUsers(BaseModel):
    max_users: int = Field(ge=0, le=100000)


@app.put("/api/admin/max-users")
def admin_max_users(m: MaxUsers, a=Depends(admin_user)):
    """Bestehende Konten bleiben immer erhalten, das Limit wirkt nur auf neue Registrierungen."""
    with db() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('max_users', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(m.max_users),))
    return admin_system(a)


class RegistrationSwitch(BaseModel):
    open: bool


@app.put("/api/admin/registration")
def admin_registration(r: RegistrationSwitch, a=Depends(admin_user)):
    with db() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('registration_open', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if r.open else "0",))
    return admin_system(a)


# ---------------------------------------------------------------- Push-Erinnerungen

def push_device_count(user_id: int) -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?", (user_id,)).fetchone()[0]


def send_meal_reminders(minute: datetime):
    """Jede Minute: Erinnerung für Mahlzeiten, deren Zeit jetzt ist und für die heute noch nichts eingetragen wurde."""
    hhmm, day = minute.strftime("%H:%M"), minute.date().isoformat()
    if hhmm == "04:15":
        with db() as conn:
            cleanup_unverified(conn)
    with db() as conn:
        users = conn.execute(
            "SELECT * FROM users WHERE reminders LIKE ? AND id IN (SELECT user_id FROM push_subscriptions)",
            (f'%"{hhmm}"%',)).fetchall()
    for u in users:
        for meal, t in json.loads(u["reminders"] or "{}").items():
            meal = int(meal)
            if t != hhmm or meal >= u["meals_per_day"]:
                continue
            with db() as conn:
                has_entry = conn.execute("SELECT 1 FROM food_entries WHERE user_id=? AND day=? AND meal=? LIMIT 1",
                                         (u["id"], day, meal)).fetchone()
            if has_entry:
                continue
            name, emoji, text = MEALS[meal]
            push.send(db, u["id"], {
                "title": f"{emoji} {name}",
                "body": text,
                "tag": f"meal-{meal}",
                "url": "/",
            })


@app.get("/api/push/key")
def push_key(u=Depends(current_user)):
    return {"key": push.public_key()}


class PushSubscription(BaseModel):
    endpoint: str = Field(max_length=1000, pattern=r"^https://")
    keys: dict[str, str]


# Push-Abos nur bei den Diensten der Browser (Chrome/Android, Firefox, Safari/iOS, Edge). Sonst ließe sich
# der Server über /api/push/test Anfragen an beliebige Adressen schicken, auch ins interne Netz.
PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")


def push_host_ok(endpoint: str) -> bool:
    host = (urlparse(endpoint).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


@app.post("/api/push/subscribe")
def push_subscribe(sub: PushSubscription, u=Depends(current_user)):
    if not sub.keys.get("p256dh") or not sub.keys.get("auth"):
        raise HTTPException(400, "Ungültiges Push-Abo")
    if not push_host_ok(sub.endpoint):
        raise HTTPException(400, "Unbekannter Push-Dienst")
    with db() as conn:
        conn.execute(
            "INSERT INTO push_subscriptions (endpoint, user_id, p256dh, auth, created) VALUES (?,?,?,?,?) "
            "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth",
            (sub.endpoint, u["id"], sub.keys["p256dh"], sub.keys["auth"], int(time.time())))
    return {"push_devices": push_device_count(u["id"])}


class PushEndpoint(BaseModel):
    endpoint: str


@app.post("/api/push/unsubscribe")
def push_unsubscribe(p: PushEndpoint, u=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?", (p.endpoint, u["id"]))
    return {"push_devices": push_device_count(u["id"])}


@app.post("/api/push/test")
def push_test(u=Depends(current_user)):
    sent = push.send(db, u["id"], {"title": "✅ Erinnerungen aktiv",
                                   "body": "So sehen deine Mahlzeiten-Erinnerungen aus.", "tag": "test", "url": "/"})
    if not sent:
        raise HTTPException(400, "Kein Gerät für Benachrichtigungen registriert")
    return {"sent": sent}


# ---------------------------------------------------------------- Frontend

@app.get("/")
def index():
    # __V__ = App-Version an CSS/JS-Adressen: nach jedem Update lädt der Browser sie garantiert neu
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("__V__", APP_VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/admin")
def admin_page():
    html = (STATIC_DIR / "admin.html").read_text(encoding="utf-8").replace("__V__", APP_VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links: bestätigt Android, dass die Bissfest-App zu dieser Domain gehört (Vollbild ohne Adressleiste)."""
    if not (ANDROID_PACKAGE and ANDROID_CERT_SHA256):
        raise HTTPException(404)
    return [{"relation": ["delegate_permission/common.handle_all_urls"],
             "target": {"namespace": "android_app", "package_name": ANDROID_PACKAGE,
                        "sha256_cert_fingerprints": [ANDROID_CERT_SHA256]}}]


@app.get("/download/bissfest.apk")
def download_apk():
    if not APK_PATH.exists():
        raise HTTPException(404, "Die Android-App ist noch nicht hinterlegt")
    return FileResponse(APK_PATH, media_type="application/vnd.android.package-archive",
                        filename=f"bissfest-{APP_VERSION}.apk", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
