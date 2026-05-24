"""
pool_anwenden.py – Modul 7b: Linguisten-Entscheidungen auf den Pool anwenden

Liest die von Linguisten ausgefüllte Datei grenzfaelle-entschieden.json
und entfernt die dort markierten Terme aus pool-konsolidiert.json.

Erwartetes Format von grenzfaelle-entschieden.json:
    Dasselbe Format wie grenzfaelle.json (Ausgabe von schlagwort_konsolidierung.py),
    aber jeder Eintrag hat ein zusätzliches Feld "entscheidung":
        "behalten_a"    → term_b aus dem Pool entfernen
        "behalten_b"    → term_a aus dem Pool entfernen
        "behalten_beide"→ beide behalten, keine Änderung

    Einträge ohne "entscheidung"-Feld werden übersprungen und gewarnt.

Eingabe:
    data/taxonomie/pool-konsolidiert.json       – Basis-Pool (Modul 7)
    data/taxonomie/grenzfaelle-entschieden.json – Entscheidungen der Linguisten

Ausgabe:
    data/taxonomie/pool-final.json                  – Bereinigter Endpool
    data/taxonomie/entfernte-begriffe-final.json    – Entfernte Terme mit Begründung

Aufruf:
    .venv/bin/python scripts/pool_anwenden.py
    .venv/bin/python scripts/pool_anwenden.py --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT		= Path(__file__).resolve().parent.parent
TAXONOMIE_DIR		= PROJECT_ROOT / "data" / "taxonomie"
LOG_DIR				= PROJECT_ROOT / "logs"

TAXONOMIE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

POOL_DATEI			= TAXONOMIE_DIR / "pool-konsolidiert.json"
ENTSCHIEDEN_DATEI	= TAXONOMIE_DIR / "grenzfaelle-entschieden.json"
POOL_FINAL_DATEI	= TAXONOMIE_DIR / "pool-final.json"
ENTFERNT_DATEI		= TAXONOMIE_DIR / "entfernte-begriffe-final.json"

# Gültige Entscheidungswerte
GUELTIGE_ENTSCHEIDUNGEN = {"behalten_a", "behalten_b", "behalten_beide"}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "pool_anwenden.log"

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
# Eingabe laden
# ---------------------------------------------------------------------------


def lade_pool() -> list[dict]:
	"""Liest pool-konsolidiert.json. Bricht ab wenn die Datei fehlt."""
	if not POOL_DATEI.exists():
		log.error(
			"pool-konsolidiert.json nicht gefunden.\n"
			"  Zuerst schlagwort_konsolidierung.py ausführen."
		)
		sys.exit(1)
	try:
		pool = json.loads(POOL_DATEI.read_text(encoding="utf-8"))
	except (json.JSONDecodeError, OSError) as exc:
		log.error("pool-konsolidiert.json nicht lesbar: %s", exc)
		sys.exit(1)
	if not isinstance(pool, list):
		log.error("pool-konsolidiert.json muss ein JSON-Array sein.")
		sys.exit(1)
	log.info("Pool geladen: %d Begriffe", len(pool))
	return pool


def lade_entscheidungen() -> list[dict]:
	"""Liest grenzfaelle-entschieden.json. Bricht ab wenn die Datei fehlt."""
	if not ENTSCHIEDEN_DATEI.exists():
		log.error(
			"grenzfaelle-entschieden.json nicht gefunden.\n"
			"  Datei aus grenzfaelle.json erstellen und 'entscheidung'-Feld ergänzen."
		)
		sys.exit(1)
	try:
		eintraege = json.loads(ENTSCHIEDEN_DATEI.read_text(encoding="utf-8"))
	except (json.JSONDecodeError, OSError) as exc:
		log.error("grenzfaelle-entschieden.json nicht lesbar: %s", exc)
		sys.exit(1)
	if not isinstance(eintraege, list):
		log.error("grenzfaelle-entschieden.json muss ein JSON-Array sein.")
		sys.exit(1)
	log.info("Entscheidungen geladen: %d Einträge", len(eintraege))
	return eintraege


# ---------------------------------------------------------------------------
# Entscheidungen anwenden
# ---------------------------------------------------------------------------


def wende_entscheidungen_an(
	pool: list[dict],
	entscheidungen: list[dict],
) -> tuple[list[dict], list[dict]]:
	"""
	Wendet die Linguisten-Entscheidungen auf den Pool an.

	Rückgabe: (pool_final, entfernte)
	  pool_final  – Pool ohne die markierten Terme
	  entfernte   – Liste der entfernten Terme mit Begründung
	"""
	# Pool-Index: lowercase-name → list-position (für schnelles Lookup)
	pool_index: dict[str, int] = {
		e["name"].lower(): idx for idx, e in enumerate(pool)
	}

	zu_entfernen: dict[str, dict] = {}	# lowercase-name → Eintrag-Dict für Protokoll

	fehlende_entscheidungen	= 0
	ungueltige_werte		= 0
	nicht_im_pool			= 0

	for eintrag in entscheidungen:
		term_a		= eintrag.get("term_a", "").strip()
		term_b		= eintrag.get("term_b", "").strip()
		entscheidung = eintrag.get("entscheidung", "").strip()

		if not entscheidung:
			log.warning(
				"Kein 'entscheidung'-Feld für Paar (%r, %r) – übersprungen.",
				term_a, term_b,
			)
			fehlende_entscheidungen += 1
			continue

		if entscheidung not in GUELTIGE_ENTSCHEIDUNGEN:
			log.warning(
				"Unbekannte Entscheidung %r für Paar (%r, %r) – übersprungen.\n"
				"  Gültige Werte: %s",
				entscheidung, term_a, term_b,
				", ".join(sorted(GUELTIGE_ENTSCHEIDUNGEN)),
			)
			ungueltige_werte += 1
			continue

		if entscheidung == "behalten_beide":
			log.info("BEHALTEN_BEIDE  %r + %r", term_a, term_b)
			continue

		# Welcher Term soll entfernt werden?
		zu_loeschen = term_b if entscheidung == "behalten_a" else term_a
		behalten    = term_a if entscheidung == "behalten_a" else term_b
		key         = zu_loeschen.lower()

		if key not in pool_index:
			log.warning(
				"NICHT IM POOL  %r soll entfernt werden (Entscheidung: %s), "
				"ist aber nicht im Pool – übersprungen.",
				zu_loeschen, entscheidung,
			)
			nicht_im_pool += 1
			continue

		if key not in zu_entfernen:
			pool_eintrag = pool[pool_index[key]]
			zu_entfernen[key] = {
				"name":         pool_eintrag["name"],
				"quelle":       pool_eintrag.get("quelle", ""),
				"grund":        "linguisten_entscheidung",
				"behalten_statt": behalten,
				"entscheidung": entscheidung,
				"typ":          eintrag.get("typ", ""),
			}
			log.info(
				"ENTFERNEN  %r → behalten: %r  (Entscheidung: %s)",
				zu_loeschen, behalten, entscheidung,
			)

	# Warnungen zusammenfassen
	if fehlende_entscheidungen:
		log.warning("%d Einträge ohne 'entscheidung'-Feld übersprungen.", fehlende_entscheidungen)
	if ungueltige_werte:
		log.warning("%d Einträge mit ungültigem Entscheidungswert übersprungen.", ungueltige_werte)
	if nicht_im_pool:
		log.warning(
			"%d zu entfernende Terme nicht im Pool gefunden "
			"(möglicherweise bereits in schlagwort_konsolidierung.py entfernt).",
			nicht_im_pool,
		)

	# Pool filtern
	entfernte_keys	= set(zu_entfernen.keys())
	pool_final		= [e for e in pool if e["name"].lower() not in entfernte_keys]
	entfernte		= list(zu_entfernen.values())

	return pool_final, entfernte


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Modul 7b: Linguisten-Entscheidungen anwenden"
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Zeigt Ergebnis, schreibt keine Ausgabedateien",
	)
	args = parser.parse_args()

	log.info("=== Pool-Anwenden gestartet ===")

	pool		  = lade_pool()
	entscheidungen = lade_entscheidungen()

	pool_final, entfernte = wende_entscheidungen_an(pool, entscheidungen)

	vorher  = len(pool)
	nachher = len(pool_final)
	entfernt_anz = len(entfernte)

	log.info(
		"Pool: %d Begriffe → %d Begriffe  (%d entfernt)",
		vorher, nachher, entfernt_anz,
	)

	print()
	print(f"  Pool vorher:   {vorher:>5}  Begriffe")
	print(f"  Pool nachher:  {nachher:>5}  Begriffe")
	print(f"  Entfernt:      {entfernt_anz:>5}  Terme")
	print()

	if args.dry_run:
		if entfernte:
			print("  Würde entfernen:")
			for e in entfernte:
				print(f"    – {e['name']!r}  (behalten statt: {e['behalten_statt']!r})")
			print()
		log.info("Dry-Run – keine Dateien geschrieben.")
		return

	POOL_FINAL_DATEI.write_text(
		json.dumps(pool_final, ensure_ascii=False, indent=2), encoding="utf-8"
	)
	ENTFERNT_DATEI.write_text(
		json.dumps(entfernte, ensure_ascii=False, indent=2), encoding="utf-8"
	)

	log.info("=== Fertig ===")
	log.info("Pool final:  %s", POOL_FINAL_DATEI)
	log.info("Entfernt:    %s", ENTFERNT_DATEI)


if __name__ == "__main__":
	main()
