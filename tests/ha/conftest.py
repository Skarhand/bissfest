"""Gemeinsame Fixtures für die Bissfest-HA-Tests."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, patch
from pathlib import Path
import sys
from typing import Any

import pytest

# Repo-Root auf den Pfad, damit `custom_components.bissfest` importierbar ist
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

# pycares (aiodns, von HAs aiohttp-Session genutzt) startet beim ersten Resolver einen
# globalen Daemon-Thread. Vorab starten, sonst meldet der Thread-Check des ersten Tests ihn.
try:
    import pycares

    pycares._shutdown_manager.start()
except (ImportError, AttributeError):
    pass

URL = "https://bissfest.example.de"
TOKEN = "bf_testtoken"
API = f"{URL}/api/v1"

STATUS: dict[str, Any] = {
    "app_version": "2.1.0",
    "user": "sebastian",
    "date": "2026-11-04",
    "calories": {"eaten": 1320, "goal": 1930, "remaining": 610, "burned_estimate": 2405, "deficit": 1085},
    "nutrients": {"protein": 76.0, "carbs": 87.0, "sugar": 30.0, "fat": 69.0},
    "meals": [
        {"index": 0, "name": "Frühstück", "kcal": 456, "entries": 5, "reminder": "09:10"},
        {"index": 1, "name": "Mittagessen", "kcal": 600, "entries": 2, "reminder": None},
        {"index": 2, "name": "Abendessen", "kcal": 264, "entries": 1, "reminder": None},
        {"index": 3, "name": "Snack 1", "kcal": 0, "entries": 0, "reminder": None},
    ],
    "water": {"ml": 1500, "goal_ml": 2000, "percent": 75},
    "weight": {"today_kg": 77.2, "latest_kg": 77.2, "latest_date": "2026-11-04", "target_kg": 75.0, "change_30d_kg": -2.8},
    "coffee": {"enabled": True, "cups": 1, "mugs": 2, "ml": 650, "caffeine_mg": 390},
    "tea": {"enabled": False, "cups": 0, "mugs": 0, "ml": 0, "caffeine_mg": 0},
    "caffeine_total_mg": 390,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Custom-Integrationen in jedem Test erlauben."""
    yield


@pytest.fixture
def status() -> dict[str, Any]:
    return copy.deepcopy(STATUS)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain="bissfest",
        title="Bissfest – sebastian",
        unique_id=f"{URL}#sebastian",
        data={"url": URL, "token": TOKEN},
    )


@pytest.fixture
def mock_setup_entry():
    """Echtes Setup im Config-Flow-Test überspringen."""
    with patch("custom_components.bissfest.async_setup_entry", AsyncMock(return_value=True)) as mock:
        yield mock
