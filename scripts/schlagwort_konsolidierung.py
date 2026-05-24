"""
schlagwort_konsolidierung.py – Modul 7: Schlagwort-Pool-Konsolidierung

Schritt A des SEO-Workflows: Bestehende WordPress-Schlagwörter mit
neuen Vorschlägen aus den Claude-Proposals zusammenführen, Duplikate
bereinigen und einen konsolidierten Pool für die Linguisten-Sitzung
erstellen.

Eingabe:
    data/taxonomie/schlagwoerter-export.json  – Bestehende WP-Schlagwörter
    analysis/proposals/batch-*-proposals.json – neue_schlagwoerter-Felder

Ausgabe:
    data/taxonomie/pool-konsolidiert.json     – Bereinigter Pool
    data/taxonomie/entfernte-begriffe.json    – Automatisch entfernte Duplikate
    data/taxonomie/grenzfaelle.json           – Grenzfälle zur Linguisten-Prüfung

Automatisch entfernt (keine manuelle Prüfung nötig):
    - Exakte Duplikate (Groß-/Kleinschreibung ignoriert)
    - Slug-Duplikate  (gleicher WP-Slug, verschiedener Name)

Zur manuellen Prüfung (grenzfaelle.json):
    - Präfix-Paare  (A ist Präfix von B, |A| ≥ PRAEFIX_MINDESTLAENGE)
    - Levenshtein-Paare  (Distanz ≤ LEVENSHTEIN_MAX_DISTANZ, |A|,|B| ≥ LEVENSHTEIN_MINDESTLAENGE)

Kein Embedding-Modell – nur regelbasierte Python-Logik.

WP-Export (einmalig, vor erstem Lauf):
    curl -u USER:APP_PASS \\
      'https://ihre-domain.de/wp-json/wp/v2/tags?per_page=100' \\
      > data/taxonomie/schlagwoerter-export.json

Aufruf:
    .venv/bin/python scripts/schlagwort_konsolidierung.py
    .venv/bin/python scripts/schlagwort_konsolidierung.py --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT	= Path(__file__).resolve().parent.parent
PROPOSALS_DIR	= PROJECT_ROOT / "analysis" / "proposals"
TAXONOMIE_DIR	= PROJECT_ROOT / "data" / "taxonomie"
LOG_DIR			= PROJECT_ROOT / "logs"

TAXONOMIE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

EXPORT_DATEI	= TAXONOMIE_DIR / "schlagwoerter-export.json"
POOL_DATEI		= TAXONOMIE_DIR / "pool-konsolidiert.json"
ENTFERNT_DATEI	= TAXONOMIE_DIR / "entfernte-begriffe.json"
GRENZFALL_DATEI	= TAXONOMIE_DIR / "grenzfaelle.json"

# ---------------------------------------------------------------------------
# Regelparameter
# ---------------------------------------------------------------------------

# Präfix-Erkennung: A ist Präfix von B → Grenzfall, wenn |A| ≥ dieser Wert
PRAEFIX_MINDESTLAENGE		= 5

# Levenshtein: Distanz ≤ MAX_DISTANZ → Grenzfall, wenn beide Begriffe ≥ MIN_LAENGE
LEVENSHTEIN_MAX_DISTANZ		= 2
LEVENSHTEIN_MINDESTLAENGE	= 6

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "schlagwort_konsolidierung.log"

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
# Daten laden
# ---------------------------------------------------------------------------


def _normalisiere_eintrag(eintrag: dict) -> dict:
	"""
	Vereinheitlicht einen Schlagwort-Eintrag.

	Unterstützte Quellformate:
	  WP REST API  → {id, name, slug, count, …}
	  Unser Export → {term_id, name, slug, count, …}
	"""
	return {
		"id":    eintrag.get("id") or eintrag.get("term_id"),
		"name":  eintrag.get("name", ""),
		"slug":  eintrag.get("slug", ""),
		"count": eintrag.get("count", 0),
	}


def lade_wp_schlagwoerter() -> list[dict]:
	"""
	Liest schlagwoerter-export.json.

	Unterstützte Dateiformate:
	  1. WP REST API-Standard (Array):   [{id, name, slug, count}, …]
	  2. Wrapper-Objekt mit Metadaten:   {…, "<key>": [{…}, …], …}
	       Erster Schlüssel mit Array-Wert wird automatisch verwendet.

	Feldnamen: id und term_id werden beide als ID-Feld akzeptiert.
	Gibt leere Liste zurück wenn die Datei fehlt oder ein Placeholder ist.
	"""
	if not EXPORT_DATEI.exists():
		log.warning(
			"schlagwoerter-export.json fehlt – nur Proposal-Begriffe werden verarbeitet.\n"
			"  Export: curl -u USER:PASS 'WP_URL/wp-json/wp/v2/tags?per_page=100'"
			" > data/taxonomie/schlagwoerter-export.json",
		)
		return []

	try:
		daten = json.loads(EXPORT_DATEI.read_text(encoding="utf-8"))
	except (json.JSONDecodeError, OSError) as exc:
		log.error("schlagwoerter-export.json nicht lesbar: %s", exc)
		sys.exit(1)

	# Format 2: Wrapper-Objekt → ersten Schlüssel mit Array-Wert verwenden
	if isinstance(daten, dict):
		schluessel = next(
			(k for k, v in daten.items() if isinstance(v, list)),
			None,
		)
		if schluessel is None:
			log.error(
				"schlagwoerter-export.json ist ein Objekt, enthält aber kein Array-Feld."
			)
			sys.exit(1)
		log.info("Wrapper-Format erkannt – verwende Feld %r", schluessel)
		daten = daten[schluessel]

	if not isinstance(daten, list):
		log.error(
			"schlagwoerter-export.json: Unbekanntes Format – weder Array noch Objekt."
		)
		sys.exit(1)

	# Placeholder-Marker prüfen
	if daten and daten[0].get("_placeholder"):
		log.warning(
			"Placeholder-Datei erkannt – wird ignoriert.\n"
			"  Bitte zuerst echte WP-Schlagwörter exportieren."
		)
		return []

	normalisiert = [_normalisiere_eintrag(e) for e in daten if isinstance(e, dict)]
	log.info("WP-Schlagwörter geladen: %d", len(normalisiert))
	return normalisiert


def lade_proposals_schlagwoerter() -> dict[str, list[str]]:
	"""
	Liest neue_schlagwoerter aus allen Proposal-Dateien.
	Rückgabe: {artikel_slug: [tag, …]}
	"""
	ergebnis: dict[str, list[str]] = {}
	for pf in sorted(PROPOSALS_DIR.glob("batch-*-proposals.json")):
		try:
			eintraege = json.loads(pf.read_text(encoding="utf-8"))
		except (json.JSONDecodeError, OSError) as exc:
			log.warning("Übersprungen (%s): %s", pf.name, exc)
			continue
		if not isinstance(eintraege, list):
			continue
		for e in eintraege:
			slug = e.get("slug", "").strip()
			tags = [
				t.strip() for t in e.get("neue_schlagwoerter", [])
				if isinstance(t, str) and t.strip()
			]
			if slug and tags:
				ergebnis[slug] = tags

	gesamt = sum(len(v) for v in ergebnis.values())
	log.info(
		"Proposals-Schlagwörter: %d Begriffe aus %d Artikeln",
		gesamt, len(ergebnis),
	)
	return ergebnis


# ---------------------------------------------------------------------------
# Regelbasierte Hilfsfunktionen
# ---------------------------------------------------------------------------


def _levenshtein(a: str, b: str) -> int:
	"""Standard-Levenshtein-Distanz (iterativ, O(n) Speicher)."""
	if a == b:
		return 0
	m, n = len(a), len(b)
	if m < n:
		a, b, m, n = b, a, n, m
	dp = list(range(n + 1))
	for i in range(1, m + 1):
		vorher	= dp[0]
		dp[0]	= i
		for j in range(1, n + 1):
			temp	= dp[j]
			dp[j]	= vorher if a[i - 1] == b[j - 1] else 1 + min(dp[j], dp[j - 1], vorher)
			vorher	= temp
	return dp[n]


def _ist_praefix_paar(a: str, b: str, mindestlaenge: int) -> bool:
	"""
	Gibt True zurück, wenn a ein Präfix von b ist (oder umgekehrt)
	und der Präfix mindestens `mindestlaenge` Zeichen lang ist.
	a und b müssen bereits lowercase sein.
	"""
	if a == b:
		return False
	kurz, lang = (a, b) if len(a) <= len(b) else (b, a)
	return len(kurz) >= mindestlaenge and lang.startswith(kurz)


# ---------------------------------------------------------------------------
# Konsolidierung
# ---------------------------------------------------------------------------


def _prioritaet(eintrag: dict) -> tuple:
	"""WP-Terme zuerst, dann nach Häufigkeit (desc), dann alphabetisch."""
	return (
		0 if eintrag["quelle"] == "export" else 1,
		-eintrag["wp_count"],
		eintrag["name"].lower(),
	)


def konsolidiere(
	wp_begriffe: list[dict],
	proposals_begriffe: dict[str, list[str]],
) -> tuple[list[dict], list[dict], list[dict]]:
	"""
	Führt alle Begriffe zusammen und bereinigt regelbasiert.

	Automatisch entfernt:
	  - Exakte Duplikate (case-insensitiv, beim Einlesen)
	  - Slug-Duplikate  (gleicher wp_slug, niedrigere Priorität entfernt)

	Grenzfälle (Linguisten-Entscheidung):
	  - Präfix-Paare  (A ist Präfix von B, |A| ≥ PRAEFIX_MINDESTLAENGE)
	  - Levenshtein-Paare  (Distanz ≤ LEVENSHTEIN_MAX_DISTANZ, |A|,|B| ≥ LEVENSHTEIN_MINDESTLAENGE)

	Rückgabe: (pool, entfernte, grenzfaelle)
	"""

	# ---- 1. Exakte Deduplizierung (case-insensitiv) ----

	alle: dict[str, dict] = {}	# lowercase-name → Eintrag

	for wp in wp_begriffe:
		name = wp.get("name", "").strip()
		if not name:
			continue
		alle[name.lower()] = {
			"name":		name,
			"quelle":	"export",
			"wp_id":	wp.get("id"),
			"wp_slug":	wp.get("slug", ""),
			"wp_count":	wp.get("count", 0),
		}

	neu_zaehler = 0
	for tags in proposals_begriffe.values():
		for tag in tags:
			key = tag.lower()
			if key not in alle:
				alle[key] = {
					"name":		tag,
					"quelle":	"proposal",
					"wp_id":	None,
					"wp_slug":	"",
					"wp_count":	0,
				}
				neu_zaehler += 1

	log.info(
		"Nach exakter Deduplizierung: %d Begriffe  "
		"(%d aus WP-Export, %d neue aus Proposals)",
		len(alle), len(wp_begriffe), neu_zaehler,
	)

	alle_eintraege = list(alle.values())

	if not alle_eintraege:
		return [], [], []

	# ---- 2. Slug-Duplikate entfernen ----
	# Gleicher wp_slug → niedrigere Priorität entfernen

	slug_index: dict[str, int] = {}	# wp_slug → Index in alle_eintraege
	entfernt:   set[int]       = set()
	entfernte:  list[dict]     = []

	for idx, e in enumerate(alle_eintraege):
		slug = e["wp_slug"]
		if not slug:
			continue
		if slug in slug_index:
			gewinner_idx = slug_index[slug]
			gewinner     = alle_eintraege[gewinner_idx]
			# Niedrigere Priorität entfernen
			if _prioritaet(e) < _prioritaet(gewinner):
				# e ist bevorzugter → alten Gewinner ersetzen
				entfernt.add(gewinner_idx)
				entfernte.append({
					"name":         gewinner["name"],
					"quelle":       gewinner["quelle"],
					"grund":        "slug_duplikat",
					"duplikat_von": e["name"],
					"wp_slug":      slug,
				})
				log.info(
					"SLUG-DUPLIKAT  %r ersetzt durch %r  (slug=%s)",
					gewinner["name"], e["name"], slug,
				)
				slug_index[slug] = idx
			else:
				entfernt.add(idx)
				entfernte.append({
					"name":         e["name"],
					"quelle":       e["quelle"],
					"grund":        "slug_duplikat",
					"duplikat_von": gewinner["name"],
					"wp_slug":      slug,
				})
				log.info(
					"SLUG-DUPLIKAT  %r entfernt  (Duplikat von %r, slug=%s)",
					e["name"], gewinner["name"], slug,
				)
		else:
			slug_index[slug] = idx

	# ---- 3. Grenzfälle: Präfix-Paare und Levenshtein-Paare ----

	grenzfaelle: list[dict] = []
	aktive = [
		(idx, e) for idx, e in enumerate(alle_eintraege)
		if idx not in entfernt
	]

	# Für O(n²)-Vergleich reicht die Größe eines Schlagwort-Pools (< 1000 Terme)
	for pos_a, (idx_a, e_a) in enumerate(aktive):
		name_a	= e_a["name"]
		lower_a	= name_a.lower()

		for idx_b, e_b in aktive[pos_a + 1:]:
			name_b	= e_b["name"]
			lower_b	= name_b.lower()

			# Präfix-Prüfung
			if _ist_praefix_paar(lower_a, lower_b, PRAEFIX_MINDESTLAENGE):
				grenzfaelle.append({
					"typ":    "praefix",
					"term_a": name_a,
					"term_b": name_b,
					"hinweis": (
						f"{'»' + name_a + '«'} ist Präfix von {'»' + name_b + '«'}"
						if lower_b.startswith(lower_a) else
						f"{'»' + name_b + '«'} ist Präfix von {'»' + name_a + '«'}"
					),
				})
				continue		# kein zusätzlicher Levenshtein-Check nötig

			# Levenshtein-Prüfung (nur bei ausreichender Länge beider Terme)
			if (
				len(name_a) >= LEVENSHTEIN_MINDESTLAENGE
				and len(name_b) >= LEVENSHTEIN_MINDESTLAENGE
			):
				dist = _levenshtein(lower_a, lower_b)
				if dist <= LEVENSHTEIN_MAX_DISTANZ:
					grenzfaelle.append({
						"typ":         "levenshtein",
						"term_a":      name_a,
						"term_b":      name_b,
						"distanz":     dist,
						"hinweis":     f"Levenshtein-Distanz {dist} – möglicher Tippfehler",
					})
					log.info(
						"LEVENSHTEIN  %r ↔ %r  (Distanz %d)",
						name_a, name_b, dist,
					)

	# ---- 4. Pool zusammenstellen ----

	pool = [e for idx, e in enumerate(alle_eintraege) if idx not in entfernt]
	pool.sort(key=_prioritaet)

	return pool, entfernte, grenzfaelle


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Modul 7: Schlagwort-Pool-Konsolidierung"
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Analysiert und zeigt Ergebnisse, schreibt keine Ausgabedateien",
	)
	args = parser.parse_args()

	log.info("=== Schlagwort-Konsolidierung gestartet ===")
	log.info(
		"Präfix-Mindestlänge: %d  |  Levenshtein-Max: %d  |  Levenshtein-Mindestlänge: %d",
		PRAEFIX_MINDESTLAENGE, LEVENSHTEIN_MAX_DISTANZ, LEVENSHTEIN_MINDESTLAENGE,
	)

	wp_begriffe        = lade_wp_schlagwoerter()
	proposals_begriffe = lade_proposals_schlagwoerter()

	if not wp_begriffe and not proposals_begriffe:
		log.error(
			"Keine Begriffe gefunden – zuerst Proposals generieren "
			"oder WP-Export durchführen."
		)
		sys.exit(1)

	pool, entfernte, grenzfaelle = konsolidiere(wp_begriffe, proposals_begriffe)

	# Grenzfall-Typen zählen für die Ausgabe
	n_praefix      = sum(1 for g in grenzfaelle if g["typ"] == "praefix")
	n_levenshtein  = sum(1 for g in grenzfaelle if g["typ"] == "levenshtein")

	log.info(
		"Ergebnis: %d im Pool · %d entfernt · %d Grenzfälle (%d Präfix, %d Levenshtein)",
		len(pool), len(entfernte), len(grenzfaelle), n_praefix, n_levenshtein,
	)

	print()
	print(f"  Pool-Größe:    {len(pool):>5}  Begriffe")
	print(f"  Entfernt:      {len(entfernte):>5}  Duplikate (exakt + Slug)")
	print(f"  Grenzfälle:    {len(grenzfaelle):>5}  Paare zur Linguisten-Prüfung")
	print(f"    davon Präfix:       {n_praefix:>4}")
	print(f"    davon Levenshtein:  {n_levenshtein:>4}")
	print()

	if args.dry_run:
		log.info("Dry-Run – keine Dateien geschrieben.")
		return

	POOL_DATEI.write_text(
		json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8"
	)
	ENTFERNT_DATEI.write_text(
		json.dumps(entfernte, ensure_ascii=False, indent=2), encoding="utf-8"
	)
	GRENZFALL_DATEI.write_text(
		json.dumps(grenzfaelle, ensure_ascii=False, indent=2), encoding="utf-8"
	)

	log.info("=== Fertig ===")
	log.info("Pool:       %s", POOL_DATEI)
	log.info("Entfernt:   %s", ENTFERNT_DATEI)
	log.info("Grenzfälle: %s", GRENZFALL_DATEI)


if __name__ == "__main__":
	main()
