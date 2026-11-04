# Bissfest für Home Assistant

Custom-Integration, die deine [Bissfest](https://bissfest.facgooder.de)-Abnehm-App mit Home Assistant verbindet: Kalorien, Nährwerte, Wasser, Gewicht, Kaffee und Tee als Sensoren – plus Buttons und Services, um Wasser, Getränke und Gewicht direkt aus Home Assistant einzutragen.

Voraussetzungen:

- Bissfest **2.1.0 oder neuer** (bringt die REST-API `/api/v1` mit)
- Home Assistant **2024.11 oder neuer**

## Installation

### Über HACS (benutzerdefiniertes Repository)

1. In Home Assistant **HACS** öffnen.
2. Oben rechts auf das Drei-Punkte-Menü → **Benutzerdefinierte Repositories**.
3. Die URL dieses Git-Repositorys eintragen, Typ **Integration** wählen, **Hinzufügen**.
4. In HACS nach **Bissfest** suchen → **Herunterladen**.
5. Home Assistant neu starten.

### Manuell

1. Den Ordner `custom_components/bissfest/` aus diesem Repository nach `<HA-Konfiguration>/custom_components/bissfest/` kopieren (dort liegt auch die `configuration.yaml`).
2. Home Assistant neu starten.

## Token erzeugen

1. Bissfest öffnen → **Einstellungen → Home Assistant**.
2. **Token erzeugen** antippen und einen Namen vergeben (z. B. „Home Assistant“).
3. Den angezeigten Token kopieren – er beginnt mit `bf_` und wird nur einmal angezeigt.

Ein Token lässt sich dort jederzeit widerrufen. Home Assistant meldet sich dann mit „Erneut authentifizieren“; einfach einen neuen Token erzeugen und eintragen.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Bissfest**

| Feld | Beispiel |
|---|---|
| Bissfest-URL | `https://bissfest.facgooder.de` |
| API-Token | `bf_…` |

Die Integration prüft den Token sofort per `GET /api/v1/status`. Mehrere Konten (auch auf verschiedenen Bissfest-Servern) sind möglich – jedes wird ein eigenes Gerät „Bissfest – &lt;Benutzer&gt;“.

**Optionen:** Über **Konfigurieren** lässt sich das Abfrageintervall einstellen (Standard 5 Minuten, 1–60). Schreibaktionen (Buttons, Services) aktualisieren die Sensoren sofort, unabhängig vom Intervall. Die API erlaubt 60 Anfragen pro Minute und Token.

## Entitäten

Die Entitäts-IDs richten sich nach der Sprache deiner Home-Assistant-Installation, z. B. `sensor.bissfest_sebastian_kalorien_gegessen` (Deutsch) bzw. `sensor.bissfest_sebastian_calories_eaten` (Englisch).

### Sensoren

| Sensor | Einheit | Hinweis |
|---|---|---|
| Kalorien gegessen | kcal | |
| Kalorienziel | kcal | |
| Kalorien übrig | kcal | |
| Kaloriendefizit | kcal | unbekannt, solange Körperdaten in Bissfest fehlen |
| Grundumsatz-Schätzung | kcal | geschätzter Tagesverbrauch; unbekannt ohne Körperdaten |
| Eiweiß, Kohlenhydrate, Zucker, Fett | g | |
| Wasser | ml | Attribut `goal_ml` |
| Wasser (%) | % | Anteil am Tagesziel |
| Gewicht | kg | letzter Eintrag; Attribute `date`, `today_kg` |
| Gewichtsveränderung 30 Tage | kg | negativ = abgenommen |
| Zielgewicht | kg | |
| Koffein gesamt | mg | Kaffee + Tee |
| Kaffee Tassen / Pötte / Menge / Koffein | – / – / ml / mg | nur wenn der Kaffee-Tracker in Bissfest eingeschaltet ist |
| Tee Tassen / Pötte / Menge / Koffein | – / – / ml / mg | nur wenn der Tee-Tracker eingeschaltet ist |
| Kalorien Frühstück, Mittagessen, Abendessen, Snack 1–4 | kcal | eine pro Mahlzeit in Bissfest; Attribute `entries` (Anzahl Einträge), `reminder` (Erinnerungszeit) |

Schaltest du in Bissfest Kaffee/Tee ein oder aus oder änderst die Zahl der Mahlzeiten, baut die Integration ihre Entitäten beim nächsten Abruf automatisch neu auf.

### Buttons

| Button | Aktion |
|---|---|
| Wasser +250 ml | 250 ml Wasser eintragen |
| Kaffee Tasse / Kaffee Pott | Kaffee eintragen (Tasse 150 ml, Pott 250 ml) |
| Tee Tasse / Tee Pott | Tee eintragen |
| Letzten Kaffee / Tee zurücknehmen | letzten Eintrag des Tages löschen |

Kaffee- und Tee-Buttons gibt es nur, wenn das jeweilige Extra in Bissfest eingeschaltet ist.

## Services

Alle Services haben das optionale Feld `config_entry_id`. Es ist nur nötig, wenn mehrere Bissfest-Konten eingerichtet sind.

| Service | Felder |
|---|---|
| `bissfest.add_water` | `ml` (−3000 bis 3000, negativ = abziehen) |
| `bissfest.log_weight` | `kg` (20 bis 400), optional `date` (Standard heute) |
| `bissfest.add_drink` | `kind` (`coffee`/`tea`), `size` (`cup`/`mug`) |
| `bissfest.undo_drink` | `kind` (`coffee`/`tea`) |

Beispiel:

```yaml
action: bissfest.log_weight
data:
  kg: 77.2
```

## Automations-Beispiele

### Wasser-Erinnerung am Nachmittag

Erinnert um 15 Uhr per Handy-Benachrichtigung, wenn noch nicht die Hälfte des Wasserziels erreicht ist. Mit einer Aktion in der Benachrichtigung trägst du direkt 250 ml ein.

```yaml
alias: Bissfest – Wasser-Erinnerung
triggers:
  - trigger: time
    at: "15:00:00"
conditions:
  - condition: numeric_state
    entity_id: sensor.bissfest_sebastian_wasser_2
    below: 50
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: Trink was! 💧
      message: >
        Erst {{ states('sensor.bissfest_sebastian_wasser') }} ml von
        {{ state_attr('sensor.bissfest_sebastian_wasser', 'goal_ml') }} ml.
      data:
        actions:
          - action: BISSFEST_WATER_250
            title: +250 ml
mode: single
```

```yaml
alias: Bissfest – Wasser aus Benachrichtigung
triggers:
  - trigger: event
    event_type: mobile_app_notification_action
    event_data:
      action: BISSFEST_WATER_250
actions:
  - action: bissfest.add_water
    data:
      ml: 250
mode: queued
```

> Die Entitäts-ID des Prozent-Sensors heißt je nach Sprache `…_wasser_2` bzw. `…_water_2`, weil beide Sensoren „Wasser“ im Namen tragen. Unter **Einstellungen → Entitäten** lässt sie sich umbenennen (z. B. in `sensor.bissfest_wasser_prozent`).

### Kaffee-Button an der Kaffeemaschine

Ein Zigbee-Taster neben der Kaffeemaschine: einmal drücken = Tasse, doppelt = Pott, lange = letzten Kaffee zurücknehmen. Auslöser je nach Taster anpassen (hier ein Beispiel über das `event`-Entity des Tasters).

```yaml
alias: Bissfest – Kaffee-Taster
triggers:
  - trigger: state
    entity_id: event.kaffee_taster_aktion
actions:
  - choose:
      - conditions: "{{ trigger.to_state.attributes.event_type == 'single' }}"
        sequence:
          - action: button.press
            target:
              entity_id: button.bissfest_sebastian_kaffee_tasse
      - conditions: "{{ trigger.to_state.attributes.event_type == 'double' }}"
        sequence:
          - action: button.press
            target:
              entity_id: button.bissfest_sebastian_kaffee_pott
      - conditions: "{{ trigger.to_state.attributes.event_type == 'long' }}"
        sequence:
          - action: button.press
            target:
              entity_id: button.bissfest_sebastian_letzten_kaffee_zurucknehmen
mode: queued
```

### Gewicht von der smarten Waage übernehmen

```yaml
alias: Bissfest – Gewicht von der Waage
triggers:
  - trigger: state
    entity_id: sensor.waage_gewicht
conditions:
  - condition: template
    value_template: "{{ 20 <= trigger.to_state.state | float(0) <= 400 }}"
actions:
  - action: bissfest.log_weight
    data:
      kg: "{{ trigger.to_state.state | float | round(1) }}"
mode: single
```

## Fehlerbehebung

| Meldung | Ursache |
|---|---|
| „Token ungültig oder widerrufen“ | Token in Bissfest widerrufen oder falsch kopiert → neuen Token erzeugen |
| „Keine Bissfest-API unter dieser URL gefunden“ | URL falsch oder Bissfest älter als 2.1.0 |
| „Zu viele Anfragen“ | mehr als 60 Anfragen/Minute – Abfrageintervall erhöhen, Automationen drosseln |
| Sensoren „nicht verfügbar“ | Bissfest war beim letzten Abruf nicht erreichbar; die Integration versucht es beim nächsten Intervall erneut |

## Entwicklung

Tests liegen in `tests/ha/` und nutzen `pytest-homeassistant-custom-component` mit einem gemockten API-Server:

```bash
python3 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
cd tests/ha && ../../.venv/bin/python -m pytest -q
```
