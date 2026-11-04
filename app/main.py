"""Bissfest – mit Biss zum Wunschgewicht: kleines Multi-User-Backend (FastAPI + SQLite)."""
import asyncio
import hashlib
import json
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import backup
import mailer
import push

DB_PATH = os.environ.get("DB_PATH", "/data/abnehm.db")
ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "true").lower() == "true"
FDC_API_KEY = os.environ.get("FDC_API_KEY", "DEMO_KEY")  # USDA FoodData Central, leer = aus
OFF_COUNTRY = os.environ.get("OFF_COUNTRY", "de")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
# Öffentliche Adresse für Links in E-Mails (leer = aus der Anfrage ermitteln)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
VERIFY_HOURS = 24
UNVERIFIED_KEEP_DAYS = 7
SESSION_DAYS = 60
USER_AGENT = "Bissfest/1.4 (self-hosted)"
STATIC_DIR = Path(__file__).parent / "static"

APP_VERSION = "1.14.0"

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

app = FastAPI(title="Bissfest")


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
PROFILE_COLUMNS = {"sex": "TEXT", "birth_year": "INTEGER", "height_cm": "REAL", "activity": "REAL", "weekly_loss": "REAL",
                   "reminders": "TEXT", "email": "TEXT",
                   # bestehende Konten gelten als bestätigt; neue Registrierungen setzen 0
                   "email_verified": "INTEGER NOT NULL DEFAULT 1",
                   # Kaffee-Tracker (Extra, standardmäßig aus) und Koffein pro Tasse
                   "coffee_enabled": "INTEGER NOT NULL DEFAULT 0", "caffeine_mg": "INTEGER NOT NULL DEFAULT 80"}
# Später ergänzte Spalten für Einträge und Gericht-Zutaten: Zucker und die gewählte Portion ("2 × Scheibe")
ENTRY_COLUMNS = {"sugar_100g": "REAL", "portion_name": "TEXT", "portion_count": "REAL"}
# Kaffee-Größen in ml; das eingestellte Koffein (caffeine_mg) gilt für eine Tasse
COFFEE_SIZES = {"cup": 150, "mug": 250}
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


@app.on_event("startup")
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
        migrate_meal_order(conn)
        # 1.13 zählte nur Tassen (Tabelle "coffee"): als 150-ml-Tassen übernehmen, dann alte Tabelle entfernen
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coffee'").fetchone():
            for r in conn.execute("SELECT user_id, day, cups FROM coffee WHERE cups > 0").fetchall():
                conn.executemany("INSERT INTO coffee_entries (user_id, day, ml, created) VALUES (?,?,?,?)",
                                 [(r["user_id"], r["day"], COFFEE_SIZES["cup"], 0)] * r["cups"])
            conn.execute("DROP TABLE coffee")
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

def hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


_login_attempts: dict[str, list[float]] = {}


def throttle(key: str, limit: int = 10, window: int = 300):
    now = time.time()
    hits = [t for t in _login_attempts.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Zu viele Versuche, bitte später erneut probieren.")
    hits.append(now)
    _login_attempts[key] = hits


def current_user(request: Request) -> sqlite3.Row:
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(401, "Nicht angemeldet")
    with db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires > ?",
            (token, int(time.time())),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Sitzung abgelaufen")
    return row


def start_session(response: Response, user_id: int):
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires) VALUES (?, ?, ?)",
            (token, user_id, int(time.time()) + SESSION_DAYS * 86400),
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
        "coffee_enabled": bool(u["coffee_enabled"]),
        "caffeine_mg": u["caffeine_mg"],
    }


def latest_weight(user_id: int) -> float | None:
    with db() as conn:
        row = conn.execute("SELECT kg FROM weights WHERE user_id=? ORDER BY day DESC LIMIT 1", (user_id,)).fetchone()
    return row["kg"] if row else None


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=200)


@app.get("/api/config")
def config():
    return {"allow_registration": ALLOW_REGISTRATION and mailer.configured(), "version": APP_VERSION}


class RegisterIn(Credentials):
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def cleanup_unverified(conn: sqlite3.Connection):
    """Nie bestätigte Konten nach UNVERIFIED_KEEP_DAYS Tagen und abgelaufene Links löschen."""
    conn.execute("DELETE FROM users WHERE email_verified = 0 AND created < ?",
                 (int(time.time()) - UNVERIFIED_KEEP_DAYS * 86400,))
    conn.execute("DELETE FROM email_tokens WHERE expires < ?", (int(time.time()),))


