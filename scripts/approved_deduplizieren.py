"""
approved_deduplizieren.py – Duplikate in approved-Verzeichnissen bereinigen

Läuft über output/approved/links/, output/approved/tags/ und
output/approved/kategorien/. Pro Verzeichnis: Gruppiert alle JSON-Dateien
nach Artikel-Slug (Dateiname ohne den letzten Timestamp-Teil -YYYYMMDD-HHMMSS),
behält pro Slug nur die neueste Datei und löscht ältere Duplikate.

Aufruf:
    .venv/bin/python scripts/approved_deduplizieren.py
    .venv/bin/python scripts/approved_deduplizieren.py --dry-run
"""

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APPROVED_DIR = PROJECT_ROOT / "output" / "approved"

MODI = ["links", "tags", "kategorien"]

# Timestamp-Suffix: -YYYYMMDD-HHMMSS (am Ende des Dateinamens, vor .json)
_TIMESTAMP_RE = re.compile(r'^(.+)-(\d{8}-\d{6})$')


# ---------------------------------------------------------------------------
# Kernlogik
# ---------------------------------------------------------------------------


def _verarbeite_verzeichnis(verz: Path, dry_run: bool) -> tuple[int, int]:
	"""
	Dedupliziert ein einzelnes approved-Verzeichnis.
	Gibt (geloescht, verbleibend) zurück.
	"""
	if not verz.exists():
		return 0, 0

	# Alle JSON-Dateien einlesen
	dateien = sorted(verz.glob("*.json"))

	# Nach Slug gruppieren
	gruppen: dict[str, list[Path]] = {}
	ohne_timestamp: list[Path] = []

	for datei in dateien:
		stem = datei.stem  # Dateiname ohne .json
		m = _TIMESTAMP_RE.match(stem)
		if m:
			slug = m.group(1)
			gruppen.setdefault(slug, []).append(datei)
		else:
			ohne_timestamp.append(datei)

	n_geloescht  = 0
	n_verbleibend = len(ohne_timestamp)

	for slug, gruppe in sorted(gruppen.items()):
		if len(gruppe) == 1:
			n_verbleibend += 1
			continue

		# Neueste Datei = höchster Timestamp-String (lexikografisch korrekt)
		gruppe_sortiert = sorted(gruppe, key=lambda p: p.stem, reverse=True)
		behalten = gruppe_sortiert[0]
		loeschen = gruppe_sortiert[1:]

		n_verbleibend += 1

		for alt in loeschen:
			if dry_run:
				print(f"    [DRY-RUN] würde löschen: {alt.name}  (behalten: {behalten.name})")
			else:
				alt.unlink()
				print(f"    Gelöscht: {alt.name}  (behalten: {behalten.name})")
			n_geloescht += 1

	return n_geloescht, n_verbleibend


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Duplikate in approved-Verzeichnissen bereinigen"
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Zeigt, was gelöscht würde – löscht nichts",
	)
	args = parser.parse_args()

	dry_run = args.dry_run

	print()
	if dry_run:
		print("  [DRY-RUN] – keine Dateien werden gelöscht")
	print()

	gesamt_geloescht  = 0
	gesamt_verbleibend = 0

	for modus in MODI:
		verz = APPROVED_DIR / modus
		geloescht, verbleibend = _verarbeite_verzeichnis(verz, dry_run)
		gesamt_geloescht   += geloescht
		gesamt_verbleibend += verbleibend

		if geloescht or verbleibend:
			print(
				f"  {modus + '/':12}  "
				f"{geloescht} Duplikat(e) {'würden gelöscht' if dry_run else 'gelöscht'},"
				f"  {verbleibend} Artikel verbleiben"
			)
		else:
			print(f"  {modus + '/':12}  (Verzeichnis leer oder nicht vorhanden)")

	print()
	if gesamt_geloescht:
		print(f"  Gesamt: {gesamt_geloescht} Datei(en) {'würden gelöscht' if dry_run else 'gelöscht'}")
	else:
		print("  Keine Duplikate gefunden.")
	print()

	if dry_run and gesamt_geloescht:
		print("  Zum tatsächlichen Löschen ohne --dry-run ausführen.")
		print()


if __name__ == "__main__":
	main()
