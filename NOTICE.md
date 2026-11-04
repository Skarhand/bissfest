# Hinweise zu Bissfest

Copyright © 2026 Sebastian Frie

Bissfest ist freie Software: Du darfst sie unter den Bedingungen der
**GNU Affero General Public License, Version 3** (siehe [LICENSE](LICENSE)) nutzen, weitergeben und ändern.
Bissfest wird ohne jede Gewährleistung bereitgestellt. Die berechneten Werte sind Schätzungen und ersetzen
keine ärztliche oder ernährungsmedizinische Beratung.

## Zusätzliche Bedingungen nach § 7 der AGPL-3.0

1. **Urheberhinweis (§ 7 b):** Der Hinweis „© Sebastian Frie“ muss in geänderten Fassungen in der
   Benutzeroberfläche sichtbar erhalten bleiben, dort wo die Software ihn anzeigt.
2. **Name und Logo (§ 7 e):** Die Lizenz gewährt keine Rechte am Namen „Bissfest“, am Bissfest-Logo und am
   App-Icon. Geänderte Fassungen müssen unter einem anderen Namen und mit einem anderen Logo veröffentlicht
   werden. Unveränderte Fassungen dürfen Name und Logo weiter verwenden. Die Bezeichnung „basiert auf Bissfest“
   ist zur Herkunftsangabe erlaubt.
3. **Herkunft (§ 7 c):** Geänderte Fassungen müssen als solche gekennzeichnet sein und dürfen nicht den Eindruck
   erwecken, von Sebastian Frie zu stammen.

## Spenden und Feedback

Der Spendenknopf und die Empfängeradresse für Feature Requests und Bug Reports sind fest im Code hinterlegt
und zeigen auf den Autor von Bissfest. Wer Bissfest unverändert betreibt, unterstützt damit das Projekt –
bitte lasse sie bestehen. Bug Reports werden über den Mailzugang der jeweiligen Installation verschickt.

## Quellcode bei Betrieb als Webdienst

Wer eine geänderte Fassung über ein Netzwerk anbietet, muss den Nutzern nach § 13 der AGPL-3.0 den Quellcode
dieser Fassung zugänglich machen. Dafür die Variable `SOURCE_URL` setzen: Der Link erscheint dann in den
Einstellungen der App.

## Fremde Bestandteile und Daten

| Bestandteil | Lizenz | Hinweis |
|---|---|---|
| [ZXing für JavaScript](https://github.com/zxing-js/library) 0.21.3 (`app/static/vendor/`) | Apache-2.0 | Lizenztext: `app/static/vendor/zxing-LICENSE.txt` |
| [FastAPI](https://fastapi.tiangolo.com/) | MIT | Python-Abhängigkeit, siehe `requirements.txt` |
| [Uvicorn](https://www.uvicorn.org/), [HTTPX](https://www.python-httpx.org/) | BSD-3-Clause | Python-Abhängigkeiten, siehe `requirements.txt` |
| [pywebpush](https://github.com/web-push-libs/pywebpush), py-vapid | MPL-2.0 | Python-Abhängigkeiten, siehe `requirements.txt` |
| [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap) | Apache-2.0 | Nur zum Bauen der Android-App |
| [Open Food Facts](https://world.openfoodfacts.org/) | ODbL (Datenbank), DbCL (Inhalte) | Nährwertdaten, Abruf zur Laufzeit |
| [USDA FoodData Central](https://fdc.nal.usda.gov/) | Public Domain (CC0) | Nährwertdaten, Abruf zur Laufzeit |

„Wero“ und das Wero-Logo sind Marken ihres Inhabers und gehören nicht zu Bissfest. Der QR-Code
`app/static/wero-qr.png` ist vom Autor für seinen Spendenlink erzeugt und von der AGPL-Lizenz ausgenommen.
