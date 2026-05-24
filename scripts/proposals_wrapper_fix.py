"""
proposals_wrapper_fix.py – Wrapper-Reparatur für Proposal-Dateien

Prüft alle JSON-Dateien in analysis/proposals/ und ergänzt bei
reinen Arrays die fehlende Wrapper-Struktur mit linguist_meta-
Platzhalter.  Bereits gewrappte Dateien werden übersprungen.

Aufruf:
    .venv/bin/python scripts/proposals_wrapper_fix.py
    .venv/bin/python scripts/proposals_wrapper_fix.py --datei batch-01-proposals.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT   = Path(__file__).resolve().parent.parent
PROPOSALS_DIR  = PROJECT_ROOT / "analysis" / "proposals"

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

_STEUERZEICHEN = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

LINGUIST_META_PLATZHALTER = {
	"modellfamilie":       "unbekannt",
	"modell":              "unbekannt",
	"modellnummer":        "unbekannt",
	"erweitertes_denken":  None,
	"aufwand_intensitaet": "unbekannt",
	"erstellt_am":         "unbekannt",
}

# ---------------------------------------------------------------------------
# Kernlogik
# ---------------------------------------------------------------------------


def _verarbeite_datei(pfad: Path) -> str:
	"""
	Prüft und repariert eine einzelne Proposal-Datei.
	Rückgabe: "OK" | "ERGÄNZT" | "FEHLER: <grund>"
	"""
	# Lesen
	try:
		roh = pfad.read_text(encoding="utf-8")
	except OSError as exc:
		return f"FEHLER: Datei nicht lesbar – {exc}"

	# Steuerzeichen bereinigen
	roh_clean = _STEUERZEICHEN.sub(' ', roh)

	# JSON parsen
	try:
		daten = json.loads(roh_clean)
	except json.JSONDecodeError as exc:
		return f"FEHLER: Ungültiges JSON – {exc}"

	# Bereits Wrapper-Objekt?
	if isinstance(daten, dict) and isinstance(daten.get("proposals"), list):
		return "OK"

	# Reines Array?
	if isinstance(daten, list):
		wrapper = {
			"linguist_meta": LINGUIST_META_PLATZHALTER,
			"proposals":     daten,
		}
		try:
			pfad.write_text(
				json.dumps(wrapper, ensure_ascii=False, indent=2),
				encoding="utf-8",
			)
		except OSError as exc:
			return f"FEHLER: Schreiben fehlgeschlagen – {exc}"
		return "ERGÄNZT"

	# Weder Array noch Wrapper
	return "FEHLER: Unbekanntes Format – weder Array noch Wrapper-Objekt"


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Wrapper-Reparatur für Proposal-Dateien"
	)
	parser.add_argument(
		"--datei",
		metavar="DATEINAME",
		help=(
			"Nur eine bestimmte Datei verarbeiten "
			"(Dateiname oder Pfad relativ zu analysis/proposals/)"
		),
	)
	args = parser.parse_args()

	# Dateien bestimmen
	if args.datei:
		pfad = Path(args.datei)
		if not pfad.is_absolute():
			pfad = pfad if pfad.exists() else PROPOSALS_DIR / pfad.name
		if not pfad.exists():
			print(f"FEHLER: Datei nicht gefunden: {pfad}")
			sys.exit(1)
		dateien = [pfad]
	else:
		if not PROPOSALS_DIR.exists():
			print(f"FEHLER: Verzeichnis nicht gefunden: {PROPOSALS_DIR}")
			sys.exit(1)
		dateien = sorted(PROPOSALS_DIR.glob("*.json"))
		if not dateien:
			print("Keine JSON-Dateien in analysis/proposals/ gefunden.")
			sys.exit(0)

	# Verarbeiten
	n_ok       = 0
	n_ergaenzt = 0
	n_fehler   = 0

	print()
	for pfad in dateien:
		ergebnis = _verarbeite_datei(pfad)
		status = ergebnis.split(":")[0]   # "OK" | "ERGÄNZT" | "FEHLER"
		print(f"  {pfad.name:<45}  {ergebnis}")
		if status == "OK":
			n_ok += 1
		elif status == "ERGÄNZT":
			n_ergaenzt += 1
		else:
			n_fehler += 1

	# Zusammenfassung
	print()
	print(f"  Gesamt:   {len(dateien)} Datei(en)")
	print(f"  OK:       {n_ok}")
	print(f"  Ergänzt:  {n_ergaenzt}")
	if n_fehler:
		print(f"  Fehler:   {n_fehler}")
	print()

	if n_fehler:
		sys.exit(1)


if __name__ == "__main__":
	main()
