"""Trophäen: Katalog, Kennzahlen je Nutzer und Vergabe.

Eine Trophäe gilt als verdient, sobald ihre Kennzahl das Ziel erreicht, und bleibt danach erhalten.
Bewusst gibt es nichts für besonders wenig Essen oder besonders schnelles Abnehmen: Ein Zieltag liegt nah am
Kalorienziel, nicht möglichst weit darunter.
"""
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, timedelta

GOAL_MIN, GOAL_MAX = 0.85, 1.05  # Zieltag: gegessen zwischen 85 und 105 % des Kalorienziels
OVER_GOAL = 1.1
LOW_SUGAR_G = 25
PROTEIN_G = 100
COMEBACK_GAP_DAYS = 8


@dataclass(frozen=True)
class Trophy:
    category: str
    key: str
    icon: str
    name: str
    description: str
    metric: str
    target: float
    secret: bool = False
    requires: str = ""  # Nutzer-Einstellung, ohne die die Trophäe nicht angezeigt wird


def _series(category: str, metric: str, items: tuple) -> list[Trophy]:
    return [Trophy(category, f"{metric}_{str(target).replace('.', '_')}", icon, name, description, metric, target)
            for target, icon, name, description in items]


START, STREAK, WEIGHT, SCALE, WATER, GOAL, FOOD, DRINKS, APP, SECRET, META = (
    "Dranbleiben", "Serien", "Abnehmen", "Waage", "Wasser", "Kalorienziel", "Essen", "Heißgetränke", "Bissfest-Profi",
    "Geheim", "Sammlung")