def public_base(request: Request) -> str:
    return PUBLIC_URL or str(request.base_url).rstrip("/")


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
    if not ALLOW_REGISTRATION:
        raise HTTPException(403, "Registrierung ist deaktiviert")
    if not mailer.configured():
        raise HTTPException(503, "Registrierung ist gerade nicht möglich: Der E-Mail-Versand ist noch nicht eingerichtet.")
    throttle("reg:" + (request.client.host if request.client else "?"), limit=5, window=3600)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", creds.username):
        raise HTTPException(400, "Benutzername: nur Buchstaben, Zahlen, _ . -")
    email = creds.email.strip().lower()
    salt = secrets.token_hex(16)
    with db() as conn:
        cleanup_unverified(conn)
        if conn.execute("SELECT 1 FROM users WHERE username=?", (creds.username,)).fetchone():
            raise HTTPException(409, "Benutzername ist bereits vergeben")
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Für diese E-Mail-Adresse gibt es schon ein Konto")
        user_id = conn.execute(
            "INSERT INTO users (username, pw_hash, salt, created, email, email_verified) VALUES (?, ?, ?, ?, ?, 0)",
            (creds.username, hash_pw(creds.password, salt), salt, int(time.time()), email),
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
    if not u or not hmac.compare_digest(u["pw_hash"], hash_pw(creds.password, u["salt"])):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    if u["email_verified"]:
        return {"ok": True, "already": True}
    try:
        send_verification(request, u["id"], u["username"], u["email"])
    except Exception as e:
        logging.getLogger("mail").error("Bestätigungsmail an %s fehlgeschlagen: %s", u["email"], e)
        raise HTTPException(502, "Die Mail konnte nicht verschickt werden. Bitte später erneut versuchen.")
    return {"ok": True, "email": u["email"]}


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
    throttle("login:" + (request.client.host if request.client else "?") + ":" + creds.username.lower())
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE username = ?", (creds.username,)).fetchone()
    if not u or not hmac.compare_digest(u["pw_hash"], hash_pw(creds.password, u["salt"])):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    if not u["email_verified"]:
        raise HTTPException(403, f"Bitte bestätige zuerst deine E-Mail-Adresse – wir haben dir einen Link an {u['email']} geschickt.")
    start_session(response, u["id"])
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
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
            "coffee_enabled=COALESCE(?, coffee_enabled), caffeine_mg=COALESCE(?, caffeine_mg) WHERE id=?",
            (s.meals_per_day, s.kcal_goal, s.water_goal_ml, s.target_weight,
             s.sex, s.birth_year, s.height_cm, s.activity, s.weekly_loss,
             None if s.coffee_enabled is None else int(s.coffee_enabled), s.caffeine_mg, u["id"]),
        )
        u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    return user_public(u)


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=200)


