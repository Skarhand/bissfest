# Bissfest-API für Home Assistant (v1)

Ab Bissfest 2.1.0. Gedacht für die Home-Assistant-Integration (`custom_components/bissfest`), funktioniert aber
mit jedem HTTP-Client.

## Anmeldung
In Bissfest unter **Einstellungen → Extras → 🏠 Home Assistant** einen Token erzeugen. Er wird nur einmal angezeigt
(Format `bf_…`), lässt sich dort jederzeit widerrufen und gilt nur für das eigene Konto.

```
Authorization: Bearer bf_xxxxxxxxxxxxxxxxxxxxxxxx
```

Fehler kommen als JSON `{"detail": "…"}`:

| Status | Bedeutung |
|---|---|
| 401 | Token fehlt, ist ungültig oder widerrufen |
| 422 | Ungültige Eingabe |
| 429 | Mehr als 60 Anfragen pro Minute mit diesem Token |

## Lesen

`GET /api/v1/status` – optional `?date=YYYY-MM-DD` (Standard: heute)

```json
{
  "app_version": "2.1.0", "user": "sebastian", "date": "2026-11-04",
  "calories": {"eaten": 1320, "goal": 1930, "sport": 200, "remaining": 810, "burned_estimate": 2605, "deficit": 1285},
  "nutrients": {"protein": 76.0, "carbs": 87.0, "sugar": 30.0, "fat": 69.0},
  "meals": [{"index": 0, "name": "Frühstück", "kcal": 456, "entries": 5, "reminder": "09:10"}],
  "water": {"ml": 1500, "goal_ml": 2000, "percent": 75},
  "weight": {"today_kg": 77.2, "latest_kg": 77.2, "latest_date": "2026-11-04", "target_kg": 75.0, "change_30d_kg": -2.8},
  "coffee": {"enabled": true, "cups": 1, "mugs": 2, "espressos": 1, "ml": 680, "caffeine_mg": 455},
  "tea": {"enabled": false, "cups": 0, "mugs": 0, "espressos": 0, "ml": 0, "caffeine_mg": 0},
  "caffeine_total_mg": 455
}
```

- `burned_estimate` (Gesamtumsatz) und `deficit` sind `null`, solange die Körperdaten fehlen.
- `sport` sind die mit Sport verdienten Kalorien (ab Bissfest 2.13.0). Sie stecken schon in `remaining`, `burned_estimate` und `deficit`.
- Alle `weight`-Werte, `target_kg`, `change_30d_kg` und `reminder` können `null` sein.
- `meals` hat 3–7 Einträge (Frühstück, Mittagessen, Abendessen, Snack 1–4).
- `coffee`/`tea`.`enabled` zeigt, ob der Nutzer das Extra eingeschaltet hat.

## Schreiben
Alle Schreib-Endpunkte antworten mit dem vollständigen Status wie oben.

| Methode & Pfad | Body | Wirkung |
|---|---|---|
| `POST /api/v1/water` | `{"ml": 250}` | Wasser eintragen (−3000…3000, negativ zieht ab, nie unter 0) |
| `POST /api/v1/weight` | `{"kg": 77.2, "date": "2026-11-04"}` | Gewicht setzen (20…400 kg, `date` optional) |
| `POST /api/v1/drink` | `{"kind": "coffee", "size": "mug"}` | Kaffee/Tee: `cup` = 150 ml, `mug` = 250 ml, nur Kaffee (ab Bissfest 2.12.0): `espresso` = 30 ml |
| `DELETE /api/v1/drink/last?kind=tea` | – | Letzten Kaffee/Tee des Tages zurücknehmen |

## Beispiel
```bash
curl -H "Authorization: Bearer $BISSFEST_TOKEN" https://bissfest.example.de/api/v1/status
curl -X POST -H "Authorization: Bearer $BISSFEST_TOKEN" -H "Content-Type: application/json" \
     -d '{"ml": 250}' https://bissfest.example.de/api/v1/water
```