CATALOG: list[Trophy] = [
    Trophy(START, "first_entry", "🦷", "Erster Biss", "Trage dein erstes Lebensmittel ein.", "entries", 1),
    *_series(START, "days_done", (
        (1, "🌱", "Tag eins geschafft", "Dein erster vollständig getrackter Tag ist vorbei."),
        (3, "🌿", "Aller guten Dinge", "3 Tage getrackt."),
        (7, "📅", "Die erste Woche", "7 Tage getrackt."),
        (14, "🗓️", "Zwei Wochen stark", "14 Tage getrackt."),
        (30, "🌙", "Der erste Monat", "30 Tage getrackt."),
        (50, "🥉", "Halbe Hundert", "50 Tage getrackt."),
        (100, "🥈", "Dreistellig", "100 Tage getrackt."),
        (182, "🥇", "Ein halbes Jahr", "182 Tage getrackt."),
        (365, "🎂", "Ein ganzes Jahr", "365 Tage getrackt."),
        (730, "🏛️", "Zwei Jahre Biss", "730 Tage getrackt."),
    )),
    *_series(STREAK, "streak", (
        (3, "🔥", "Funke", "3 Tage in Folge getrackt."),
        (7, "🔥", "Kleines Feuer", "7 Tage in Folge getrackt."),
        (14, "🔥", "Lagerfeuer", "14 Tage in Folge getrackt."),
        (30, "🌋", "Monatsflamme", "30 Tage in Folge getrackt."),
        (60, "🌋", "Dauerbrenner", "60 Tage in Folge getrackt."),
        (100, "☄️", "Hundert am Stück", "100 Tage in Folge getrackt."),
        (365, "☀️", "Ewige Flamme", "365 Tage in Folge getrackt."),
    )),
    Trophy(STREAK, "comeback", "🪃", "Wieder da", "Nach mindestens einer Woche Pause wieder eingestiegen. Stark!", "comebacks", 1),
    Trophy(STREAK, "bounce_back", "🤸", "Stehaufmensch", "Am Tag nach einem Ausrutscher wieder im Kalorienziel gelandet.", "bounce_backs", 1),
    *_series(WEIGHT, "lost_kg", (
        (1, "🪶", "Das erste Kilo", "1 kg abgenommen."),
        (2, "🧈", "Acht Stück Butter", "2 kg abgenommen."),
        (3, "🍉", "Eine Wassermelone weniger", "3 kg abgenommen."),
        (5, "🎳", "Eine Bowlingkugel", "5 kg abgenommen."),
        (7.5, "🐈", "Ein Kater weniger", "7,5 kg abgenommen."),
        (10, "🏋️", "Zweistellig", "10 kg abgenommen."),
        (15, "🚲", "Ein Fahrrad", "15 kg abgenommen."),
        (20, "🧳", "Koffer abgegeben", "20 kg abgenommen."),
        (25, "🧱", "Halber Zentner", "25 kg abgenommen."),
        (30, "🦁", "Löwenanteil", "30 kg abgenommen."),
        (40, "🏔️", "Gipfelstürmer", "40 kg abgenommen."),
        (50, "👑", "Ein ganzer Zentner", "50 kg abgenommen."),
    )),
    *_series(WEIGHT, "lost_percent", (
        (5, "📉", "Fünf Prozent", "5 % deines Startgewichts abgenommen. Das merkt schon dein Körper."),
        (10, "📉", "Zehn Prozent", "10 % deines Startgewichts abgenommen."),
    )),
    Trophy(WEIGHT, "target_half", "⛰️", "Halbzeit", "Die Hälfte des Wegs zum Zielgewicht geschafft.", "target_percent", 50),
    Trophy(WEIGHT, "target_three_quarters", "🧗", "Fast oben", "Drei Viertel des Wegs zum Zielgewicht geschafft.", "target_percent", 75),
    Trophy(WEIGHT, "target_reached", "🏁", "Wunschgewicht", "Du hast dein Zielgewicht erreicht!", "target_percent", 100),
    Trophy(WEIGHT, "target_hold", "⚖️", "Gekommen, um zu bleiben", "30 Wiegetage nah am Zielgewicht geblieben.", "target_hold", 30),
    *_series(SCALE, "weigh_days", (
        (1, "⚖️", "Bestandsaufnahme", "Zum ersten Mal gewogen."),
        (7, "🔎", "Wiegefreund", "An 7 Tagen gewogen."),
        (30, "📊", "Datensammler", "An 30 Tagen gewogen."),
        (100, "📈", "Statistik-Fan", "An 100 Tagen gewogen."),
        (365, "🔭", "Langzeitstudie", "An 365 Tagen gewogen."),
    )),
    *_series(SCALE, "weigh_streak", (
        (7, "🗞️", "Morgenritual", "7 Tage in Folge gewogen."),
        (30, "🧘", "Tiefenentspannt", "30 Tage in Folge gewogen."),
    )),
    *_series(WATER, "water_days", (
        (1, "💧", "Erster Schluck", "Dein Wasserziel zum ersten Mal erreicht."),
        (7, "🚰", "Gut gewässert", "Wasserziel an 7 Tagen erreicht."),
        (30, "🌊", "Im Fluss", "Wasserziel an 30 Tagen erreicht."),
        (100, "🐬", "Wasserratte", "Wasserziel an 100 Tagen erreicht."),
    )),
    *_series(WATER, "water_streak", (
        (7, "🌧️", "Regenwoche", "7 Tage in Folge das Wasserziel erreicht."),
        (30, "🏞️", "Stetiger Strom", "30 Tage in Folge das Wasserziel erreicht."),
    )),
    *_series(WATER, "water_liters", (
        (100, "🛁", "Eine Badewanne", "100 Liter Wasser getrunken."),
        (500, "🛶", "Ein Planschbecken", "500 Liter Wasser getrunken."),
        (1000, "🐳", "Tausend Liter", "1000 Liter Wasser getrunken."),
    )),
    *_series(GOAL, "goal_days", (
        (1, "🎯", "Volltreffer", "Einen Tag nah am Kalorienziel abgeschlossen."),
        (7, "🏹", "Treffsicher", "7 Tage nah am Kalorienziel."),
        (30, "🎯", "Scharfschütze", "30 Tage nah am Kalorienziel."),
        (100, "🧿", "Punktlandung", "100 Tage nah am Kalorienziel."),
    )),
    *_series(GOAL, "goal_streak", (
        (3, "🎳", "Hattrick", "3 Tage in Folge nah am Kalorienziel."),
        (7, "🎰", "Sieben auf einen Streich", "7 Tage in Folge nah am Kalorienziel."),
        (30, "🧭", "Auf Kurs", "30 Tage in Folge nah am Kalorienziel."),
    )),
    *_series(GOAL, "perfect_days", (
        (1, "💎", "Perfekter Tag", "Kalorienziel, Wasserziel, alle Mahlzeiten und gewogen an einem Tag."),
        (7, "💎", "Sieben Juwelen", "7 perfekte Tage."),
        (30, "👑", "Kronjuwelen", "30 perfekte Tage."),
    )),
    *_series(FOOD, "entries", (
        (100, "🍽️", "Hundert Happen", "100 Einträge."),
        (500, "📚", "Tagebuchschreiber", "500 Einträge."),
        (1000, "🗃️", "Tausendsassa", "1000 Einträge."),
        (5000, "🏆", "Chronist", "5000 Einträge."),
    )),
    *_series(FOOD, "unique_foods", (
        (10, "🧺", "Einkaufskorb", "10 verschiedene Lebensmittel eingetragen."),
        (50, "🌈", "Abwechslung", "50 verschiedene Lebensmittel eingetragen."),
        (100, "🧭", "Geschmacksforscher", "100 verschiedene Lebensmittel eingetragen."),
        (250, "🌍", "Weltenbummler", "250 verschiedene Lebensmittel eingetragen."),
    )),
    *_series(FOOD, "all_meals_days", (
        (1, "🍱", "Alles dabei", "Alle Mahlzeiten eines Tages eingetragen."),
        (30, "🧑‍🍳", "Lückenlos", "An 30 Tagen alle Mahlzeiten eingetragen."),
    )),
    *_series(FOOD, "protein_days", (
        (1, "🥚", "Eiweiß-Tag", f"An einem Tag {PROTEIN_G} g Eiweiß gegessen."),
        (30, "💪", "Muskelfutter", f"An 30 Tagen {PROTEIN_G} g Eiweiß gegessen."),
    )),
    *_series(FOOD, "low_sugar_days", (
        (1, "🍃", "Süße Pause", f"Einen Tag im Kalorienziel mit höchstens {LOW_SUGAR_G} g Zucker."),
        (10, "🌿", "Zuckerbremse", f"10 Tage im Kalorienziel mit höchstens {LOW_SUGAR_G} g Zucker."),
    )),
    *(Trophy(DRINKS, f"coffee_{t}", icon, name, f"{t} Kaffee getrunken.", "coffee", t, requires="coffee_enabled") for t, icon, name in (
        (1, "☕", "Erster Kaffee"), (100, "🫘", "Bohnenzähler"), (500, "⚙️", "Kaffeemaschine"))),
    *(Trophy(DRINKS, f"tea_{t}", icon, name, f"{t} Tee getrunken.", "tea", t, requires="tea_enabled") for t, icon, name in (
        (1, "🍵", "Erster Tee"), (100, "🫖", "Teekenner"), (500, "🧘", "Teemeister"))),
    Trophy(APP, "body_data", "📐", "Gut vermessen", "Körperdaten vollständig eingetragen.", "body_data", 1),
    Trophy(APP, "target_set", "🚩", "Ziel vor Augen", "Ein Zielgewicht festgelegt.", "target_set", 1),
    Trophy(APP, "avatar", "🤳", "Gesicht zeigen", "Ein Profilbild hochgeladen.", "avatar", 1),
    Trophy(APP, "reminders", "⏰", "Nie mehr vergessen", "Eine Erinnerung für eine Mahlzeit eingestellt.", "reminders", 1),
    Trophy(APP, "push", "🔔", "Immer erreichbar", "Push-Nachrichten auf einem Gerät aktiviert.", "push", 1),
    Trophy(APP, "home_assistant", "🏠", "Smart Home", "Einen Home-Assistant-Token erzeugt.", "api_tokens", 1),
    Trophy(APP, "feedback", "💡", "Mitgestalter", "Eine Idee oder einen Fehler gemeldet.", "feedback", 1),
    *_series(APP, "templates", (
        (1, "🍲", "Hausrezept", "Dein erstes Gericht gespeichert."),
        (5, "📖", "Kochbuch", "5 Gerichte gespeichert."),
    )),
    *_series(APP, "custom_foods", (
        (1, "🧾", "Etikettenleser", "Ein Lebensmittel in die Bissfest-Datenbank eingetragen."),
        (10, "🗄️", "Archivar", "10 Lebensmittel in die Bissfest-Datenbank eingetragen."),
        (50, "🏗️", "Datenbank-Baumeister", "50 Lebensmittel in die Bissfest-Datenbank eingetragen."),
    )),
    *_series(APP, "portions", (
        (1, "🥄", "Abgemessen", "Eine Portion gespeichert."),
        (10, "⚗️", "Portionsprofi", "10 Portionen gespeichert."),
    )),
    Trophy(SECRET, "early_bird", "🐦", "Früher Vogel", "Vor 7 Uhr etwas eingetragen.", "early_entries", 1, secret=True),
    Trophy(SECRET, "night_owl", "🦉", "Nachteule", "Nach 23 Uhr etwas eingetragen.", "late_entries", 1, secret=True),
    Trophy(SECRET, "weekend", "🛋️", "Wochenend-Held", "Samstag und Sonntag derselben Woche getrackt.", "weekends", 1, secret=True),
    Trophy(SECRET, "weekends_10", "🏖️", "Wochenend-Profi", "An 10 Wochenenden komplett getrackt.", "weekends", 10, secret=True),
    Trophy(SECRET, "new_year", "🎆", "Guter Vorsatz", "An Neujahr getrackt.", "new_year", 1, secret=True),
    Trophy(SECRET, "christmas", "🎄", "Fest im Biss", "An Weihnachten getrackt.", "christmas", 1, secret=True),
    Trophy(SECRET, "halloween", "🎃", "Süßes oder Saures", "An Halloween getrackt.", "halloween", 1, secret=True),
    Trophy(SECRET, "friday_13", "🐈‍⬛", "Kein Aberglaube", "An einem Freitag, dem 13., getrackt.", "friday_13", 1, secret=True),
    Trophy(SECRET, "leap_day", "🐸", "Schaltjahr-Springer", "Am 29. Februar getrackt.", "leap_day", 1, secret=True),
    Trophy(META, "collector_10", "🥉", "Sammler", "10 Trophäen verdient.", "trophies", 10),
    Trophy(META, "collector_25", "🥈", "Vitrine", "25 Trophäen verdient.", "trophies", 25),
    Trophy(META, "collector_50", "🥇", "Trophäenschrank", "50 Trophäen verdient.", "trophies", 50),
    Trophy(META, "collector_75", "🏅", "Ruhmeshalle", "75 Trophäen verdient.", "trophies", 75),
]
CATEGORIES = list(dict.fromkeys(t.category for t in CATALOG))


