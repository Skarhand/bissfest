# Bissfest

**Mit Biss zum Wunschgewicht.**

Abnehmen klappt nicht mit Verzicht, sondern mit Dranbleiben. Bissfest ist ein selbst gehosteter
Ernährungs-Tracker für die ganze Familie: Mahlzeiten mit Kalorien aus öffentlichen Lebensmittel-Datenbanken,
Wasser, tägliches Gewicht, persönliches Kaloriendefizit und Erinnerungen zu den Mahlzeiten.

Aktuelle Version: siehe `APP_VERSION` in `app/main.py` (wird unten auf dem Tagesbildschirm angezeigt).

Selbst gehostete Web-App (PWA) zum Abnehmen – läuft im Browser auf iPhone, Android und Desktop.

**Funktionen**

- Mehrere Benutzer mit eigenem Login (Passwörter gehasht, Sitzungs-Cookie)
- 3–7 Mahlzeiten pro Tag, Anzahl und Namen frei wählbar
- Lebensmittelsuche in **Open Food Facts** (inkl. Barcode) und **USDA FoodData Central**
- Kalorien und Makros (Eiweiß, Kohlenhydrate, Fett) werden pro Tag summiert, mit Tagesziel
- Wasser-Tracker mit Tagesziel
- Tägliches Gewicht mit Verlaufsdiagramm und Zielgewicht
- „Zuletzt gegessen“ für schnelles Wiederholen, manuelle Einträge
- Alles in einer SQLite-Datei unter `./data`

## Starten

```bash
git clone https://github.com/Skarhand/bissfest.git
```

```bash
cd bissfest && docker compose up -d --build
```

