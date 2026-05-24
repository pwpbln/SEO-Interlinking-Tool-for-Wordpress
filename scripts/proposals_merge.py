"""
proposals_merge.py – Modul 3c: Proposal-Merger

Führt zwei Proposal-Dateien für denselben Batch zusammen
(z. B. Claude-Ausgabe + Gemini-Ausgabe).

Merge-Logik:
  link_vorschlaege   Union beider Listen; Duplikate per ziel_url entfernt.
  neue_schlagwoerter Union beider Listen; Pool-Begriffe bevorzugt; max. 5.
  verwandte_artikel  Union beider Listen; Duplikate per url entfernt.
  silo               Erste Datei gewinnt (Fallback: zweite).
  cornerstone        True wenn mindestens eine Quelle True sagt.
  linguist_meta      Beide Metadaten-Objekte als Array gespeichert.

Ausgabe:  analysis/proposals/merged/batch-XX-proposals.json

Aufruf – zwei explizite Dateien:
    .venv/bin/python scripts/proposals_merge.py \\
        analysis/proposals/batch-01-proposals.json \\
        analysis/proposals/batch-01-proposals_GEMINI.json

Aufruf – Batch-Bezeichner (sucht automatisch alle passenden Dateien):
    .venv/bin/python scripts/proposals_merge.py batch-01
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT	= Path(__file__).resolve().parent.parent
PROPOSALS_DIR	= PROJECT_ROOT / "analysis" / "proposals"
MERGED_DIR		= PROPOSALS_DIR / "merged"
POOL_DATEI		= PROJECT_ROOT / "data" / "taxonomie" / "pool-final.json"
LOG_DIR			= PROJECT_ROOT / "logs"

MERGED_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "proposals_merge.log"

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

# Steuerzeichen U+0000–U+001F außer \t \n \r
_STEUERZEICHEN = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

MAX_SCHLAGWOERTER = 5

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _norm_url(url: str) -> str:
	"""Normalisiert eine URL für Duplikat-Vergleiche."""
	return url.strip().rstrip("/").lower()


def lade_proposals(pfad: Path) -> tuple[list[dict], dict | None]:
	"""
	Liest eine Proposal-Datei; unterstützt reines Array und Wrapper-Format.
	Rückgabe: (proposals_liste, linguist_meta_oder_None)
	"""
	try:
		text = _STEUERZEICHEN.sub(' ', pfad.read_text(encoding="utf-8"))
		daten = json.loads(text)
	except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
		log.error("Datei nicht lesbar (%s): %s", pfad.name, exc)
		sys.exit(1)

	if isinstance(daten, list):
		return daten, None
	if isinstance(daten, dict) and isinstance(daten.get("proposals"), list):
		return daten["proposals"], daten.get("linguist_meta")
	log.error(
		"%s: unbekanntes Format – weder Array noch Wrapper-Objekt.",
		pfad.name,
	)
	sys.exit(1)


def lade_pool_namen() -> set[str]:
	"""Liest pool-final.json und gibt eine Menge normalisierter Begriffsnamen zurück."""
	if not POOL_DATEI.exists():
		log.warning("pool-final.json nicht gefunden – keine Pool-Priorisierung.")
		return set()
	try:
		pool = json.loads(POOL_DATEI.read_text(encoding="utf-8"))
		return {e["name"].strip().lower() for e in pool if isinstance(e, dict) and e.get("name")}
	except Exception as exc:
		log.warning("pool-final.json nicht lesbar: %s", exc)
		return set()


# ---------------------------------------------------------------------------
# Merge-Logik
# ---------------------------------------------------------------------------


def _merge_schlagwoerter(
	tags_a: list[str],
	tags_b: list[str],
	pool_namen: set[str],
) -> list[str]:
	"""
	Union beider Tag-Listen; Pool-Begriffe zuerst; max. MAX_SCHLAGWOERTER.
	Reihenfolge innerhalb jeder Gruppe: alphabetisch.
	"""
	gesehen: set[str] = set()
	alle: list[str] = []
	for tag in tags_a + tags_b:
		key = tag.strip().lower()
		if key and key not in gesehen:
			gesehen.add(key)
			alle.append(tag.strip())

	pool_tags  = sorted([t for t in alle if t.lower() in pool_namen], key=str.lower)
	other_tags = sorted([t for t in alle if t.lower() not in pool_namen], key=str.lower)
	return (pool_tags + other_tags)[:MAX_SCHLAGWOERTER]


def _merge_links(
	links_a: list[dict],
	links_b: list[dict],
) -> list[dict]:
	"""Union beider Link-Listen; Duplikate per normalisierter ziel_url entfernt."""
	gesehen: set[str] = set()
	ergebnis: list[dict] = []
	for link in links_a + links_b:
		key = _norm_url(link.get("ziel_url", ""))
		if key and key not in gesehen:
			gesehen.add(key)
			ergebnis.append(link)
	return ergebnis


def _merge_verwandte(
	va_a: list[dict],
	va_b: list[dict],
) -> list[dict]:
	"""Union beider verwandte_artikel-Listen; Duplikate per normalisierter url entfernt."""
	gesehen: set[str] = set()
	ergebnis: list[dict] = []
	for va in va_a + va_b:
		key = _norm_url(va.get("url", ""))
		if key and key not in gesehen:
			gesehen.add(key)
			ergebnis.append(va)
	return ergebnis


def _merge_eintrag(a: dict, b: dict, pool_namen: set[str]) -> dict:
	"""Führt zwei Einträge mit gleichem Slug zusammen."""
	slug		= a.get("slug") or b.get("slug", "")
	silo		= a.get("silo") or b.get("silo", "")
	cornerstone	= bool(a.get("cornerstone")) or bool(b.get("cornerstone"))

	tags = _merge_schlagwoerter(
		a.get("neue_schlagwoerter") or [],
		b.get("neue_schlagwoerter") or [],
		pool_namen,
	)
	links = _merge_links(
		a.get("link_vorschlaege") or [],
		b.get("link_vorschlaege") or [],
	)
	verwandte = _merge_verwandte(
		a.get("verwandte_artikel") or [],
		b.get("verwandte_artikel") or [],
	)

	eintrag: dict = {
		"slug":               slug,
		"silo":               silo,
		"neue_schlagwoerter": tags,
		"link_vorschlaege":   links,
		"cornerstone":        cornerstone,
	}
	if verwandte:
		eintrag["verwandte_artikel"] = verwandte
	return eintrag


def merge(
	eintraege_a: list[dict],
	eintraege_b: list[dict],
	pool_namen: set[str],
) -> list[dict]:
	"""
	Führt zwei Proposal-Listen zusammen.
	Reihenfolge: Slugs aus Datei A zuerst, dann Slugs nur in B.
	"""
	index_a = {e["slug"]: e for e in eintraege_a if e.get("slug")}
	index_b = {e["slug"]: e for e in eintraege_b if e.get("slug")}

	ergebnis: list[dict] = []
	for slug, a in index_a.items():
		if slug in index_b:
			ergebnis.append(_merge_eintrag(a, index_b[slug], pool_namen))
			log.info("MERGE   %s", slug)
		else:
			ergebnis.append(a)
			log.info("NUR-A   %s", slug)

	for slug, b in index_b.items():
		if slug not in index_a:
			ergebnis.append(b)
			log.info("NUR-B   %s", slug)

	return ergebnis


# ---------------------------------------------------------------------------
# Ausgabe-Dateiname
# ---------------------------------------------------------------------------


def _ausgabe_pfad(pfad_a: Path) -> Path:
	"""
	Leitet den Ausgabepfad aus der ersten Eingabedatei ab.
	batch-01-proposals.json       → merged/batch-01-proposals.json
	batch-01-proposals_GEMINI.json→ merged/batch-01-proposals.json
	"""
	name = pfad_a.name
	# Variante-Suffix abschneiden: alles nach dem zweiten '-proposals'
	basis = re.sub(r'(-proposals).*$', r'\1', name) + ".json"
	return MERGED_DIR / basis


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Modul 3c: Proposal-Merger",
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	parser.add_argument(
		"eingabe",
		nargs="+",
		metavar="DATEI_ODER_BATCH",
		help=(
			"Zwei Proposal-Dateien (Pfad oder Dateiname in analysis/proposals/) "
			"oder ein Batch-Bezeichner wie 'batch-01'."
		),
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Ergebnis berechnen, aber nicht auf Disk schreiben.",
	)
	args = parser.parse_args()

	# ── Eingabedateien auflösen ──────────────────────────────────────────────

	dateien: list[Path] = []

	if len(args.eingabe) == 1:
		# Batch-Bezeichner: alle passenden Dateien im PROPOSALS_DIR suchen
		bezeichner = args.eingabe[0]
		# "batch-01", "01", "1" → "batch-01"
		m = re.match(r'^(?:batch-)?(\d{1,2})$', bezeichner)
		if m:
			bezeichner = f"batch-{int(m.group(1)):02d}"
		muster = f"{bezeichner}-proposals*.json"
		gefunden = sorted(PROPOSALS_DIR.glob(muster))
		# merged/-Unterordner ausschließen
		gefunden = [p for p in gefunden if p.parent == PROPOSALS_DIR]
		if len(gefunden) < 2:
			log.error(
				"Weniger als 2 Dateien für Muster '%s' in %s gefunden: %s",
				muster, PROPOSALS_DIR, [p.name for p in gefunden],
			)
			sys.exit(1)
		dateien = gefunden[:2]
		if len(gefunden) > 2:
			log.warning(
				"%d Dateien gefunden – nehme die ersten zwei: %s",
				len(gefunden), [p.name for p in dateien],
			)

	elif len(args.eingabe) == 2:
		for eingabe in args.eingabe:
			pfad = Path(eingabe)
			if not pfad.is_absolute():
				# Relativ zum CWD, dann relativ zu PROPOSALS_DIR
				pfad = pfad if pfad.exists() else PROPOSALS_DIR / pfad.name
			dateien.append(pfad)

	else:
		parser.error("Entweder einen Batch-Bezeichner oder genau zwei Dateipfade angeben.")

	for p in dateien:
		if not p.exists():
			log.error("Datei nicht gefunden: %s", p)
			sys.exit(1)

	pfad_a, pfad_b = dateien[0], dateien[1]
	log.info("=== Proposals-Merge ===")
	log.info("Datei A: %s", pfad_a.name)
	log.info("Datei B: %s", pfad_b.name)

	# ── Laden ───────────────────────────────────────────────────────────────

	eintraege_a, meta_a = lade_proposals(pfad_a)
	eintraege_b, meta_b = lade_proposals(pfad_b)
	pool_namen = lade_pool_namen()

	log.info(
		"Geladen: A=%d Einträge, B=%d Einträge, Pool=%d Begriffe",
		len(eintraege_a), len(eintraege_b), len(pool_namen),
	)

	# ── Merge ────────────────────────────────────────────────────────────────

	ergebnis = merge(eintraege_a, eintraege_b, pool_namen)

	# Statistik
	nur_a	= sum(1 for e in ergebnis if e["slug"] in
	              {x["slug"] for x in eintraege_a} - {x["slug"] for x in eintraege_b})
	nur_b	= sum(1 for e in ergebnis if e["slug"] in
	              {x["slug"] for x in eintraege_b} - {x["slug"] for x in eintraege_a})
	beide	= len(ergebnis) - nur_a - nur_b
	links	= sum(len(e.get("link_vorschlaege", [])) for e in ergebnis)

	# ── Ausgabe ──────────────────────────────────────────────────────────────

	# linguist_meta: beide als Array (None-Einträge überspringen)
	metas = [m for m in (meta_a, meta_b) if m is not None]
	ausgabe_obj: dict = {"proposals": ergebnis}
	if metas:
		ausgabe_obj["linguist_meta"] = metas

	ausgabe_pfad = _ausgabe_pfad(pfad_a)
	ausgabe_json = json.dumps(ausgabe_obj, ensure_ascii=False, indent=2)

	print()
	print(f"  Datei A:          {pfad_a.name}  ({len(eintraege_a)} Einträge)")
	print(f"  Datei B:          {pfad_b.name}  ({len(eintraege_b)} Einträge)")
	print(f"  Merged:           {len(ergebnis)} Einträge"
	      f"  (beide: {beide}, nur A: {nur_a}, nur B: {nur_b})")
	print(f"  Link-Vorschläge:  {links}")
	print(f"  Pool-Begriffe:    {len(pool_namen)}")
	print(f"  Ausgabe:          {ausgabe_pfad}")
	print()

	if args.dry_run:
		log.info("Dry-Run – keine Datei geschrieben.")
		return

	ausgabe_pfad.write_text(ausgabe_json, encoding="utf-8")
	log.info("=== Fertig ===  %s", ausgabe_pfad)


if __name__ == "__main__":
	main()