def _best_streak(days) -> int:
    best = current = 0
    previous = None
    for day in sorted(days):
        current = current + 1 if previous and day - previous == timedelta(days=1) else 1
        best, previous = max(best, current), day
    return best


def _count(conn: sqlite3.Connection, sql: str, *params) -> int:
    return conn.execute(sql, params).fetchone()[0] or 0


def collect_stats(conn: sqlite3.Connection, u: sqlite3.Row, today: date) -> dict:
    uid, today_iso = u["id"], today.isoformat()
    goal, water_goal, meals_goal = u["kcal_goal"], u["water_goal_ml"], u["meals_per_day"]
    food = conn.execute(
        """SELECT day, COUNT(DISTINCT meal) AS meals, SUM(kcal_100g * grams / 100) AS kcal,
           SUM(COALESCE(protein_100g, 0) * grams / 100) AS protein, SUM(COALESCE(sugar_100g, 0) * grams / 100) AS sugar,
           SUM(sugar_100g IS NULL) AS sugar_unknown FROM food_entries WHERE user_id=? AND day<=? GROUP BY day""",
        (uid, today_iso)).fetchall()
    food_days = {date.fromisoformat(r["day"]): r for r in food}
    done = {d: r for d, r in food_days.items() if d < today}  # der heutige Tag läuft noch
    # Mit Sport verdiente Kalorien erhöhen das Tagesziel
    sport = {date.fromisoformat(r["day"]): r["kcal"] for r in conn.execute(
        "SELECT day, SUM(kcal) AS kcal FROM sport_entries WHERE user_id=? GROUP BY day", (uid,))} if u["sport_enabled"] else {}
    day_goal = lambda d: goal + sport.get(d, 0)  # noqa: E731
    goal_days = {d for d, r in done.items() if GOAL_MIN * day_goal(d) <= r["kcal"] <= GOAL_MAX * day_goal(d)}
    all_meals = {d for d, r in food_days.items() if r["meals"] >= meals_goal}

    water = {date.fromisoformat(r["day"]): r["ml"] for r in conn.execute(
        "SELECT day, SUM(ml) AS ml FROM water_entries WHERE user_id=? AND day<=? GROUP BY day", (uid, today_iso))}
    water_days = {d for d, ml in water.items() if ml >= water_goal}

    weights = [(date.fromisoformat(r["day"]), r["kg"]) for r in conn.execute(
        "SELECT day, kg FROM weights WHERE user_id=? AND day<=? ORDER BY day", (uid, today_iso))]
    weigh_days = {d for d, _ in weights}
    start = weights[0][1] if weights else 0
    lowest = min((kg for _, kg in weights), default=0)
    lost = round(start - lowest, 1) if weights else 0

    target = u["target_weight"]
    target_percent = target_hold = 0
    if weights and target and start > target:
        target_percent = min(100, (start - lowest) / (start - target) * 100)
        reached = next((i for i, (_, kg) in enumerate(weights) if kg <= target), None)
        if reached is not None:
            target_hold = sum(1 for _, kg in weights[reached + 1:] if kg <= target + 1)

    ordered = sorted(food_days)
    gaps = sum(1 for a, b in zip(ordered, ordered[1:]) if (b - a).days >= COMEBACK_GAP_DAYS)
    bounce = sum(1 for d, r in done.items() if r["kcal"] > OVER_GOAL * day_goal(d) and d + timedelta(days=1) in goal_days)
    weekends = sum(1 for d in food_days if d.weekday() == 5 and d + timedelta(days=1) in food_days)
    hours = conn.execute(
        """SELECT SUM(h BETWEEN 4 AND 6) AS early, SUM(h >= 23 OR h < 4) AS late FROM (
           SELECT CAST(strftime('%H', created, 'unixepoch', 'localtime') AS INTEGER) AS h
           FROM food_entries WHERE user_id=?)""", (uid,)).fetchone()
    drinks = dict(conn.execute("SELECT kind, COUNT(*) FROM coffee_entries WHERE user_id=? GROUP BY kind", (uid,)).fetchall())

    return {
        "entries": _count(conn, "SELECT COUNT(*) FROM food_entries WHERE user_id=?", uid),
        "unique_foods": _count(conn, "SELECT COUNT(DISTINCT LOWER(name)) FROM food_entries WHERE user_id=?", uid),
        "days_done": len(done), "streak": _best_streak(food_days), "comebacks": gaps, "bounce_backs": bounce,
        "lost_kg": lost, "lost_percent": lost / start * 100 if start else 0,
        "target_percent": target_percent, "target_hold": target_hold,
        "weigh_days": len(weigh_days), "weigh_streak": _best_streak(weigh_days),
        "water_days": len(water_days), "water_streak": _best_streak(water_days),
        "water_liters": sum(ml for ml in water.values() if ml > 0) / 1000,
        "goal_days": len(goal_days), "goal_streak": _best_streak(goal_days),
        "perfect_days": len(goal_days & water_days & weigh_days & all_meals),
        "all_meals_days": len(all_meals),
        "protein_days": sum(1 for r in food if r["protein"] >= PROTEIN_G),
        "low_sugar_days": sum(1 for d in goal_days if not done[d]["sugar_unknown"] and done[d]["sugar"] <= LOW_SUGAR_G),
        "coffee": drinks.get("coffee", 0), "tea": drinks.get("tea", 0),
        "body_data": int(all(u[k] for k in ("sex", "birth_year", "height_cm", "activity"))),
        "target_set": int(bool(target)), "avatar": int(bool(u["avatar_version"])),
        "reminders": int(u["reminders"] not in (None, "", "{}")),
        "push": _count(conn, "SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?", uid),
        "api_tokens": _count(conn, "SELECT COUNT(*) FROM api_tokens WHERE user_id=?", uid),
        "feedback": _count(conn, "SELECT COUNT(*) FROM feedback WHERE user_id=?", uid),
        "templates": _count(conn, "SELECT COUNT(*) FROM meal_templates WHERE user_id=?", uid),
        "custom_foods": _count(conn, "SELECT COUNT(*) FROM custom_foods WHERE created_by=?", uid),
        "portions": _count(conn, "SELECT COUNT(*) FROM food_portions WHERE created_by=?", uid),
        "early_entries": hours["early"] or 0, "late_entries": hours["late"] or 0, "weekends": weekends,
        "new_year": int(any((d.month, d.day) == (1, 1) for d in food_days)),
        "christmas": int(any(d.month == 12 and d.day in (24, 25, 26) for d in food_days)),
        "halloween": int(any((d.month, d.day) == (10, 31) for d in food_days)),
        "friday_13": int(any(d.day == 13 and d.weekday() == 4 for d in food_days)),
        "leap_day": int(any((d.month, d.day) == (2, 29) for d in food_days)),
    }