Dann `http://<server-ip>:7854` öffnen. Für neue Konten braucht Bissfest einen Mailzugang, siehe
[Registrierung mit E-Mail-Bestätigung](#registrierung-mit-e-mail-bestätigung).

Eigene Einstellungen gehören in eine `docker-compose.override.yml` neben der `docker-compose.yml`. Compose liest sie
automatisch ein, und sie bleibt bei Updates unberührt:

```yaml
services:
  abnehm-app:
    environment:
      PUBLIC_URL: "https://bissfest.example.de"
      ADMIN_USERS: "dein-benutzername"
      COOKIE_SECURE: "true"
      VAPID_SUBJECT: "mailto:du@example.de"
```

## Auf dem Handy installieren

- **iPhone (Safari):** Teilen → „Zum Home-Bildschirm“
- **Android (Chrome):** Menü ⋮ → „App installieren“ / „Zum Startbildschirm hinzufügen“

## HTTPS (empfohlen)

Für Logins übers Internet und den Kamera-Barcode-Scanner wird HTTPS benötigt. Am einfachsten
einen Reverse Proxy (Caddy, Nginx Proxy Manager, Traefik) vor Port 7854 setzen und danach
`COOKIE_SECURE: "true"` setzen.

Beispiel Caddy:

```
bissfest.example.de {
    reverse_proxy abnehm-app:8000
}
```

## Einstellungen (Umgebungsvariablen)

| Variable                                  | Standard                        | Bedeutung                                                                                                                                                |
|-------------------------------------------|---------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `ALLOW_REGISTRATION`                      | `true`                          | Neue Konten erlauben. Nach dem Einrichten auf `false` stellen.                                                                                           |
| `COOKIE_SECURE`                           | `false`                         | `true` hinter HTTPS                                                                                                                                      |
| `FDC_API_KEY`                             | `DEMO_KEY`                      | USDA-Key ([kostenlos](https://fdc.nal.usda.gov/api-key-signup), 1000 Anfragen/Std.). `DEMO_KEY` erlaubt nur ~10 Suchen pro Stunde. Leer = USDA-Suche aus |
| `VAPID_SUBJECT`                           | `mailto:abnehm-app@example.org` | Kontaktadresse für Push-Dienste                                                                                                                          |
| `SOURCE_URL`                              | –                               | Link zum Quellcode in den Einstellungen (Pflicht bei geänderten Fassungen, AGPL § 13)                                                                    |
| `ANDROID_PACKAGE` / `ANDROID_CERT_SHA256` | offizielle App                  | Nur für selbst signierte APKs ändern (assetlinks.json)                                                                                                   |
| `ADMIN_USERS`                             | –                               | Benutzernamen mit Zugriff auf `/admin` (kommagetrennt)                                                                                                   |
| `MAX_USERS`                               | `0`                             | Höchstzahl an Konten, 0 = unbegrenzt (im Admin-Dashboard änderbar)                                                                                       |
| `BACKUP_TIME`                             | `03:00`                         | Uhrzeit der nächtlichen Sicherung, leer = aus                                                                                                            |
| `BACKUP_KEEP_DAYS`                        | `30`                            | Aufbewahrungsdauer der Backups                                                                                                                           |
| `OFF_COUNTRY`                             | `de`                            | Länder-Subdomain für die Open-Food-Facts-Fallback-Suche                                                                                                  |

## Lebensmittel per Tabelle importieren

Unter **Lebensmittel hinzufügen → Neu → Mehrere per Tabelle importieren** gibt es eine Excel-Vorlage (.xlsx) mit Name,
Hersteller, Marke, Barcode, Nährwerten pro 100 g und pro Portion sowie der Portion (Größe, Einheit, Gewicht).
Vor der Übernahme zeigt Bissfest eine Vorschau mit Fehlern. Vorhandene Lebensmittel werden nie überschrieben, fehlende Portionen aber ergänzt.
Bei TL und EL ohne Gewicht nutzt Bissfest für bekannte Lebensmittel Richtwerte für gestrichene Löffel.

## Trophäen

Unter **Einstellungen → Trophäen** sammelt jeder Nutzer Auszeichnungen, die Liste steht in [docs/trophaeen.txt](docs/trophaeen.txt).
Einmal verdient, bleiben sie erhalten. Bewusst gibt es keine Trophäen für besonders wenig Essen oder schnelles Abnehmen.
Neue Trophäen im Katalog in `app/trophies.py` eintragen und die Liste neu erzeugen:

```bash
cd app && python -c "import trophies; print(trophies.catalog_text())" > ../docs/trophaeen.txt
```

## Admin-Dashboard

Unter `/admin` (z. B. https://bissfest.example.de/admin) für alle Benutzer aus `ADMIN_USERS`
(kommagetrennt). Anmeldung mit dem normalen Bissfest-Konto.

- **Übersicht:** Nutzer, Aktivität, Einträge, offenes Feedback, Diagramm der letzten 30/90 Tage
- **Nutzer:** Konten mit letzter Aktivität; Bestätigungsmail erneut senden, manuell bestätigen, löschen
- **Feedback:** Ideen und Bug Reports mit Screenshot und technischen Angaben, als erledigt markieren, antworten
- **Lebensmittel:** gemeinsame Bissfest-Datenbank mit Nutzungshäufigkeit, Einträge löschen
- **System:** Backup-, Mail-, Speicher- und Push-Status; Registrierung und Konten-Limit ohne Neustart ändern

Aus Datenschutzgründen zeigt das Dashboard keine Mahlzeiten, Gewichte oder Körperdaten anderer Nutzer.

## Home Assistant

Bissfest hat eine REST-API (`/api/v1`) für Home Assistant und andere Werkzeuge. Token erzeugen unter
**Einstellungen → Extras → 🏠 Home Assistant**, Details in [docs/home-assistant-api.md](docs/home-assistant-api.md).
Die passende Integration hat ein eigenes Repository: **bissfest-homeassistant**
(https://github.com/Skarhand/bissfest-homeassistant, installierbar über HACS).

## Android-App (APK)

Die offizielle Android-App (Trusted Web Activity) funktioniert mit **jedem Bissfest-Server**: Beim ersten Start fragt
sie nach der Server-Adresse (HTTPS), prüft sie und öffnet Bissfest danach im Vollbild. Wechseln lässt sich der Server
über Einstellungen → „Server wechseln“ oder lange auf das App-Symbol tippen. Die iOS-Web-App läuft ohnehin auf jedem Server.

Jeder Server liefert dafür `/.well-known/assetlinks.json` mit Paketname und Zertifikat der offiziellen App aus
(Standardwerte von `ANDROID_PACKAGE`/`ANDROID_CERT_SHA256`). In den Einstellungen sehen Android-Nutzer einen
Download-Knopf; ohne eigene Datei zeigt er auf die offizielle APK. Web-Updates kommen automatisch in der App an.

Eigene APK bauen (JDK 17, Android SDK mit `platforms;android-36` und `build-tools;36.1.0`):

```bash
cd android && npm install && npm run build      # -> android/dist/bissfest-<version>.apk
```

Der Signaturschlüssel liegt **außerhalb des Repos** in `../android-signing/`. Version und `VERSION_CODE` stehen in
`android/build-apk.mjs`, der Startbildschirm in `android/overlay/SetupActivity.java`. Eine eigene APK als
`data/downloads/bissfest.apk` ablegen; wer selbst signiert, setzt `ANDROID_PACKAGE` und `ANDROID_CERT_SHA256`.

## Registrierung mit E-Mail-Bestätigung

Neue Konten brauchen eine E-Mail-Adresse. Bissfest schickt einen Bestätigungslink (24 Stunden gültig);
erst danach ist die Anmeldung möglich. Nie bestätigte Konten werden nach 7 Tagen gelöscht. Konten von vor
Version 1.11 gelten als bestätigt.

Für den Versand `.env.example` als `.env` neben die `docker-compose.yml` kopieren, SMTP-Daten eintragen,
`chmod 600 .env` und `docker compose up -d`. Ohne SMTP-Daten ist die Registrierung automatisch gesperrt.

## Erinnerungen (Push-Nachrichten)

In den Einstellungen bekommt jede Mahlzeit eine optionale Uhrzeit. Zu dieser Zeit schickt die App eine
Push-Nachricht – aber nur, wenn für diese Mahlzeit heute noch nichts eingetragen ist.
Pro Gerät einmal unter Einstellungen → „Erinnerungen“ → „Auf diesem Gerät aktivieren“ tippen.

Voraussetzungen:

- **HTTPS** – über `http://<ip>:7854` erlauben Browser keine Push-Nachrichten.
- **iPhone/iPad:** iOS 16.4 oder neuer, App über Safari → Teilen → „Zum Home-Bildschirm“ installieren und von dort öffnen.
- **Android:** Chrome/Edge/Firefox, keine Installation nötig (empfohlen aber trotzdem).

Der Server erzeugt beim ersten Start ein Schlüsselpaar (`data/vapid_private.pem`). Diese Datei nicht
löschen, sonst müssen alle Geräte Erinnerungen neu aktivieren. `VAPID_SUBJECT` sollte eine echte
Kontaktadresse sein (`mailto:…`).

## Backup

Die App sichert sich jede Nacht selbst (Standard 03:00 Uhr, `BACKUP_TIME`) in den
Ordner `./backup`. Jede Sicherung ist eine Datei
`abnehm-backup-JJJJ-MM-TT_HHMM.tar.gz` mit:

- `abnehm.db` – komplette Datenbank (alle Konten inkl. Logins, für eine Wiederherstellung)
- `benutzer/<name>.json` – lesbarer Export je Benutzer: Einstellungen, Gewichtsverlauf, Mahlzeiten, Wasser
- `vapid_private.pem` – Schlüssel für Push-Nachrichten (beim Wiederherstellen nach `data/` kopieren)

Backups älter als `BACKUP_KEEP_DAYS` (Standard 30) werden gelöscht. `letztes-backup.txt` zeigt die letzte
erfolgreiche Sicherung.

**Schutz vor falschem Ziel:** Gesichert wird nur, wenn im Backup-Ordner die Datei `.abnehm-backup-ziel`
liegt. Ist ein Netzlaufwerk nicht gemountet, fehlt sie – dann schreibt die App nichts und meldet einen
Fehler im Log (`docker logs abnehm-app`). Ohne diese Datei läuft also auch kein Backup. Einmalig anlegen,
bei einem Netzlaufwerk erst nach dem Mounten:

```bash
touch backup/.abnehm-backup-ziel
```

Sofort-Backup: `docker exec abnehm-app python backup.py`

Für ein anderes Ziel in der `docker-compose.override.yml` einen weiteren Ordner einbinden und `BACKUP_DIR` darauf setzen.

**Wiederherstellen:**

```bash
docker compose stop
tar -xzf backup/abnehm-backup-<datum>.tar.gz -C /tmp abnehm.db
rm -f data/abnehm.db-wal data/abnehm.db-shm && cp /tmp/abnehm.db data/abnehm.db
docker compose start
```

## Hinweise

- Barcode-Scanner: Android/Chrome nutzt den eingebauten `BarcodeDetector`, alle anderen (iPhone) die Bibliothek ZXing 0.21.3 (Apache-2.0, liegt lokal unter `app/static/vendor/`). Die Kamera braucht HTTPS.
- Nährwertdaten: Open Food Facts (ODbL), USDA FoodData Central (Public Domain).

## Lizenz

Bissfest ist freie Software unter der **GNU AGPL-3.0** ([LICENSE](LICENSE)). Name, Logo und App-Icon sind
davon ausgenommen; geänderte Fassungen brauchen einen eigenen Namen. Der Urheberhinweis muss erhalten bleiben.
Spendenknopf und Feedback-Adresse sind fest im Code hinterlegt. Details und fremde Bestandteile: [NOTICE.md](NOTICE.md).

## Versionen

- **2.10.1** – Trophäen als Bild und Text über das Teilen-Menü des Geräts teilen
- **2.10.0** – Trophäen für Dranbleiben, Abnehmen, Wasser, Kalorienziel und mehr, inklusive geheimer Trophäen
- **2.9.3** – Richtwerte für TL und EL bei Zucker, Öl, Mehl und Co., auch im Tabellen-Import
- **2.9.2** – Erneuter Tabellen-Import ergänzt fehlende Portionen bei vorhandenen Lebensmitteln
- **2.9.1** – Tabellen-Import liest auch .xlsx-Dateien aus anderen Tabellen-Apps
- **2.9.0** – Nährwertvorlage: mehrere Lebensmittel per Excel-Tabelle in die Bissfest-Datenbank importieren
- **2.8.3** – Veröffentlichung auf GitHub: neutrale `docker-compose.yml`, Link zum Quellcode in den Einstellungen
- **2.8.2** – Profilbild ändern und löschen im eigenen Untermenü, oben im Profil nur noch Bild und Name
- **2.8.1** – Profilbild: Selfie direkt mit der Frontkamera oder Bild aus der Galerie
- **2.8.0** – Profilbild (nur für den Nutzer selbst sichtbar), Kopfbereich im Profil
- **2.7.6** – „Angemeldet als“ und Abmelden im Untermenü „Profil“
- **2.7.5** – Hinweiszeilen unten in den Einstellungen zentriert
- **2.7.4** – Link zum Admin-Dashboard im Untermenü „Profil“
- **2.7.3** – Einstellungen aufgeräumt: Ziele, Erinnerungen und Extras als Untermenüs, Home Assistant unter Extras
- **2.7.2** – E-Mail-Adresse und Passwort im Untermenü „Profil“ der Einstellungen
- **2.7.1** – Wasser-Knöpfe: +200 ml statt +250 ml, „−“ zieht 200 ml ab

- **2.7.0** – Höchstzahl an Konten (`MAX_USERS`, im Admin-Dashboard änderbar)
- **2.6.0** – Passwort vergessen (Link per Mail) und E-Mail-Adresse ändern mit Bestätigung
- **2.5.0** – Android-App fragt beim ersten Start nach dem Server und läuft mit jeder Bissfest-Installation
- **2.4.3** – Lizenz AGPL-3.0, NOTICE.md, Spendenlink und Feedback-Adresse fest im Code, Quellcode-Link (`SOURCE_URL`)
- **2.4.2** – App-Version auf der Anmeldeseite
- **2.4.1** – Tippfehler in der E-Mail-Adresse lässt sich vor der Bestätigung korrigieren
- **2.4.0** – Einrichtungs-Assistent für das persönliche Kalorienziel, Hinweis solange nur Beispielwerte gesetzt sind
- **2.3.0** – Admin-Dashboard unter /admin (Übersicht, Nutzer, Feedback, Lebensmittel, System, Registrierungsschalter)
- **2.2.0** – „Fehler melden“: Bug Report mit Screenshot und technischen Angaben per Mail
- **2.1.0** – REST-API `/api/v1` mit persönlichen Tokens für Home Assistant
- **2.0.0** – Echte Android-App (APK, Trusted Web Activity), Download in der App, assetlinks.json
- **1.17.0** – Feature Request aus der App per Mail an service@facgooder.de (zusätzlich gespeichert)
- **1.16.1** – Spenden nur noch per Wero (PayPal entfernt)
- **1.16.0** – „Spendier mir einen Kaffee“ mit Wero (Link + QR-Code), PayPal vorbereitet
- **1.15.0** – Tee-Tracker (zuschaltbar wie Kaffee), Koffein aus Kaffee und Tee gemeinsam gegen 400 mg
- **1.14.0** – Kaffee-Tracker: Tasse (150 ml) oder Pott (250 ml), Koffein nach Menge, letzten Kaffee zurücknehmen
- **1.13.0** – Kaffee-Tracker als zuschaltbares Extra (Tassen, Koffein, Verlauf)
- **1.12.0** – Portionen statt Gramm (z. B. „1 × Scheibe“): eigene, aus den Datenbanken und typische Vorschläge
- **1.11.1** – Copyright-Hinweis
- **1.11.0** – Registrierung mit E-Mail-Bestätigung (SMTP über `.env`)
- **1.10.0** – Neues Logo und App-Icon, Markenfarben, Betrieb unter https://bissfest.facgooder.de
- **1.9.0** – Barcode-Scanner mit Kamera für iPhone und Android (ZXing als Fallback)
- **1.8.0** – Gemeinsame Bissfest-Lebensmitteldatenbank (Hersteller, Marke, Zucker), gespeicherte Gerichte
- **1.7.0** – Feste Mahlzeiten (Frühstück, Mittagessen, Abendessen, Snack 1–4) mit festen Push-Texten
- **1.6.0** – Körperdaten & Kaloriendefizit als eigenes Untermenü in den Einstellungen
- **1.5.0** – Darstellung wählbar: Hell, Dunkel oder System
- **1.4.0** – Neuer Name: Bissfest – „Mit Biss zum Wunschgewicht“
- **1.3.0** – Push-Erinnerungen zu den Mahlzeiten, Versionsanzeige
- **1.2.0** – Kaloriendefizit-Rechner (Grund-/Gesamtumsatz), Defizit in Tagesansicht und Verlauf
- **1.1.0** – Nächtliches Backup aufs Netzlaufwerk
- **1.0.0** – Erste Version: Mehrbenutzer, Mahlzeiten, Lebensmittelsuche, Wasser, Gewicht

---

© 2026 Sebastian Frie