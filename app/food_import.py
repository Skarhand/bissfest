"""Nährwertvorlage: Excel-Tabelle erzeugen und ausgefüllte Tabellen einlesen.

Spalten und Einlesen stehen hier zusammen, damit Vorlage und Import nicht auseinanderlaufen.
"""
import io
import re
import zipfile

from defusedxml import ElementTree
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

SHEET = "Lebensmittel"
HEADER_ROW = 2
FIRST_ROW = 3
MAX_FOODS = 500
MAX_SCAN_ROWS = 2000  # auch Vorlagen mit vielen leeren, formatierten Zeilen schnell abarbeiten
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
EXAMPLE_PREFIX = "beispiel:"
MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

NUTRIENTS = (("kcal", "Brennwert (kcal)"), ("protein", "Eiweiß (g)"), ("fat", "Fett (g)"),
             ("carbs", "Kohlenhydrate (g)"), ("sugar", "davon Zucker (g)"))

# Schlüssel, Überschrift, Spaltenbreite – die Reihenfolge entspricht den Spalten der Tabelle
COLUMNS = (
    ("name", "Lebensmittel *", 30), ("manufacturer", "Hersteller", 18), ("brand", "Marke", 18), ("code", "Barcode", 16),
    *((f"{k}_100g", label + (" *" if k == "kcal" else ""), 13) for k, label in NUTRIENTS),
    ("portion_size", "Portionsgröße", 13), ("portion_unit", "Einheit", 11), ("portion_grams", "Gewicht der Portion (g)", 13),
    *((f"{k}_portion", label, 13) for k, label in NUTRIENTS),
)
GROUPS = (("Lebensmittel", 1, 4, "2E8B5F"), ("Nährwerte pro 100 g", 5, 9, "1F6F8B"),
          ("Portion", 10, 12, "B8742A"), ("Nährwerte pro Portion", 13, 17, "7A4E9E"))
UNIT_CHOICES = ("g", "ml", "Stück", "Scheibe", "TL", "EL", "Becher", "Glas", "Tasse", "Riegel", "Packung", "Portion")
UNIT_ALIASES = {
    "g": "g", "gr": "g", "gramm": "g", "gram": "g", "ml": "ml", "milliliter": "ml",
    "stk": "Stück", "st": "Stück", "stück": "Stück", "stueck": "Stück", "scheibe": "Scheibe", "scheiben": "Scheibe",
    "tl": "TL", "teelöffel": "TL", "el": "EL", "esslöffel": "EL",
}
# Richtwerte für gestrichene Löffel: Muster auf den Namen, Gramm pro TL, Gramm pro EL. Die erste passende Regel gewinnt.
SPOON_WEIGHTS = (
    (r"\böl\b|\w+öl\b", 4.5, 13),
    (r"erdnussbutter|nussnougat|nutella|nusscreme|schokocreme|mandelmus|marmelade|konfitüre|gelee|aufstrich", 8, 22),
    (r"honig|sirup|dicksaft", 7, 20),
    (r"butter\b|margarine", 5, 15),
    (r"zucker\b|\bsalz\b|\w+salz\b", 5, 15),
    (r"mehl\b|stärke\b", 3, 10),
    (r"\bkakao(pulver)?\b", 3, 8),
    (r"haferflocken|müsli|granola|cornflakes", 3, 9),
    (r"milch\b|sahne\b|\bwasser\b", 5, 15),
)
NO_SPOON = r"schokolade|riegel|keks|kuchen|\beis\b|torte"
EXAMPLES = (
    ("Beispiel: Butter", "Edeka", "Gut & Günstig", "", 741, 0.7, 82, 0.6, 0.6, 1, "TL", 5, 37, 0, 4.1, 0, 0),
    ("Beispiel: Toastbrot", "Edeka", "Gut & Günstig", "", 265, 8.5, 3.5, 48, 4.5, 2, "Scheibe", 50, 133, 4.3, 1.8, 24, 2.3),
)
INSTRUCTIONS = (
    "So füllst du die Vorlage aus",
    "",
    "1. Trage im Blatt „Lebensmittel“ pro Zeile ein Lebensmittel ein, höchstens 500 je Datei.",
    "2. Pflicht sind nur die Spalten mit *: der Name und die Kalorien.",
    "3. Nährwerte pro 100 g oder pro Portion reichen. Fehlende 100-g-Werte rechnet Bissfest aus der Portion aus.",
    "4. Stehen beide da, gelten die Werte pro 100 g.",
    "5. Portion: Größe, Einheit und Gewicht beziehen sich auf dieselbe Menge, z. B. 2 | Scheibe | 50 ergibt 1 Scheibe = 25 g.",
    "6. Bei der Einheit g oder ml genügt die Portionsgröße, z. B. 30 | g. ml wird wie Gramm gerechnet.",
    "7. Bei TL und EL ohne Gewicht setzt Bissfest für bekannte Lebensmittel wie Zucker oder Öl einen Richtwert ein.",
    "8. Die Einheit kannst du auswählen oder frei eintippen, z. B. Stk., TL oder Handvoll.",
    "9. Zeilen, die mit „Beispiel:“ beginnen, werden nicht importiert. Du kannst sie stehen lassen.",
    "10. Speichere die Datei als .xlsx und lade sie in Bissfest unter Lebensmittel hinzufügen → Neu → Tabelle importieren hoch.",
    "",
    "Vor dem Import zeigt Bissfest dir, welche Zeilen übernommen werden und wo Fehler stecken.",
    "Importierte Lebensmittel landen in der gemeinsamen Bissfest-Datenbank und sind für alle Nutzer sichtbar.",
)