def evaluate(conn: sqlite3.Connection, u: sqlite3.Row, today: date | None = None) -> dict:
    """Vergibt neu erreichte Trophäen und liefert die Liste für die App."""
    stats = collect_stats(conn, u, today or date.today())
    earned = {r["key"]: r["earned"] for r in conn.execute("SELECT key, earned FROM user_trophies WHERE user_id=?", (u["id"],))}
    now, new = int(time.time()), []

    def award(trophies):
        for t in trophies:
            if t.key not in earned and stats[t.metric] >= t.target:
                earned[t.key] = now
                # OR IGNORE: Zwei Geräte können gleichzeitig prüfen, gemeldet wird die Trophäe nur einmal
                if conn.execute("INSERT OR IGNORE INTO user_trophies (user_id, key, earned) VALUES (?,?,?)",
                                (u["id"], t.key, now)).rowcount:
                    new.append(t)

    award(t for t in CATALOG if t.metric != "trophies")
    stats["trophies"] = len(earned)
    award(t for t in CATALOG if t.metric == "trophies")
    stats["trophies"] = len(earned)

    visible = [t for t in CATALOG if t.key in earned or not t.requires or u[t.requires]]
    items = []
    for t in visible:
        done = t.key in earned
        hidden = t.secret and not done
        items.append({
            "key": t.key, "category": t.category, "icon": "❔" if hidden else t.icon,
            "name": "Geheime Trophäe" if hidden else t.name,
            "description": "Lass dich überraschen." if hidden else t.description,
            "earned": earned.get(t.key), "secret": hidden,
            "value": None if hidden else min(stats[t.metric], t.target), "target": None if hidden else t.target,
        })
    return {"trophies": items, "categories": CATEGORIES, "earned": sum(1 for t in visible if t.key in earned),
            "total": len(visible), "new": [{"icon": t.icon, "name": t.name, "description": t.description} for t in new]}


def catalog_text() -> str:
    """Alle Trophäen als Text, für docs/trophaeen.txt."""
    lines = [f"Bissfest – alle {len(CATALOG)} Trophäen", "=" * 40, ""]
    lines += ["Einmal verdient, bleibt eine Trophäe erhalten. Geheime Trophäen zeigt die App erst, wenn sie verdient sind.",
              "Kaffee- und Tee-Trophäen erscheinen nur, wenn der jeweilige Tracker eingeschaltet ist.",
              f"Ein Tag zählt als „nah am Kalorienziel“ bei {GOAL_MIN:.0%} bis {GOAL_MAX:.0%} des Ziels, inklusive Sport-Kalorien. Der laufende Tag zählt dafür noch nicht.", ""]
    for category in CATEGORIES:
        trophies = [t for t in CATALOG if t.category == category]
        lines += [f"{category} ({len(trophies)})", "-" * (len(category) + 4)]
        lines += [f"{t.icon} {t.name}: {t.description}" for t in trophies]
        lines.append("")
    return "\n".join(lines)
