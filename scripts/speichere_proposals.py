"""
speichere_proposals.py – Hilfsskript

Liest JSON von stdin, validiert es und speichert es nach
analysis/proposals/<batch-name>-proposals.json.

Claude Desktop gibt die Vorschläge als einzelne JSON-Blöcke aus
(einen pro Quellartikel). Dieses Skript erkennt beides:
  - Ein fertig zusammengesetztes JSON-Array  [{ ... }, { ... }]
  - Mehrere JSON-Objekte in ```json ... ```-Codeblöcken, die es
    automatisch zu einem Array zusammenfügt

Aufruf:
    python scripts/speichere_proposals.py batch-01
    python scripts/speichere_proposals.py batch-01 --force
    cat ergebnis.json | python scripts/speichere_proposals.py batch-01
"""

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT	= Path(__file__).resolve().parent.parent
PROPOSALS_DIR	= PROJECT_ROOT / "analysis" / "proposals"

PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / "env.local")

# ---------------------------------------------------------------------------
# JSON aus Roheingabe extrahieren
# ---------------------------------------------------------------------------


def extrahiere_json(text: str) -> str:
	"""
	Bereitet den stdin-Text für das JSON-Parsing vor.

	Drei Eingabe-Varianten werden erkannt:
	  1. Roher JSON-Text (Array oder einzelnes Objekt)
	  2. Ein einziger ```json ... ```-Codeblock
	  3. Mehrere ```json ... ```-Codeblöcke → werden zu einem Array zusammengefügt

	Gibt einen gültigen JSON-String zurück (immer ein Array).
	Löst ValueError aus, wenn kein JSON gefunden wird.
	"""
	text = text.strip()

	# Alle ```json ... ``` oder ``` ... ```-Blöcke extrahieren
	codeblock_muster = re.compile(
		r'```(?:json)?\s*\n(.*?)\n\s*```',
		re.DOTALL,
	)
	bloecke = codeblock_muster.findall(text)

	if bloecke:
		# Jeden Block als JSON parsen
		objekte: list = []
		for i, block in enumerate(bloecke, start=1):
			block = block.strip()
			try:
				geparst = json.loads(block)
			except json.JSONDecodeError as exc:
				raise ValueError(
					f"Codeblock {i} enthält kein gültiges JSON: {exc}"
				) from exc

			# Codeblock kann selbst ein Array sein
			if isinstance(geparst, list):
				objekte.extend(geparst)
			else:
				objekte.append(geparst)

		return json.dumps(objekte, ensure_ascii=False)

	# Kein Codeblock – Text direkt als JSON interpretieren
	try:
		geparst = json.loads(text)
	except json.JSONDecodeError as exc:
		raise ValueError(f"Kein gültiges JSON erkannt: {exc}") from exc

	# Einzelnes Objekt in Liste wrappen
	if isinstance(geparst, dict):
		geparst = [geparst]

	if not isinstance(geparst, list):
		raise ValueError(
			f"Ungültiger Typ: Erwartet Objekt oder Array, erhalten {type(geparst).__name__}."
		)

	return json.dumps(geparst, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Struktur-Validierung (weich: warnt, bricht nicht ab)
# ---------------------------------------------------------------------------


def validiere_struktur(daten: list) -> list[str]:
	"""
	Prüft, ob jedes Objekt im Array die Pflichtfelder besitzt.
	Gibt eine Liste von Warnungen zurück (leer = alles i.O.).
	"""
	pflichtfelder = {"quell_slug", "quell_titel", "quell_url", "vorschlaege"}
	warnungen: list[str] = []

	for i, obj in enumerate(daten, start=1):
		if not isinstance(obj, dict):
			warnungen.append(f"Eintrag {i}: kein Objekt (Typ: {type(obj).__name__})")
			continue
		fehlend = pflichtfelder - set(obj.keys())
		if fehlend:
			slug = obj.get("quell_slug", f"Eintrag {i}")
			warnungen.append(
				f"[{slug}]: fehlende Felder: {', '.join(sorted(fehlend))}"
			)
		vorschlaege = obj.get("vorschlaege", [])
		if not isinstance(vorschlaege, list) or len(vorschlaege) == 0:
			slug = obj.get("quell_slug", f"Eintrag {i}")
			warnungen.append(f"[{slug}]: 'vorschlaege' ist leer oder kein Array")

	return warnungen


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description=(
			"SEO Crawler – Proposals von stdin nach analysis/proposals/ speichern.\n"
			"Erwartet JSON von Claude Desktop (Codeblöcke oder Array)."
		),
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	parser.add_argument(
		"batch",
		metavar="BATCH-NAME",
		help="Batch-Bezeichner, z. B. batch-01  (Ausgabe: batch-01-proposals.json)",
	)
	parser.add_argument(
		"--force",
		action="store_true",
		help="Vorhandene Datei überschreiben",
	)
	args = parser.parse_args()

	# Ausgabepfad ermitteln
	dateiname	= f"{args.batch}-proposals.json"
	ausgabe		= PROPOSALS_DIR / dateiname

	if ausgabe.exists() and not args.force:
		print(f"FEHLER: {ausgabe} existiert bereits.")
		print("Mit --force überschreiben.")
		sys.exit(1)

	# Hinweis bei interaktiver Eingabe
	if sys.stdin.isatty():
		print(f"JSON eingeben (Codeblöcke oder Array), dann Strg+D:")
		print()

	# stdin lesen
	try:
		rohtext = sys.stdin.read()
	except KeyboardInterrupt:
		print("\nAbgebrochen.")
		sys.exit(1)

	if not rohtext.strip():
		print("FEHLER: Keine Eingabe erhalten.")
		sys.exit(1)

	# JSON extrahieren und parsen
	try:
		json_str = extrahiere_json(rohtext)
	except ValueError as exc:
		print(f"FEHLER: {exc}")
		sys.exit(1)

	# Endgültig parsen (für Validierung und formatiertes Schreiben)
	daten = json.loads(json_str)

	# Struktur-Validierung (weich)
	warnungen = validiere_struktur(daten)
	if warnungen:
		print(f"WARNUNG: Struktur-Probleme in {len(warnungen)} Einträgen:")
		for w in warnungen:
			print(f"  ⚠  {w}")
		print()

	# Datei schreiben
	ausgabe.write_text(
		json.dumps(daten, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)

	# Zusammenfassung
	n_artikel		= len(daten)
	n_vorschlaege	= sum(len(obj.get("vorschlaege", [])) for obj in daten if isinstance(obj, dict))

	print(f"Gespeichert: {ausgabe}")
	print(f"  Artikel:     {n_artikel}")
	print(f"  Vorschläge:  {n_vorschlaege}")
	if warnungen:
		print(f"  Warnungen:   {len(warnungen)}  (siehe oben)")


if __name__ == "__main__":
	main()