class ImportFileError(ValueError):
    """Die Datei als Ganzes lässt sich nicht einlesen."""


def spoon_grams(name: str) -> tuple[float, float] | None:
    """Richtwerte (TL, EL) für ein Lebensmittel. Zusätze wie „mit Zucker“ zählen nicht, Kaffee mit Zucker bleibt Kaffee."""
    base = re.split(r"\s(?:mit|ohne)\s", name.lower())[0]
    if re.search(NO_SPOON, base):
        return None
    return next(((tl, el) for pattern, tl, el in SPOON_WEIGHTS if re.search(pattern, base)), None)


def build_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    white_bold = Font(bold=True, color="FFFFFF")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for title, first, last, color in GROUPS:
        ws.merge_cells(start_row=1, start_column=first, end_row=1, end_column=last)
        ws.cell(1, first, title).alignment = center
        ws.cell(1, first).font = white_bold
        for col in range(first, last + 1):
            fill = PatternFill("solid", fgColor=color)
            ws.cell(1, col).fill = fill
            ws.cell(HEADER_ROW, col).fill = fill
    for col, (_, title, width) in enumerate(COLUMNS, 1):
        cell = ws.cell(HEADER_ROW, col, title)
        cell.font, cell.alignment = white_bold, center
        ws.column_dimensions[cell.column_letter].width = width
    ws.row_dimensions[HEADER_ROW].height = 46
    for r, example in enumerate(EXAMPLES, FIRST_ROW):
        for col, value in enumerate(example, 1):
            cell = ws.cell(r, col, value if value != "" else None)
            cell.font = Font(italic=True, color="808080")
    last_row = FIRST_ROW + MAX_FOODS + len(EXAMPLES)
    for r in range(FIRST_ROW, last_row):  # Barcodes als Text, sonst macht Excel daraus 4,01E+12
        ws.cell(r, 4).number_format = "@"
    units = DataValidation(type="list", formula1='"' + ",".join(UNIT_CHOICES) + '"', allow_blank=True, showErrorMessage=False)
    ws.add_data_validation(units)
    units.add(f"K{FIRST_ROW}:K{last_row}")
    ws.freeze_panes = ws.cell(FIRST_ROW, 2)

    help_sheet = wb.create_sheet("Anleitung")
    help_sheet.column_dimensions["A"].width = 115
    for r, line in enumerate(INSTRUCTIONS, 1):
        help_sheet.cell(r, 1, line).alignment = Alignment(wrap_text=True, vertical="top")
    help_sheet["A1"].font = Font(bold=True, size=14)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _number(value) -> float | None:
    """Zahl aus einer Zelle, auch "12,5", "< 0,5" oder "12 g". Wirft ValueError, wenn es keine Zahl ist."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"\s*(kcal|gr\.?|g|ml)$", "", str(value).strip(), flags=re.I)
    text = re.sub(r"^[<>~≈]\s*", "", text).replace(",", ".")
    return float(text) if text else None


def _column_index(cell_ref: str) -> int:
    """"K5" -> 10 (Spalten ab 0)."""
    index = 0
    for char in re.match(r"[A-Za-z]*", cell_ref).group().upper():
        index = index * 26 + ord(char) - 64
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    # rPh = Lesehilfen für japanische Texte, gehören nicht zum Zellinhalt
    return ["".join(t.text or "" for part in si if part.tag != f"{MAIN_NS}rPh" for t in part.iter(f"{MAIN_NS}t"))
            for si in root.findall(f"{MAIN_NS}si")]


def _sheet_path(archive: zipfile.ZipFile) -> str:
    """Pfad des Blatts „Lebensmittel“, sonst des ersten Blatts."""
    sheets = ElementTree.fromstring(archive.read("xl/workbook.xml")).find(f"{MAIN_NS}sheets")
    sheet = next((s for s in sheets if s.get("name") == SHEET), sheets[0])
    rel_id = sheet.get(f"{REL_NS}id")
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(r.get("Target") for r in rels if r.get("Id") == rel_id)
    return target.lstrip("/") if target.startswith("/") else "xl/" + target


def _cell_value(cell, strings: list[str]):
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{MAIN_NS}t"))
    raw = cell.findtext(f"{MAIN_NS}v")
    if raw is None or kind == "e":
        return None
    if kind == "s":
        return strings[int(raw)]
    return float(raw) if kind == "n" else raw


def _read_rows(data: bytes) -> list[tuple]:
    """Liest nur die Zellwerte. Formatierungen bleiben außen vor, weil jede Tabellen-App sie anders speichert."""
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError("Die Datei ist größer als 2 MB")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_UNPACKED_BYTES:
                raise ImportFileError("Die Datei ist zu groß")
            strings = _shared_strings(archive)
            sheet = ElementTree.fromstring(archive.read(_sheet_path(archive)))
        rows, row_no = {}, 0
        for row in sheet.iter(f"{MAIN_NS}row"):
            row_no = int(row.get("r") or row_no + 1)
            if row_no > MAX_SCAN_ROWS:
                break
            values, col = [None] * len(COLUMNS), -1
            for cell in row.findall(f"{MAIN_NS}c"):
                col = _column_index(cell.get("r")) if cell.get("r") else col + 1
                if col < len(COLUMNS):
                    values[col] = _cell_value(cell, strings)
            rows[row_no] = tuple(values)
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("Das ist keine lesbare .xlsx-Datei. Bitte die Tabelle als .xlsx speichern.")
    if _text(rows.get(HEADER_ROW, (None,))[0]).rstrip(" *").lower() != "lebensmittel":
        raise ImportFileError("Das ist nicht die Bissfest-Vorlage. Bitte zuerst die Vorlage herunterladen.")
    empty = (None,) * len(COLUMNS)
    return [rows.get(r, empty) for r in range(FIRST_ROW, max(rows) + 1)]


def _portion(size: float | None, unit_raw: str, grams: float | None) -> tuple[str | None, float | None, float | None]:
    """Liefert Portionsname, Gramm pro 1 Einheit und Gramm der ganzen Portion. Ohne Gewicht: Name, None, None."""
    unit = UNIT_ALIASES.get(unit_raw.lower().rstrip("."), unit_raw.rstrip("."))
    if size is None and not unit and grams is None:
        return None, None, None
    if unit in ("g", "ml") or (not unit and grams is None):
        total = grams if grams is not None else size
        return "Portion", total, total
    count = size or 1
    return unit or "Portion", (grams / count if grams is not None else None), grams


def _parse_row(cells: dict) -> tuple[dict, list[str]]:
    """Wandelt eine Zeile in ein Lebensmittel um. Ergebnis: Lebensmittel und Warnungen. ValueError = Zeile ungültig."""
    labels = {key: title.rstrip(" *") for key, title, _ in COLUMNS}
    numbers = {}
    for key, value in cells.items():
        if key.endswith(("_100g", "_portion")) or key in ("portion_size", "portion_grams"):
            try:
                numbers[key] = _number(value)
            except ValueError:
                raise ValueError(f"{labels[key]}: „{_text(value)}“ ist keine Zahl")
            if numbers[key] is not None and numbers[key] < 0:
                raise ValueError(f"{labels[key]} darf nicht negativ sein")

    name = _text(cells["name"])
    if not name:
        raise ValueError("Der Name fehlt")
    code = _text(cells["code"])
    if code and not re.fullmatch(r"\d{6,14}", code):
        raise ValueError("Der Barcode muss aus 6 bis 14 Ziffern bestehen")
    unit_raw = _text(cells["portion_unit"])
    if len(unit_raw) > 30:
        raise ValueError("Die Einheit ist zu lang")
    if numbers["portion_size"] == 0 or numbers["portion_grams"] == 0:
        raise ValueError("Die Portion darf nicht 0 sein")
    portion_name, portion_grams, total = _portion(numbers["portion_size"], unit_raw, numbers["portion_grams"])

    warnings = []
    food = {"name": name, "manufacturer": _text(cells["manufacturer"]), "brand": _text(cells["brand"]), "code": code}
    for field, value in (("Name", name), ("Hersteller", food["manufacturer"]), ("Marke", food["brand"])):
        if len(value) > 120:
            raise ValueError(f"{field} ist länger als 120 Zeichen")
    for key, label in NUTRIENTS:
        per_100, per_portion = numbers[f"{key}_100g"], numbers[f"{key}_portion"]
        if per_100 is None and per_portion is not None and total:
            per_100 = round(per_portion * 100 / total, 1)
        food[f"{key}_100g"] = per_100
        if key == "kcal" and per_100 is not None and per_portion is not None and total:
            expected = per_100 * total / 100
            if abs(expected - per_portion) > max(2, expected * 0.1):
                warnings.append(f"Kalorien pro Portion passen nicht zu den 100-g-Werten ({round(expected)} statt "
                                f"{round(per_portion)} kcal). Die Werte pro 100 g werden übernommen.")
        limit = 1000 if key == "kcal" else 100
        if per_100 is not None and per_100 > limit:
            raise ValueError(f"{label.split(' (')[0]} pro 100 g ist höher als {limit}")
    if food["kcal_100g"] is None:
        raise ValueError("Die Kalorien fehlen. Für die Umrechnung aus der Portion braucht es auch das Gewicht.")
    if food["sugar_100g"] is not None and food["carbs_100g"] is not None and food["sugar_100g"] > food["carbs_100g"]:
        raise ValueError("Zucker kann nicht mehr sein als Kohlenhydrate")

    food["portion_name"], food["portion_grams"] = None, None
    spoons = spoon_grams(name) if portion_name in ("TL", "EL") and portion_grams is None else None
    if spoons:
        portion_grams = spoons[0] if portion_name == "TL" else spoons[1]
        warnings.append(f"Kein Gewicht angegeben, Richtwert genutzt: 1 {portion_name} = {portion_grams} g.")
    if portion_name and portion_grams is None:
        warnings.append(f"Für „{portion_name}“ fehlt das Gewicht, die Portion wird nicht angelegt. "
                        "Du kannst es nachtragen und die Datei erneut hochladen.")
    elif portion_grams is not None and portion_grams > 3000:
        raise ValueError("Eine Portion darf höchstens 3000 g wiegen")
    elif portion_name:
        food["portion_name"], food["portion_grams"] = portion_name, round(portion_grams, 1)
    return food, warnings


def parse_workbook(data: bytes) -> tuple[list[dict], list[dict]]:
    """Liest eine ausgefüllte Vorlage. Ergebnis: gültige Lebensmittel (mit Zeile und Warnungen) und fehlerhafte Zeilen."""
    keys = [key for key, _, _ in COLUMNS]
    foods, errors = [], []
    for row_no, values in enumerate(_read_rows(data), FIRST_ROW):
        if all(_text(v) == "" for v in values):
            continue
        if _text(values[0]).lower().startswith(EXAMPLE_PREFIX):
            continue
        if len(foods) + len(errors) >= MAX_FOODS:
            raise ImportFileError(f"Höchstens {MAX_FOODS} Lebensmittel je Datei")
        try:
            food, warnings = _parse_row(dict(zip(keys, values)))
            foods.append({**food, "row": row_no, "warnings": warnings})
        except ValueError as e:
            errors.append({"row": row_no, "name": _text(values[0]), "message": str(e)})
    return foods, errors
