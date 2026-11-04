"""Konstanten der Bissfest-Integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "bissfest"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 5  # Minuten
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 60

TOKEN_PREFIX: Final = "bf_"

DRINK_KINDS: Final = ("coffee", "tea")
DRINK_SIZES: Final = ("cup", "mug")

# Feste Mahlzeitennamen aus Bissfest → stabiler Schlüssel (unique_id + Übersetzung)
MEAL_KEYS: Final = {
    "Frühstück": "breakfast",
    "Mittagessen": "lunch",
    "Abendessen": "dinner",
    "Snack 1": "snack_1",
    "Snack 2": "snack_2",
    "Snack 3": "snack_3",
    "Snack 4": "snack_4",
}

SERVICE_ADD_WATER: Final = "add_water"
SERVICE_LOG_WEIGHT: Final = "log_weight"
SERVICE_ADD_DRINK: Final = "add_drink"
SERVICE_UNDO_DRINK: Final = "undo_drink"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_ML: Final = "ml"
ATTR_KG: Final = "kg"
ATTR_DATE: Final = "date"
ATTR_KIND: Final = "kind"
ATTR_SIZE: Final = "size"
