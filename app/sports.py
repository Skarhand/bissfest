"""Sportarten mit MET-Werten (Richtwerte nach dem Compendium of Physical Activities).

Gezählt wird nur der Verbrauch über dem Ruheumsatz: (MET - 1) × kg × Stunden. Der Ruheumsatz steckt schon im
Tagesbedarf, sonst würde er doppelt gezählt.
"""

CUSTOM = "custom"

SPORTS = (
    ("walk", "🚶", "Spazieren", 3.5),
    ("walk_fast", "🚶", "Zügiges Gehen", 4.3),
    ("hike", "🥾", "Wandern", 6.0),
    ("jog", "🏃", "Joggen (locker)", 8.0),
    ("run", "🏃", "Laufen (schnell)", 11.0),
    ("cycle", "🚲", "Radfahren (gemütlich)", 6.0),
    ("cycle_fast", "🚴", "Radfahren (zügig)", 8.0),
    ("ergometer", "🚴", "Ergometer", 6.8),
    ("swim", "🏊", "Schwimmen", 6.0),
    ("strength", "🏋️", "Krafttraining", 4.5),
    ("hiit", "🔥", "HIIT / Zirkeltraining", 8.0),
    ("yoga", "🧘", "Yoga", 2.5),
    ("pilates", "🤸", "Pilates", 3.0),
    ("dance", "💃", "Tanzen", 5.0),
    ("football", "⚽", "Fußball", 7.0),
    ("tennis", "🎾", "Tennis", 7.3),
    ("badminton", "🏸", "Badminton", 5.5),
    ("rowing", "🚣", "Rudern", 7.0),
    ("climbing", "🧗", "Klettern", 7.5),
    ("skating", "⛸️", "Inlineskaten / Eislaufen", 7.0),
    ("stairs", "🪜", "Treppensteigen", 8.0),
    ("garden", "🌱", "Gartenarbeit", 3.5),
)
BY_KEY = {key: (icon, name, met) for key, icon, name, met in SPORTS}


def earned_kcal(met: float, kg: float, minutes: int) -> int:
    return round((met - 1) * kg * minutes / 60)