@app.put("/api/password")
def change_password(p: PasswordChange, request: Request, u=Depends(current_user)):
    if not hmac.compare_digest(u["pw_hash"], hash_pw(p.old_password, u["salt"])):
        raise HTTPException(400, "Altes Passwort ist falsch")
    salt = secrets.token_hex(16)
    with db() as conn:
        conn.execute("UPDATE users SET pw_hash=?, salt=? WHERE id=?", (hash_pw(p.new_password, salt), salt, u["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=? AND token != ?", (u["id"], request.cookies.get("session")))
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
        coffee = coffee_day(conn, u["id"], day)
    totals = {k: round(sum((f[k] or 0) for f in foods), 1) for k in ("kcal", *NUTRIENTS)}
    return {"day": day, "foods": foods, "water_ml": water["ml"], "water_count": water["n"],
            "weight": w["kg"] if w else None, "coffee": coffee, "totals": totals}


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


def coffee_day(conn: sqlite3.Connection, user_id: int, day: str) -> dict:
    r = conn.execute(
        "SELECT COALESCE(SUM(ml = ?), 0) AS cups, COALESCE(SUM(ml = ?), 0) AS mugs, COALESCE(SUM(ml), 0) AS ml "
        "FROM coffee_entries WHERE user_id=? AND day=?",
        (COFFEE_SIZES["cup"], COFFEE_SIZES["mug"], user_id, day)).fetchone()
    return dict(r)


class CoffeeIn(BaseModel):
    day: str
    size: Literal["cup", "mug"] = "cup"


@app.post("/api/coffee")
def add_coffee(c: CoffeeIn, u=Depends(current_user)):
    """Tasse (150 ml) oder Pott (250 ml) eintragen."""
    day = check_day(c.day)
    with db() as conn:
        conn.execute("INSERT INTO coffee_entries (user_id, day, ml, created) VALUES (?,?,?,?)",
                     (u["id"], day, COFFEE_SIZES[c.size], int(time.time())))
        return coffee_day(conn, u["id"], day)


@app.delete("/api/coffee/last")
def undo_coffee(day: str, u=Depends(current_user)):
    """Den zuletzt eingetragenen Kaffee des Tages zurücknehmen."""
    day = check_day(day)
    with db() as conn:
        conn.execute("DELETE FROM coffee_entries WHERE id = (SELECT id FROM coffee_entries WHERE user_id=? AND day=? "
                     "ORDER BY created DESC, id DESC LIMIT 1)", (u["id"], day))
        return coffee_day(conn, u["id"], day)


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
        coffee = conn.execute("SELECT day, SUM(ml) AS ml FROM coffee_entries WHERE user_id=? AND day>=? "
                              "GROUP BY day ORDER BY day", (u["id"], start)).fetchall()
    return {"weights": [dict(r) for r in weights], "kcal": [dict(r) for r in kcal], "water": [dict(r) for r in water],
            "coffee": [dict(r) for r in coffee]}


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
    r = await client.get("https://api.nal.usda.gov/fdc/v1/foods/search",
                         params={"api_key": FDC_API_KEY, "query": q, "pageSize": 15,
                                 "dataType": "Foundation,SR Legacy,Survey (FNDDS)"})
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
    (r"nutella|nussnougat|marmelade|konfitüre|honig|aufstrich", "TL", 8),
    (r"haferflocken|müsli|granola|cornflakes", "EL", 10), (r"\böl\b|olivenöl|rapsöl", "EL", 10),
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
    """Portion für alle Nutzer speichern; gleicher Name überschreibt die Grammzahl."""
    key = food_key(p.source, p.code, p.name, p.brand)
    with db() as conn:
        conn.execute(
            "INSERT INTO food_portions (food_key, name, grams, created_by, created) VALUES (?,?,?,?,?) "
            "ON CONFLICT(food_key, name) DO UPDATE SET grams=excluded.grams",
            (key, p.portion_name.strip(), p.grams, u["id"], int(time.time())))
    return get_portions(p.name, p.brand, p.source, p.code, u)


@app.delete("/api/portions/{portion_id}")
def delete_portion(portion_id: int, u=Depends(current_user)):
    with db() as conn:
        cur = conn.execute("DELETE FROM food_portions WHERE id=? AND created_by=?", (portion_id, u["id"]))
    if not cur.rowcount:
        raise HTTPException(403, "Nur wer die Portion angelegt hat, kann sie löschen")
    return {"ok": True}


# ---------------------------------------------------------------- Gerichte (gespeicherte Mahlzeiten)

ITEM_COLS = ("name, brand, grams, kcal_100g, protein_100g, carbs_100g, sugar_100g, fat_100g, source, code, "
             "portion_name, portion_count")


@app.get("/api/templates")
def list_templates(u=Depends(current_user)):
    with db() as conn:
        tpls = conn.execute("SELECT * FROM meal_templates WHERE user_id=? ORDER BY name COLLATE NOCASE",
                            (u["id"],)).fetchall()
        out = []
        for t in tpls:
            items = [entry_dict(r) for r in conn.execute(
                "SELECT * FROM meal_template_items WHERE template_id=? ORDER BY id", (t["id"],))]
            out.append({"id": t["id"], "name": t["name"], "items": items,
                        "kcal": round(sum(i["kcal"] for i in items)),
                        "grams": round(sum(i["grams"] for i in items))})
    return out


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


@app.post("/api/push/subscribe")
def push_subscribe(sub: PushSubscription, u=Depends(current_user)):
    if not sub.keys.get("p256dh") or not sub.keys.get("auth"):
        raise HTTPException(400, "Ungültiges Push-Abo")
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


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
