"""
taxonomie_export.py – WordPress-Taxonomien per REST API exportieren

Fragt alle Schlagwörter (tags) und Kategorien (categories) der WordPress-
Site ab und speichert sie lokal als JSON-Dateien.

Ausgabedateien:
    data/taxonomie/schlagwoerter-export.json   – WP REST API-Standardformat
    data/taxonomie/kategorien-export.json      – WP REST API-Standardformat

Format beider Dateien:
    [
      {
        "id":     5,
        "name":   "Theater",
        "slug":   "theater",
        "parent": 0,        # nur bei Kategorien
        "count":  22
      },
      ...
    ]

Aufruf:
    .venv/bin/python scripts/taxonomie_export.py
    .venv/bin/python scripts/taxonomie_export.py --nur-kategorien
    .venv/bin/python scripts/taxonomie_export.py --nur-tags
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT	= Path(__file__).resolve().parent.parent
TAXONOMIE_DIR	= PROJECT_ROOT / "data" / "taxonomie"
LOG_DIR			= PROJECT_ROOT / "logs"
ENV_FILE		= PROJECT_ROOT / "env.local"

TAGS_DATEI		= TAXONOMIE_DIR / "schlagwoerter-export.json"
KATEGORIEN_DATEI= TAXONOMIE_DIR / "kategorien-export.json"

TAXONOMIE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "taxonomie_export.log"

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s  %(levelname)-7s  %(message)s",
	datefmt="%Y-%m-%d %H:%M:%S",
	handlers=[
		logging.FileHandler(LOG_FILE, encoding="utf-8"),
		logging.StreamHandler(sys.stdout),
	],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

load_dotenv(ENV_FILE)

WP_URL          = os.getenv("WP_URL", "").rstrip("/")
WP_USER         = os.getenv("WP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")

if not WP_URL:
	log.error("WP_URL fehlt in env.local – Abbruch.")
	sys.exit(1)

if not WP_USER or not WP_APP_PASSWORD:
	log.error("WP_USER oder WP_APP_PASSWORD fehlt in env.local – Abbruch.")
	sys.exit(1)

AUTH     = (WP_USER, WP_APP_PASSWORD)
REST_BASE = f"{WP_URL}/wp-json/wp/v2"

# ---------------------------------------------------------------------------
# Paginierter REST-Abruf
# ---------------------------------------------------------------------------

PER_PAGE = 100


def _hole_alle_seiten(endpoint: str, felder: str) -> list[dict]:
	"""
	Ruft einen paginierten WP-REST-API-Endpunkt komplett ab.

	Nutzt den Header X-WP-TotalPages für die Seitenanzahl; fällt auf
	leere-Antwort-Erkennung zurück wenn der Header fehlt.

	Rückgabe: flache Liste aller Einträge über alle Seiten.
	"""
	url     = f"{REST_BASE}/{endpoint}"
	alle:   list[dict] = []
	seite   = 1
	gesamt_seiten: int | None = None

	while True:
		params = {
			"per_page": PER_PAGE,
			"page":     seite,
			"_fields":  felder,
		}
		log.info("GET %s  (Seite %d%s)", endpoint,
		         seite, f" von {gesamt_seiten}" if gesamt_seiten else "")

		try:
			resp = requests.get(url, params=params, auth=AUTH, timeout=30)
		except requests.RequestException as exc:
			log.error("Netzwerkfehler bei %s Seite %d: %s", endpoint, seite, exc)
			sys.exit(1)

		# 400 = Seite jenseits des Endes → sauber abbrechen
		if resp.status_code == 400:
			log.debug("Seite %d → HTTP 400 – Ende der Pagination.", seite)
			break

		if resp.status_code != 200:
			log.error(
				"HTTP %d bei %s Seite %d: %s",
				resp.status_code, endpoint, seite, resp.text[:200],
			)
			sys.exit(1)

		# TotalPages aus Header lesen (beim ersten Aufruf)
		if gesamt_seiten is None:
			try:
				gesamt_seiten = int(resp.headers.get("X-WP-TotalPages", 1))
			except (ValueError, TypeError):
				gesamt_seiten = None

		try:
			eintraege = resp.json()
		except ValueError as exc:
			log.error("JSON-Parsefehler bei %s Seite %d: %s", endpoint, seite, exc)
			sys.exit(1)

		if not isinstance(eintraege, list) or not eintraege:
			break

		alle.extend(eintraege)

		if gesamt_seiten is not None and seite >= gesamt_seiten:
			break

		seite += 1

	return alle


# ---------------------------------------------------------------------------
# Export-Funktionen
# ---------------------------------------------------------------------------


def exportiere_tags() -> int:
	"""
	Lädt alle Schlagwörter via GET /wp/v2/tags und speichert
	schlagwoerter-export.json.  Rückgabe: Anzahl exportierter Einträge.
	"""
	log.info("=== Schlagwörter exportieren ===")
	eintraege = _hole_alle_seiten("tags", felder="id,name,slug,count")

	# Sortieren: alphabetisch nach name
	eintraege.sort(key=lambda e: e.get("name", "").lower())

	TAGS_DATEI.write_text(
		json.dumps(eintraege, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)
	log.info(
		"schlagwoerter-export.json geschrieben: %d Einträge → %s",
		len(eintraege), TAGS_DATEI,
	)
	return len(eintraege)


def exportiere_kategorien() -> int:
	"""
	Lädt alle Kategorien via GET /wp/v2/categories und speichert
	kategorien-export.json.  Rückgabe: Anzahl exportierter Einträge.
	"""
	log.info("=== Kategorien exportieren ===")
	eintraege = _hole_alle_seiten("categories", felder="id,name,slug,parent,count")

	# Sortieren: Hauptkategorien zuerst (parent=0), dann alphabetisch
	eintraege.sort(key=lambda e: (e.get("parent", 0) != 0, e.get("name", "").lower()))

	KATEGORIEN_DATEI.write_text(
		json.dumps(eintraege, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)
	log.info(
		"kategorien-export.json geschrieben: %d Einträge → %s",
		len(eintraege), KATEGORIEN_DATEI,
	)
	return len(eintraege)


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – WordPress-Taxonomien per REST API exportieren"
	)
	gruppe = parser.add_mutually_exclusive_group()
	gruppe.add_argument(
		"--nur-tags",
		action="store_true",
		help="Nur Schlagwörter exportieren",
	)
	gruppe.add_argument(
		"--nur-kategorien",
		action="store_true",
		help="Nur Kategorien exportieren",
	)
	args = parser.parse_args()

	ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
	log.info("=== taxonomie_export.py gestartet  %s ===", ts)
	log.info("WordPress: %s", WP_URL)

	n_tags = n_kat = 0

	if not args.nur_kategorien:
		n_tags = exportiere_tags()

	if not args.nur_tags:
		n_kat = exportiere_kategorien()

	print()
	print("  ╔══════════════════════════════════════════╗")
	print("  ║   Taxonomie-Export abgeschlossen          ║")
	print("  ╚══════════════════════════════════════════╝")
	print()
	if not args.nur_kategorien:
		print(f"  Schlagwörter:  {n_tags:>4}  → data/taxonomie/schlagwoerter-export.json")
	if not args.nur_tags:
		print(f"  Kategorien:    {n_kat:>4}  → data/taxonomie/kategorien-export.json")
	print()
	print(f"  Stand: {ts}")
	print()

	if not args.nur_tags:
		print("  Nächste Schritte:")
		print("    .venv/bin/python scripts/batch_vorbereitung.py --force")
		print("    .venv/bin/python scripts/prompt_kompilieren.py")
		print()

	log.info(
		"=== Fertig ===  Tags: %d  Kategorien: %d",
		n_tags, n_kat,
	)


if __name__ == "__main__":
	main()
