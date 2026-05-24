"""
freigabe_server.py – Modul 5/8: Lokaler Freigabe-Server

Startet einen HTTP-Server auf localhost:8080 und zeigt Vorschläge
aus analysis/proposals/ einzeln im Browser an.  Der Benutzer
entscheidet per Klick (oder Tastenkürzel J/N) über Annahme oder
Ablehnung.

Modi (--modus):
    links        Verlinkungsvorschläge (Standard)
    tags         Schlagwort-Vorschläge pro Artikel
    kategorien   Silo- und Cornerstone-Zuordnung pro Artikel

Eingabe:
    analysis/proposals/batch-*-proposals.json
    data/embeddings/index.json  – Artikel-Index für Titel/URL-Lookup

Ausgabe:
    output/approved/links/        – angenommene Verlinkungen
    output/approved/tags/         – angenommene Schlagwörter
    output/approved/kategorien/   – angenommene Kategorie-Zuordnungen
    output/zurueckgestellt/<modus>.json – zurückgestellte Vorschläge
    logs/abgelehnt-<modus>.log    – abgelehnte Vorschläge

Aufruf:
    .venv/bin/python scripts/freigabe_server.py
    .venv/bin/python scripts/freigabe_server.py --modus tags
    .venv/bin/python scripts/freigabe_server.py --modus kategorien
    .venv/bin/python scripts/freigabe_server.py --nur-zurueckgestellt
    .venv/bin/python scripts/freigabe_server.py --port 9090
    .venv/bin/python scripts/freigabe_server.py --kein-browser
"""

import argparse
import html as html_lib
import json
import logging
import random
import re
import string
import threading
import sys
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT			= Path(__file__).resolve().parent.parent
PROPOSALS_DIR			= PROJECT_ROOT / "analysis" / "proposals"
APPROVED_DIR			= PROJECT_ROOT / "output" / "approved"
ZURUECKGESTELLT_DIR		= PROJECT_ROOT / "output" / "zurueckgestellt"
INDEX_DATEI				= PROJECT_ROOT / "data" / "embeddings" / "index.json"
PARSED_DIR				= PROJECT_ROOT / "data" / "parsed"
LOG_DIR					= PROJECT_ROOT / "logs"

APPROVED_DIR.mkdir(parents=True, exist_ok=True)
ZURUECKGESTELLT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / "env.local")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "freigabe_server.log"

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
# Gemeinsamer Zustand (prozesslokal; Single-User-Server)
# ---------------------------------------------------------------------------

_zustand: dict = {
	"proposals":		[],
	"index":			0,
	"angenommen":		0,
	"abgelehnt":		0,
	"zurueckgestellt":	0,
	"modus":			None,		# None = Startseite anzeigen
}

# Werden in main() / _wechsle_modus() auf den gewählten Modus gesetzt
_approved_dir:				Path = APPROVED_DIR / "links"
_abgelehnt_log:				Path = LOG_DIR / "abgelehnt-links.log"
_zurueckgestellt_datei:		Path = ZURUECKGESTELLT_DIR / "links.json"
_nur_zurueckgestellt:		bool = False
_zurueckgestellt_erledigt:	set[int] = set()		# Indizes die in --nur-zurueckgestellt verarbeitet wurden
_post_id_index:				dict[str, int] = {}		# slug → WordPress post_id (aus data/parsed/)
_artikel_index_cache:		dict[str, dict] = {}	# einmal geladen, für Modus-Wechsel wiederverwendet
_wp_update					= None					# lazy-import aus update_wordpress
_wp_verfuegbar:				bool | None = None		# None = noch nicht geprüft
_letzter_test_slug:			str | None = None		# für "Anderen Artikel wählen"

# ---------------------------------------------------------------------------
# Artikel-Index laden
# ---------------------------------------------------------------------------


def _lade_artikel_index() -> dict[str, dict]:
	"""
	Liest data/embeddings/index.json.
	Format der Datei: {"0": {slug, url, title}, "1": {...}, …}
	Rückgabe: {slug: {url, title}}
	"""
	if not INDEX_DATEI.exists():
		log.warning(
			"index.json nicht gefunden – Artikel-Titel und URLs nicht verfügbar.\n"
			"  Bitte zuerst scripts/embeddings.py ausführen."
		)
		return {}
	try:
		daten = json.loads(INDEX_DATEI.read_text(encoding="utf-8"))
		return {
			v["slug"]: {"url": v.get("url", ""), "title": v.get("title", "")}
			for v in daten.values()
			if isinstance(v, dict) and "slug" in v
		}
	except Exception as exc:
		log.warning("index.json nicht lesbar: %s", exc)
		return {}


def _slug_aus_url(url: str) -> str:
	"""Extrahiert den Slug aus einer WordPress-Post-URL (letztes Pfadsegment)."""
	return url.rstrip("/").rsplit("/", 1)[-1]


def _lade_post_id_index() -> dict[str, int]:
	"""
	Liest alle data/parsed/<slug>.json und gibt {slug: post_id} zurück.
	Einträge ohne gültige post_id werden übersprungen.
	"""
	index: dict[str, int] = {}
	if not PARSED_DIR.exists():
		return index
	for datei in PARSED_DIR.glob("*.json"):
		try:
			daten = json.loads(datei.read_text(encoding="utf-8"))
			slug    = daten.get("slug", "").strip()
			post_id = daten.get("post_id")
			if slug and isinstance(post_id, int):
				index[slug] = post_id
		except Exception:
			pass
	return index


# ---------------------------------------------------------------------------
# Fortschritts-Daten für Startseite
# ---------------------------------------------------------------------------


def _sammle_fortschritt() -> dict:
	"""
	Zählt Proposals und freigegebene Dateien für die Startseite.
	Reines Datei-Scanning ohne vollständiges Laden der Proposal-Inhalte.
	"""
	batches_gesamt = len(list((PROJECT_ROOT / "analysis" / "batches").glob("batch-*.md")))

	proposals_gueltig = 0
	proposals_fehler  = 0
	links_gesamt      = 0
	tags_gesamt       = 0
	kat_gesamt        = 0

	_st = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
	for pf in sorted(PROPOSALS_DIR.glob("batch-*-proposals.json")):
		try:
			roh = _st.sub(' ', pf.read_text(encoding="utf-8"))
			daten = json.loads(roh)
			if isinstance(daten, dict) and isinstance(daten.get("proposals"), list):
				eintraege = daten["proposals"]
			elif isinstance(daten, list):
				eintraege = daten
			else:
				proposals_fehler += 1
				continue
			proposals_gueltig += 1
			for e in eintraege:
				links_gesamt += len(e.get("link_vorschlaege", []))
				tags_gesamt  += len([
					t for t in e.get("neue_schlagwoerter", [])
					if isinstance(t, str) and t.strip()
				])
				if e.get("silo", "").strip():
					kat_gesamt += 1
		except (json.JSONDecodeError, OSError):
			proposals_fehler += 1

	def _n(pfad: Path) -> int:
		return len(list(pfad.glob("*.json"))) if pfad.exists() else 0

	return {
		"batches_gesamt":      batches_gesamt,
		"proposals_gueltig":   proposals_gueltig,
		"proposals_fehler":    proposals_fehler,
		"links_gesamt":        links_gesamt,
		"tags_gesamt":         tags_gesamt,
		"kat_gesamt":          kat_gesamt,
		"approved_links":      _n(APPROVED_DIR / "links"),
		"approved_tags":       _n(APPROVED_DIR / "tags"),
		"approved_kat":        _n(APPROVED_DIR / "kategorien"),
		"done_gesamt":         _n(APPROVED_DIR / "done"),
	}


# ---------------------------------------------------------------------------
# WordPress-Integration – Hilfsfunktionen
# ---------------------------------------------------------------------------


def _import_wp_update() -> bool:
	"""
	Lazy-Import von fuehre_update_aus aus update_wordpress.
	Rückgabe: True wenn Import erfolgreich.
	"""
	global _wp_update, _wp_verfuegbar
	if _wp_verfuegbar is not None:
		return _wp_verfuegbar
	try:
		import sys as _sys
		_scripts = str(Path(__file__).parent)
		if _scripts not in _sys.path:
			_sys.path.insert(0, _scripts)
		from update_wordpress import fuehre_update_aus as _fn
		_wp_update     = _fn
		_wp_verfuegbar = True
	except (ImportError, SystemExit) as exc:
		_wp_update     = None
		_wp_verfuegbar = False
		log.warning("update_wordpress nicht importierbar: %s", exc)
	return _wp_verfuegbar


def _lade_approved_slugs() -> dict[str, set[str]]:
	"""
	Gibt {modus: {slug, …}} für alle freigegebenen JSON-Dateien zurück.
	"""
	ergebnis: dict[str, set[str]] = {}
	for modus in ("links", "tags", "kategorien"):
		slugs: set[str] = set()
		pfad = APPROVED_DIR / modus
		if pfad.exists():
			for f in pfad.glob("*.json"):
				try:
					d = json.loads(f.read_text(encoding="utf-8"))
					s = d.get("quell_slug", "").strip()
					if s:
						slugs.add(s)
				except Exception:
					pass
		ergebnis[modus] = slugs
	return ergebnis


def _lade_proposals_fuer_slug(slug: str) -> dict[str, list[dict]]:
	"""
	Lädt alle freigegebenen Proposals für einen Slug aus allen drei Modi.
	Rückgabe: {"links": [...], "tags": [...], "kategorien": [...]}
	"""
	ergebnis: dict[str, list[dict]] = {"links": [], "tags": [], "kategorien": []}
	for modus in ("links", "tags", "kategorien"):
		pfad = APPROVED_DIR / modus
		if not pfad.exists():
			continue
		for f in sorted(pfad.glob("*.json")):
			try:
				d = json.loads(f.read_text(encoding="utf-8"))
				if d.get("quell_slug", "").strip() == slug:
					ergebnis[modus].append(d)
			except Exception:
				pass
	return ergebnis


# ---------------------------------------------------------------------------
# Konfigurations-Hilfsfunktionen (env.local, Pool, vorgeschlagene Tags)
# ---------------------------------------------------------------------------

ENV_LOCAL = PROJECT_ROOT / "env.local"
SEO_KONTEXT_DATEI = PROJECT_ROOT / "seo-kontext.md"
POOL_DATEI = PROJECT_ROOT / "data" / "taxonomie" / "pool-final.json"


def _lese_env_local() -> dict[str, str]:
	"""Liest env.local und gibt {KEY: VALUE} zurück (nur KEY=VALUE-Zeilen)."""
	werte: dict[str, str] = {}
	if not ENV_LOCAL.exists():
		return werte
	for zeile in ENV_LOCAL.read_text(encoding="utf-8").splitlines():
		z = zeile.strip()
		if z and not z.startswith("#") and "=" in z:
			k, _, v = z.partition("=")
			werte[k.strip()] = v.strip()
	return werte


_WS_TABLE = str.maketrans('', '', string.whitespace)


def _schreibe_env_local(neu: dict[str, str]) -> None:
	"""
	Aktualisiert oder ergänzt Schlüssel in env.local.
	Alle anderen Zeilen bleiben unverändert.
	WP_APP_PASSWORD wird vor dem Speichern von Whitespace befreit
	(WordPress liefert Application Passwords im Format "xxxx xxxx xxxx").
	"""
	if "WP_APP_PASSWORD" in neu:
		neu = {**neu, "WP_APP_PASSWORD": neu["WP_APP_PASSWORD"].translate(_WS_TABLE)}
	zeilen: list[str] = []
	gesetzt: set[str] = set()

	if ENV_LOCAL.exists():
		for zeile in ENV_LOCAL.read_text(encoding="utf-8").splitlines():
			z = zeile.strip()
			if z and not z.startswith("#") and "=" in z:
				k = z.partition("=")[0].strip()
				if k in neu:
					zeilen.append(f"{k}={neu[k]}")
					gesetzt.add(k)
					continue
			zeilen.append(zeile)

	for k, v in neu.items():
		if k not in gesetzt:
			zeilen.append(f"{k}={v}")

	ENV_LOCAL.write_text("\n".join(zeilen) + "\n", encoding="utf-8")


def _lese_pool_namen() -> list[str]:
	"""Gibt alphabetisch sortierte Begriffsnamen aus pool-final.json zurück."""
	if not POOL_DATEI.exists():
		return []
	try:
		pool = json.loads(POOL_DATEI.read_text(encoding="utf-8"))
		namen = [e["name"].strip() for e in pool if isinstance(e, dict) and e.get("name")]
		return sorted(namen, key=str.lower)
	except Exception:
		return []


def _sammle_vorgeschlagene_tags(pool_namen: set[str]) -> list[str]:
	"""
	Liest neue_schlagwoerter aus allen Proposal-Dateien.
	Rückgabe: alphabetisch sortierte Gesamtliste (dedup).
	"""
	alle: set[str] = set()
	_st = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
	for pf in sorted(PROPOSALS_DIR.glob("batch-*-proposals.json")):
		try:
			roh = _st.sub(' ', pf.read_text(encoding="utf-8"))
			daten = json.loads(roh)
			eintraege = (
				daten["proposals"] if isinstance(daten, dict)
				and isinstance(daten.get("proposals"), list)
				else daten if isinstance(daten, list)
				else []
			)
			for e in eintraege:
				for t in e.get("neue_schlagwoerter", []):
					if isinstance(t, str) and t.strip():
						alle.add(t.strip())
		except Exception:
			pass
	return sorted(alle, key=str.lower)


# ---------------------------------------------------------------------------
# Vorschläge laden – je Modus
# ---------------------------------------------------------------------------


def _lade_proposals_links(artikel_index: dict) -> list[dict]:
	"""
	Liest link_vorschlaege aus allen Proposal-Dateien.
	Jeder Link-Eintrag wird zu einem eigenen flachen Dict.
	Rückgabe: [{quell_slug, quell_titel, quell_url, kontext_satz,
	             ankertext, ziel_url, ziel_titel, begruendung}, …]
	"""
	dateien = sorted(PROPOSALS_DIR.glob("batch-*-proposals.json"))
	proposals: list[dict] = []

	for datei in dateien:
		try:
			roh = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ',
			             datei.read_text(encoding="utf-8"))
			eintraege_roh = json.loads(roh)
		except (json.JSONDecodeError, OSError) as exc:
			log.warning("Datei übersprungen (%s): %s", datei.name, exc)
			continue
		# Wrapper-Format {"proposals": [...]} oder reines Array
		if isinstance(eintraege_roh, list):
			eintraege = eintraege_roh
		elif isinstance(eintraege_roh, dict) and isinstance(eintraege_roh.get("proposals"), list):
			eintraege = eintraege_roh["proposals"]
		else:
			continue

		for eintrag in eintraege:
			slug = eintrag.get("slug", "").strip()
			if not slug:
				continue
			artikel		= artikel_index.get(slug, {})
			quell_url	= artikel.get("url", "")
			quell_titel	= artikel.get("title", slug)

			link_vorschlaege = eintrag.get("link_vorschlaege", [])
			for link in link_vorschlaege:
				ziel_url	= link.get("ziel_url", "").strip()
				ziel_slug	= _slug_aus_url(ziel_url)
				ziel_info	= artikel_index.get(ziel_slug, {})
				proposals.append({
					"quell_slug":	slug,
					"quell_titel":	quell_titel,
					"quell_url":	quell_url,
					"post_id":		_post_id_index.get(slug),
					"kontext_satz":	link.get("kontext_satz", ""),
					"ankertext":	link.get("ankertext", ""),
					"ziel_url":		ziel_url,
					"ziel_titel":	ziel_info.get("title", ""),
					"begruendung":	link.get("begruendung", ""),
					"quelldatei":	datei.name,
				})
			# Kein Link-Vorschlag, aber verwandte Artikel → Hinweis-Eintrag
			if not link_vorschlaege:
				verwandte = [
					v for v in eintrag.get("verwandte_artikel", [])
					if isinstance(v, dict)
				]
				if verwandte:
					proposals.append({
						"quell_slug":		slug,
						"quell_titel":		quell_titel,
						"quell_url":		quell_url,
						"_typ":				"verwandte_artikel",
						"verwandte_artikel":verwandte,
						"quelldatei":		datei.name,
					})

	log.info(
		"Links-Proposals geladen: %d  (aus %d Dateien)",
		len(proposals), len(dateien),
	)
	return proposals


def _lade_proposals_tags(artikel_index: dict) -> list[dict]:
	"""
	Liest neue_schlagwoerter aus allen Proposal-Dateien.
	Ein Eintrag pro Schlagwort (nicht pro Artikel), damit jedes Tag
	einzeln angenommen, abgelehnt oder zurückgestellt werden kann.
	Rückgabe: [{quell_slug, quell_titel, quell_url, schlagwort,
	             neue_schlagwoerter, alle_schlagwoerter, post_id}, …]
	"""
	dateien = sorted(PROPOSALS_DIR.glob("batch-*-proposals.json"))
	proposals: list[dict] = []

	for datei in dateien:
		try:
			roh = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ',
			             datei.read_text(encoding="utf-8"))
			eintraege_roh = json.loads(roh)
		except (json.JSONDecodeError, OSError) as exc:
			log.warning("Datei übersprungen (%s): %s", datei.name, exc)
			continue
		if isinstance(eintraege_roh, list):
			eintraege = eintraege_roh
		elif isinstance(eintraege_roh, dict) and isinstance(eintraege_roh.get("proposals"), list):
			eintraege = eintraege_roh["proposals"]
		else:
			continue

		for eintrag in eintraege:
			slug = eintrag.get("slug", "").strip()
			tags = [
				t.strip() for t in eintrag.get("neue_schlagwoerter", [])
				if isinstance(t, str) and t.strip()
			]
			if not slug or not tags:
				continue
			artikel = artikel_index.get(slug, {})
			quell_titel = artikel.get("title", slug)
			quell_url   = artikel.get("url", "")
			post_id     = _post_id_index.get(slug)
			# Ein Eintrag pro Schlagwort – konsistent mit Link-Modus
			for tag in tags:
				proposals.append({
					"quell_slug":           slug,
					"quell_titel":          quell_titel,
					"quell_url":            quell_url,
					"post_id":              post_id,
					"schlagwort":           tag,
					"neue_schlagwoerter":   [tag],   # für update_wordpress.py
					"alle_schlagwoerter":   tags,    # Kontext-Anzeige im Browser
					"quelldatei":           datei.name,
				})

	log.info(
		"Tags-Proposals geladen: %d  (aus %d Dateien)",
		len(proposals), len(dateien),
	)
	return proposals


def _lade_proposals_kategorien(artikel_index: dict) -> list[dict]:
	"""
	Liest kategorien, silo und cornerstone aus allen Proposal-Dateien.
	Ein Eintrag pro Artikel mit gesetztem Silo oder kategorien-Array.
	Rückgabe: [{quell_slug, quell_titel, quell_url, silo, kategorien, cornerstone}, …]
	"""
	dateien = sorted(PROPOSALS_DIR.glob("batch-*-proposals.json"))
	proposals: list[dict] = []

	for datei in dateien:
		try:
			roh = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ',
			             datei.read_text(encoding="utf-8"))
			eintraege_roh = json.loads(roh)
		except (json.JSONDecodeError, OSError) as exc:
			log.warning("Datei übersprungen (%s): %s", datei.name, exc)
			continue
		if isinstance(eintraege_roh, list):
			eintraege = eintraege_roh
		elif isinstance(eintraege_roh, dict) and isinstance(eintraege_roh.get("proposals"), list):
			eintraege = eintraege_roh["proposals"]
		else:
			continue

		for eintrag in eintraege:
			slug = eintrag.get("slug", "").strip()
			silo = eintrag.get("silo", "").strip()
			if not slug or not silo:
				continue
			artikel = artikel_index.get(slug, {})
			# kategorien: new field (list of WP category names); falls back to silo only
			kategorien = eintrag.get("kategorien")
			if not isinstance(kategorien, list):
				kategorien = []
			proposals.append({
				"quell_slug":	slug,
				"quell_titel":	artikel.get("title", slug),
				"quell_url":	artikel.get("url", ""),
				"post_id":		_post_id_index.get(slug),
				"silo":			silo,
				"kategorien":	kategorien,
				"cornerstone":	bool(eintrag.get("cornerstone", False)),
				"quelldatei":	datei.name,
			})

	log.info(
		"Kategorien-Proposals geladen: %d  (aus %d Dateien)",
		len(proposals), len(dateien),
	)
	return proposals


# ---------------------------------------------------------------------------
# CSS und JS (inline, kein CDN)
# ---------------------------------------------------------------------------

_CSS = """\
* { box-sizing: border-box; }
body {
	font-family: system-ui, -apple-system, sans-serif;
	max-width: 840px;
	margin: 2rem auto;
	padding: 0 1.25rem;
	background: #efefef;
	color: #222;
	line-height: 1.55;
}
.card {
	background: #fff;
	border-radius: 8px;
	padding: 2rem 2.5rem 2.5rem;
	box-shadow: 0 2px 12px rgba(0,0,0,.09);
}
.progress {
	font-size: .82rem;
	color: #999;
	border-bottom: 1px solid #eee;
	padding-bottom: .7rem;
	margin-bottom: 1.6rem;
	display: flex;
	align-items: center;
	flex-wrap: wrap;
	gap: .4rem;
}
.progress strong { color: #555; }
.progress .ok   { color: #2e7d32; font-weight: 600; }
.progress .no   { color: #b71c1c; font-weight: 600; }
.progress .wait { color: #e65100; font-weight: 600; }
.modus-badge {
	display: inline-block;
	padding: .15rem .55rem;
	border-radius: 99px;
	font-size: .7rem;
	font-weight: 700;
	text-transform: uppercase;
	letter-spacing: .07em;
}
.modus-links      { background: #e3f2fd; color: #1565c0; }
.modus-tags       { background: #e8f5e9; color: #2e7d32; }
.modus-kategorien { background: #fff3e0; color: #e65100; }
.label {
	font-size: .7rem;
	font-weight: 700;
	text-transform: uppercase;
	letter-spacing: .08em;
	color: #aaa;
	margin-top: 1.4rem;
	margin-bottom: .3rem;
}
h1 {
	font-size: 1.3rem;
	font-weight: 600;
	margin: 0 0 .2rem;
}
.permalink {
	font-size: .8rem;
	margin-bottom: .4rem;
}
.permalink a, .ziel-box a { color: #1a56aa; text-decoration: none; }
.permalink a:hover, .ziel-box a:hover { text-decoration: underline; }
.kontext-box {
	background: #fffde7;
	border-left: 4px solid #f9a825;
	padding: .75rem 1rem;
	border-radius: 0 4px 4px 0;
	font-size: .95rem;
}
mark {
	background: #ffe57f;
	padding: 0 2px;
	border-radius: 2px;
}
.ankertext-box {
	font-size: 1.05rem;
	font-weight: 700;
	padding: .35rem 0;
}
.ziel-box {
	font-family: ui-monospace, "Cascadia Code", monospace;
	font-size: .82rem;
	word-break: break-all;
}
.ziel-titel {
	font-family: system-ui, sans-serif;
	color: #666;
	font-size: .85rem;
	margin-top: .15rem;
}
.begruendung-box {
	background: #f0f4ff;
	border-left: 4px solid #4a6fa5;
	padding: .75rem 1rem;
	border-radius: 0 4px 4px 0;
	font-size: .95rem;
}
/* ── Verwandte Artikel (Hinweisblock) ── */
.verwandte-box {
	background: #f5f5f5;
	border: 1px solid #ddd;
	border-radius: 6px;
	padding: .9rem 1.1rem;
	margin-top: .4rem;
}
.verwandte-box ul { margin: .4rem 0 0 1.1rem; padding: 0; }
.verwandte-box li { margin-bottom: .5rem; font-size: .93rem; }
.verwandte-box .va-begruendung { color: #666; font-size: .85rem; }
/* ── Tag-Chips ── */
.chips {
	display: flex;
	flex-wrap: wrap;
	gap: .45rem;
	margin-top: .35rem;
}
.chip {
	display: inline-block;
	background: #e8f5e9;
	color: #1b5e20;
	border: 1px solid #a5d6a7;
	border-radius: 99px;
	padding: .2rem .8rem;
	font-size: .88rem;
	font-weight: 500;
}
.chip-aktiv {
	background: #1b5e20;
	color: #fff;
	border-color: #1b5e20;
	font-size: 1rem;
	padding: .3rem 1rem;
}
.chip-kontext {
	background: #f1f1f1;
	color: #888;
	border-color: #ddd;
}
.tag-aktuell {
	font-size: 1.35rem;
	font-weight: 700;
	color: #1b5e20;
	padding: .5rem 0 .2rem;
}
/* ── Startseite ── */
.start-grid {
	display: grid;
	gap: .65rem;
	margin-top: 1rem;
}
.btn-start {
	display: block;
	width: 100%;
	padding: 1rem 1.3rem;
	font-size: 1rem;
	font-weight: 700;
	border: none;
	border-radius: 7px;
	cursor: pointer;
	text-align: left;
	letter-spacing: .02em;
	transition: filter .12s;
	line-height: 1.3;
}
.btn-start:active { filter: brightness(.85); }
.btn-start .sub { font-weight: 400; opacity: .8; font-size: .88rem; }
.btn-start-links       { background: #1565c0; color: #fff; }
.btn-start-links:hover { filter: brightness(1.12); }
.btn-start-tags        { background: #2e7d32; color: #fff; }
.btn-start-tags:hover  { filter: brightness(1.12); }
.btn-start-kategorien       { background: #c45000; color: #fff; }
.btn-start-kategorien:hover { filter: brightness(1.12); }
.fort-zeile {
	display: flex;
	align-items: baseline;
	gap: .6rem;
	padding: .4rem 0;
	border-bottom: 1px solid #f0f0f0;
	font-size: .92rem;
}
.fort-zeile:last-child { border-bottom: none; }
.fort-label { flex: 1; color: #555; }
.fort-wert  { font-weight: 600; white-space: nowrap; min-width: 4rem; text-align: right; }
.fort-bar   {
	font-family: ui-monospace, "Cascadia Code", monospace;
	font-size: .72rem;
	color: #bbb;
	white-space: nowrap;
}
.fort-pct   { font-size: .8rem; color: #999; min-width: 2.8rem; text-align: right; }
.warn-box {
	background: #fff8e1;
	border: 1px solid #ffe082;
	border-radius: 6px;
	padding: .7rem 1rem;
	font-size: .88rem;
	color: #7b5800;
	margin-bottom: 1.2rem;
}
.back-link {
	display: inline-block;
	margin-top: 1.5rem;
	color: #888;
	font-size: .88rem;
	text-decoration: none;
}
.back-link:hover { color: #333; text-decoration: underline; }
/* ── WordPress-Bereich ── */
.wp-trennlinie {
	border: none;
	border-top: 2px solid #eee;
	margin: 1.8rem 0 1.3rem;
}
.wp-ausgegraut {
	opacity: .45;
	pointer-events: none;
	user-select: none;
}
.wp-hinweis-inaktiv {
	font-size: .84rem;
	color: #aaa;
	font-style: italic;
	margin-bottom: .7rem;
}
.btn-start-wp-test       { background: #00695c; color: #fff; }
.btn-start-wp-test:hover { filter: brightness(1.12); }
.btn-start-wp-alle {
	background: #b71c1c;
	color: #fff;
	font-size: .9rem;
	padding: .75rem 1rem;
}
.btn-start-wp-alle:hover { filter: brightness(1.12); }
.result-ok     { color: #2e7d32; font-weight: 700; }
.result-fehler { color: #b71c1c; font-weight: 700; }
.result-box {
	background: #f5f5f5;
	border: 1px solid #e0e0e0;
	border-radius: 5px;
	padding: .7rem 1rem;
	font-family: ui-monospace, "Cascadia Code", monospace;
	font-size: .76rem;
	white-space: pre-wrap;
	word-break: break-all;
	max-height: 260px;
	overflow-y: auto;
	margin-top: .4rem;
	color: #444;
}
.updates-log {
	background: #1a1a1a;
	color: #f0f0f0;
	border-radius: 6px;
	padding: .75rem 1rem;
	font-family: ui-monospace, "Cascadia Code", monospace;
	font-size: .8em;
	white-space: pre-wrap;
	word-break: break-all;
	max-height: 400px;
	overflow-y: auto;
	margin-top: 1.2rem;
	line-height: 1.5;
}
.log-ok      { color: #69f0ae; }
.log-fehler  { color: #ff5252; }
.log-warnung { color: #ffd740; }
.wp-artikel-box {
	background: #e8f5e9;
	border-left: 4px solid #2e7d32;
	padding: .7rem 1rem;
	border-radius: 0 4px 4px 0;
	font-size: .95rem;
	margin-bottom: .3rem;
}
.aend-zeile {
	display: flex;
	align-items: baseline;
	gap: .5rem;
	padding: .25rem 0;
	border-bottom: 1px solid #f5f5f5;
	font-size: .9rem;
}
.aend-zeile:last-child { border-bottom: none; }
.aend-pfeil { color: #bbb; }
.warn-rot {
	background: #ffebee;
	border: 1px solid #ef9a9a;
	border-radius: 6px;
	padding: .75rem 1rem;
	color: #7f0000;
	font-size: .92rem;
	margin-bottom: 1rem;
}
/* ── Konfigurations-Karte ── */
.konfig-card {
	background: #f8f8f8;
	border: 1px solid #ddd;
	border-radius: 6px;
	padding: 1.5em;
	margin-top: 2rem;
}
.konfig-titel {
	font-size: 1rem;
	font-weight: 700;
	margin: 0 0 1.3rem;
	color: #444;
}
.konfig-sektion { margin-bottom: 1.5rem; }
.konfig-sektion:last-child { margin-bottom: 0; }
.konfig-sektion-kopf {
	display: flex;
	justify-content: space-between;
	align-items: center;
	margin-bottom: .7rem;
}
.schloss-btn {
	background: none;
	border: 1px solid #ccc;
	border-radius: 4px;
	padding: .15rem .5rem;
	cursor: pointer;
	font-size: 1rem;
	line-height: 1.4;
}
.schloss-btn:hover { background: #e8e8e8; }
.konfig-feld-zeile {
	display: grid;
	grid-template-columns: 170px 1fr;
	align-items: center;
	gap: .5rem;
	margin-bottom: .5rem;
}
.konfig-feld-zeile label { font-size: .84rem; color: #666; }
.konfig-feld-zeile input {
	padding: .32rem .6rem;
	border: 1px solid #ccc;
	border-radius: 4px;
	font-size: .88rem;
	background: #fff;
	width: 100%;
}
.konfig-feld-zeile input:disabled { background: #f0f0f0; color: #bbb; }
.konfig-speichern-btn {
	padding: .45rem 1.2rem;
	background: #1565c0;
	color: #fff;
	border: none;
	border-radius: 5px;
	font-size: .9rem;
	font-weight: 600;
	cursor: pointer;
}
.konfig-speichern-btn:hover { filter: brightness(1.1); }
.konfig-btn-zeile {
	display: flex;
	gap: .6rem;
	align-items: center;
	margin-top: .6rem;
}
.verbindung-test-btn {
	padding: .45rem 1.1rem;
	background: #fff;
	color: #1565c0;
	border: 1.5px solid #1565c0;
	border-radius: 5px;
	font-size: .9rem;
	font-weight: 600;
	cursor: pointer;
}
.verbindung-test-btn:hover { background: #e8f0fe; }
.verbindung-ergebnis {
	margin-top: .55rem;
	font-size: .88rem;
	min-height: 1.3em;
}
.verbindung-ok  { color: #2e7d32; }
.verbindung-err { color: #c62828; }
.nur-lesen-badge {
	font-size: .63rem;
	font-weight: 700;
	text-transform: uppercase;
	letter-spacing: .07em;
	color: #aaa;
	margin-left: .5rem;
	vertical-align: middle;
}
.seo-pre {
	background: #f0f0f0;
	border-left: 4px solid #888;
	padding: .65rem 1rem;
	border-radius: 0 4px 4px 0;
	max-height: 300px;
	overflow-y: auto;
	font-size: .85em;
	font-family: ui-monospace, "Cascadia Code", monospace;
	cursor: not-allowed;
	white-space: pre-wrap;
	word-break: break-word;
	margin: .4rem 0 0;
	color: #444;
}
.pool-grid {
	display: grid;
	grid-template-columns: 1fr 1fr;
	gap: 1em;
	margin-top: .4rem;
}
.pool-grid textarea {
	width: 100%;
	height: 200px;
	font-size: .8em;
	font-family: ui-monospace, "Cascadia Code", monospace;
	border: 1px solid #ccc;
	border-radius: 4px;
	padding: .4rem .6rem;
	resize: vertical;
	background: #fff;
}
details summary {
	cursor: pointer;
	font-size: .82rem;
	color: #999;
	margin-top: .8rem;
	user-select: none;
}
details[open] summary { color: #666; }
details .hilfe-text {
	font-size: .82rem;
	color: #666;
	margin: .4rem 0 0;
	padding: .5rem .8rem;
	background: #efefef;
	border-radius: 4px;
	line-height: 1.6;
}
/* ── Kategorie / Cornerstone ── */
.kategorie-box {
	background: #fff3e0;
	border-left: 4px solid #fb8c00;
	padding: .75rem 1rem;
	border-radius: 0 4px 4px 0;
	font-size: 1rem;
	font-weight: 600;
}
.cornerstone-ja {
	display: inline-block;
	background: #fff9c4;
	color: #f57f17;
	border: 1px solid #ffe082;
	border-radius: 4px;
	padding: .2rem .65rem;
	font-size: .85rem;
	font-weight: 700;
	margin-top: .45rem;
}
.cornerstone-nein {
	display: inline-block;
	background: #f5f5f5;
	color: #9e9e9e;
	border: 1px solid #e0e0e0;
	border-radius: 4px;
	padding: .2rem .65rem;
	font-size: .85rem;
	font-weight: 600;
	margin-top: .45rem;
}
.buttons {
	display: flex;
	gap: 1rem;
	margin-top: 2rem;
}
.btn {
	flex: 1;
	padding: 1rem;
	font-size: 1rem;
	font-weight: 700;
	border: none;
	border-radius: 6px;
	cursor: pointer;
	letter-spacing: .025em;
	transition: filter .12s;
}
.btn:active { filter: brightness(.85); }
.btn-annehmen { background: #2e7d32; color: #fff; }
.btn-annehmen:hover { filter: brightness(1.1); }
.btn-ablehnen { background: #b71c1c; color: #fff; }
.btn-ablehnen:hover { filter: brightness(1.1); }
.btn-zurueckstellen { background: #f57f17; color: #fff; }
.btn-zurueckstellen:hover { filter: brightness(1.1); }
.hint {
	text-align: center;
	font-size: .75rem;
	color: #ccc;
	margin-top: .55rem;
}
.summary {
	text-align: center;
	padding: 3.5rem 2rem;
}
.summary h1 { font-size: 1.7rem; margin-bottom: 1.6rem; }
.stat { font-size: 1.1rem; margin: .5rem 0; }
.stat-ok { color: #2e7d32; font-weight: 700; }
.stat-no { color: #b71c1c; font-weight: 700; }
.pfad { margin-top: 1.5rem; font-size: .82rem; color: #888;
        font-family: ui-monospace, monospace; }
.leer { text-align: center; padding: 3rem 2rem; }
.leer h1 { font-size: 1.5rem; color: #999; }
.leer p  { color: #888; font-size: .95rem; }
.leer code { background: #f4f4f4; padding: .1rem .35rem;
             border-radius: 3px; font-family: ui-monospace, monospace; }
"""

_JS = """\
document.addEventListener('keydown', function(e) {
	if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
	var k = e.key.toLowerCase();
	if (k === 'j') { e.preventDefault(); document.querySelector('.btn-annehmen').click(); }
	if (k === 'n') { e.preventDefault(); document.querySelector('.btn-ablehnen').click(); }
	if (k === 's') { e.preventDefault(); document.querySelector('.btn-zurueckstellen').click(); }
});
"""

# ---------------------------------------------------------------------------
# HTML-Bausteine (wiederverwendet in allen Modi)
# ---------------------------------------------------------------------------


def _kopf(titel: str) -> str:
	return (
		'<!DOCTYPE html>\n'
		'<html lang="de">\n'
		'<head>\n'
		'  <meta charset="UTF-8">\n'
		'  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
		f'  <title>{html_lib.escape(titel)}</title>\n'
		f'  <style>{_CSS}</style>\n'
		'</head>\n'
		'<body>\n'
	)


def _fuss(mit_js: bool = False) -> str:
	if mit_js:
		return f'<script>{_JS}</script>\n</body>\n</html>'
	return '</body>\n</html>'


def _highlight(kontext: str, ankertext: str) -> str:
	"""HTML-escaped Kontext-Satz; Ankertext mit <mark> hervorgehoben."""
	kontext_safe	= html_lib.escape(kontext)
	ankertext_safe	= html_lib.escape(ankertext)
	if ankertext_safe:
		muster = re.compile(re.escape(ankertext_safe), re.IGNORECASE)
		kontext_safe = muster.sub(
			f'<mark>{ankertext_safe}</mark>', kontext_safe, count=1
		)
	return kontext_safe


def _progress_zeile(index: int, gesamt: int) -> str:
	modus		= _zustand["modus"]
	n_ok		= _zustand["angenommen"]
	n_no		= _zustand["abgelehnt"]
	n_wait		= _zustand["zurueckgestellt"]
	modus_label	= {"links": "Links", "tags": "Tags", "kategorien": "Kategorien"}.get(modus, modus)
	badge_extra	= " (zurückgestellt)" if _nur_zurueckgestellt else ""
	return (
		f'  <div class="progress">\n'
		f'    <span class="modus-badge modus-{html_lib.escape(modus)}">'
		f'{html_lib.escape(modus_label + badge_extra)}</span>\n'
		f'    Vorschlag <strong>{index + 1}</strong> von <strong>{gesamt}</strong>'
		f'    &nbsp;·&nbsp; <span class="ok">{n_ok} angenommen</span>'
		f'    &nbsp;·&nbsp; <span class="no">{n_no} abgelehnt</span>'
		f'    &nbsp;·&nbsp; <span class="wait">{n_wait} zurückgestellt</span>\n'
		f'  </div>\n'
	)


def _artikel_kopf(proposal: dict) -> str:
	"""Quellartikel-Block (Titel + Link) – in allen Modi gleich."""
	quell_titel	= html_lib.escape(proposal.get("quell_titel", proposal.get("quell_slug", "")))
	quell_url	= html_lib.escape(proposal.get("quell_url", ""))
	zeilen = (
		'  <div class="label">Quellartikel</div>\n'
		f'  <h1>{quell_titel}</h1>\n'
	)
	if quell_url:
		zeilen += (
			f'  <div class="permalink">'
			f'<a href="{quell_url}" target="_blank" rel="noopener">{quell_url}</a>'
			f'</div>\n'
		)
	return zeilen


def _formular_buttons(index: int) -> str:
	return (
		'  <form method="POST" action="/entscheiden">\n'
		f'    <input type="hidden" name="index" value="{index}">\n'
		'    <div class="buttons">\n'
		'      <button class="btn btn-annehmen" name="entscheidung" value="annehmen">'
		'&#10003; Annehmen [J]</button>\n'
		'      <button class="btn btn-zurueckstellen" name="entscheidung" value="zurueckstellen">'
		'&#8987; Zurückstellen [S]</button>\n'
		'      <button class="btn btn-ablehnen" name="entscheidung" value="ablehnen">'
		'&#10007; Ablehnen [N]</button>\n'
		'    </div>\n'
		'  </form>\n'
		'  <div class="hint">'
		'J = Annehmen &nbsp;|&nbsp; S = Zurückstellen &nbsp;|&nbsp; N = Ablehnen'
		'</div>\n'
	)


# ---------------------------------------------------------------------------
# Seitengeneratoren – je Modus
# ---------------------------------------------------------------------------


def _seite_vorschlag_links(proposal: dict, index: int, gesamt: int) -> bytes:
	"""Verlinkungsvorschlag oder Hinweisblock für verwandte Artikel."""
	# ── Sonderfall: nur verwandte Artikel, keine Link-Vorschläge ──
	if proposal.get("_typ") == "verwandte_artikel":
		eintraege = proposal.get("verwandte_artikel", [])
		li_html = ""
		for va in eintraege:
			titel		= html_lib.escape(va.get("titel", ""))
			url			= html_lib.escape(va.get("url", ""))
			begr		= html_lib.escape(va.get("begruendung", ""))
			link		= f'<a href="{url}" target="_blank" rel="noopener">{titel}</a>' if url else titel
			li_html	   += (
				f'    <li>{link}'
				+ (f'<br><span class="va-begruendung">{begr}</span>' if begr else "")
				+ "</li>\n"
			)
		weiter_btn = (
			f'<form method="post" action="/entscheiden" style="margin-top:1.4rem">'
			f'<input type="hidden" name="index" value="{index}">'
			f'<input type="hidden" name="entscheidung" value="weiter">'
			f'<button type="submit" style="background:#757575;color:#fff;'
			f'padding:.55rem 1.4rem;border:none;border-radius:5px;'
			f'font-size:1rem;cursor:pointer">Weiter →</button></form>\n'
		)
		seite = (
			_kopf(f"Verwandte Artikel – {index + 1} von {gesamt}")
			+ '<div class="card">\n'
			+ _progress_zeile(index, gesamt)
			+ _artikel_kopf(proposal)
			+ '  <div class="label">Thematisch verwandte Artikel (manuell prüfen)</div>\n'
			+ '  <div class="verwandte-box"><ul>\n'
			+ li_html
			+ '  </ul></div>\n'
			+ weiter_btn
			+ '</div>\n'
			+ _fuss(mit_js=False)
		)
		return seite.encode("utf-8")

	# ── Normaler Verlinkungsvorschlag ──
	kontext_html	= _highlight(proposal.get("kontext_satz", ""), proposal.get("ankertext", ""))
	ankertext		= html_lib.escape(proposal.get("ankertext", ""))
	ziel_url		= html_lib.escape(proposal.get("ziel_url", ""))
	ziel_titel		= html_lib.escape(proposal.get("ziel_titel", ""))
	begruendung		= html_lib.escape(proposal.get("begruendung", ""))
	ziel_titel_zeile = f'  <div class="ziel-titel">{ziel_titel}</div>\n' if ziel_titel else ""

	seite = (
		_kopf(f"Freigabe Links – {index + 1} von {gesamt}")
		+ '<div class="card">\n'
		+ _progress_zeile(index, gesamt)
		+ _artikel_kopf(proposal)
		+ '  <div class="label">Kontext-Satz</div>\n'
		+ f'  <div class="kontext-box">{kontext_html}</div>\n'
		+ '  <div class="label">Vorgeschlagener Ankertext</div>\n'
		+ f'  <div class="ankertext-box">{ankertext}</div>\n'
		+ '  <div class="label">Ziel-URL</div>\n'
		+ f'  <div class="ziel-box">'
		+ f'<a href="{ziel_url}" target="_blank" rel="noopener">{ziel_url}</a></div>\n'
		+ ziel_titel_zeile
		+ '  <div class="label">Begründung</div>\n'
		+ f'  <div class="begruendung-box">{begruendung}</div>\n'
		+ _formular_buttons(index)
		+ '</div>\n'
		+ _fuss(mit_js=True)
	)
	return seite.encode("utf-8")


def _seite_vorschlag_tags(proposal: dict, index: int, gesamt: int) -> bytes:
	"""
	Schlagwort-Vorschlag: ein Tag pro Schritt.
	Zeigt das aktuelle Schlagwort prominent + alle anderen Tags des Artikels
	als Kontext-Chips in Grau.
	"""
	aktuell  = proposal.get("schlagwort", "")
	alle     = proposal.get("alle_schlagwoerter", [aktuell])

	chips_html = ""
	for t in alle:
		css = "chip chip-aktiv" if t == aktuell else "chip chip-kontext"
		chips_html += f'    <span class="{css}">{html_lib.escape(t)}</span>\n'

	seite = (
		_kopf(f"Freigabe Tags – {index + 1} von {gesamt}")
		+ '<div class="card">\n'
		+ _progress_zeile(index, gesamt)
		+ _artikel_kopf(proposal)
		+ '  <div class="label">Schlagwort</div>\n'
		+ f'  <div class="tag-aktuell">{html_lib.escape(aktuell)}</div>\n'
		+ '  <div class="label">Alle Schlagwörter dieses Artikels</div>\n'
		+ '  <div class="chips">\n'
		+ chips_html
		+ '  </div>\n'
		+ _formular_buttons(index)
		+ '</div>\n'
		+ _fuss(mit_js=True)
	)
	return seite.encode("utf-8")


def _seite_vorschlag_kategorien(proposal: dict, index: int, gesamt: int) -> bytes:
	"""Kategorie-Vorschlag: Artikel-Kopf + Silo + WP-Kategorien + Cornerstone-Badge."""
	silo		= html_lib.escape(proposal.get("silo", "–"))
	kategorien	= proposal.get("kategorien", [])
	cornerstone	= proposal.get("cornerstone", False)
	cs_html		= (
		'<span class="cornerstone-ja">&#9733; Cornerstone-Artikel</span>'
		if cornerstone else
		'<span class="cornerstone-nein">Kein Cornerstone</span>'
	)

	if kategorien:
		chips = "".join(
			f'<span class="chip chip-aktiv">{html_lib.escape(k)}</span>\n'
			for k in kategorien
		)
		kat_html = (
			'  <div class="label">WordPress-Kategorien</div>\n'
			f'  <div style="margin:.3rem 0">{chips}</div>\n'
		)
	else:
		kat_html = (
			'  <div class="label">WordPress-Kategorien</div>\n'
			'  <div style="margin:.3rem 0;color:#999;font-style:italic">'
			'Kein WordPress-Kategorie-Vorschlag – Silo wird als Fallback verwendet</div>\n'
		)

	seite = (
		_kopf(f"Freigabe Kategorien – {index + 1} von {gesamt}")
		+ '<div class="card">\n'
		+ _progress_zeile(index, gesamt)
		+ _artikel_kopf(proposal)
		+ '  <div class="label">Silo (Analyse-Kategorie)</div>\n'
		+ f'  <div class="kategorie-box">{silo}</div>\n'
		+ kat_html
		+ '  <div class="label">Cornerstone-Status</div>\n'
		+ f'  <div style="margin-top:.35rem">{cs_html}</div>\n'
		+ _formular_buttons(index)
		+ '</div>\n'
		+ _fuss(mit_js=True)
	)
	return seite.encode("utf-8")


def _seite_vorschlag(proposal: dict, index: int, gesamt: int) -> bytes:
	"""Dispatcher: ruft den modus-spezifischen Seitengenerator auf."""
	modus = _zustand["modus"]
	if modus == "tags":
		return _seite_vorschlag_tags(proposal, index, gesamt)
	if modus == "kategorien":
		return _seite_vorschlag_kategorien(proposal, index, gesamt)
	return _seite_vorschlag_links(proposal, index, gesamt)


# ---------------------------------------------------------------------------
# Modus-Wechsel (auch aus Startseite heraus)
# ---------------------------------------------------------------------------


def _konfiguration_card_html() -> str:
	"""Konfigurationskarte am Ende der Startseite."""
	env = _lese_env_local()
	wp_url  = html_lib.escape(env.get("WP_URL",  ""))
	wp_user = html_lib.escape(env.get("WP_USER", ""))
	pw_placeholder = (
		"gesetzt – leer lassen zum Beibehalten"
		if env.get("WP_APP_PASSWORD") else
		"Application Password eingeben"
	)

	# ── Abschnitt 2: SEO-Kontext ─────────────────────────────────────────
	seo_text = ""
	if SEO_KONTEXT_DATEI.exists():
		seo_text = html_lib.escape(SEO_KONTEXT_DATEI.read_text(encoding="utf-8"))

	# ── Abschnitt 3: Pool / vorgeschlagene Tags ───────────────────────────
	pool_namen  = _lese_pool_namen()
	pool_set    = {n.lower() for n in pool_namen}
	vorgeschlagen = _sammle_vorgeschlagene_tags(pool_set)

	pool_text = "\n".join(pool_namen)
	n_pool    = len(pool_namen)

	neu_tags  = [t for t in vorgeschlagen if t.lower() not in pool_set]
	n_neu     = len(neu_tags)
	vorschlag_zeilen = []
	for t in vorgeschlagen:
		marker = " ✓" if t.lower() in pool_set else ""
		vorschlag_zeilen.append(html_lib.escape(t) + marker)
	vorschlag_text = "\n".join(vorschlag_zeilen)

	# ── Lock-Toggle + Verbindungstest JS (inline) ────────────────────────
	js = (
		"(function(){"
		# Schloss-Toggle
		"var b=document.getElementById('schloss-btn');"
		"b.onclick=function(){"
		"var lock=this.textContent==='\\u{1F512}';"
		"this.textContent=lock?'\\u{1F513}':'\\u{1F512}';"
		"document.querySelectorAll('.wp-verbindungsfeld').forEach(function(f){f.disabled=!lock;});"
		"document.getElementById('konfig-btn-zeile').style.display=lock?'flex':'none';"
		"document.getElementById('verbindung-ergebnis').textContent='';"
		"};"
		# Verbindungstest-Button
		"document.getElementById('verbindung-testen-btn').onclick=function(){"
		"var el=document.getElementById('verbindung-ergebnis');"
		"el.textContent='Teste…'; el.className='verbindung-ergebnis';"
		"fetch('/verbindung-testen',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'}})"
		".then(function(r){return r.json();})"
		".then(function(d){"
		"el.textContent=d.nachricht;"
		"el.className='verbindung-ergebnis '+(d.ok?'verbindung-ok':'verbindung-err');"
		"})"
		".catch(function(e){"
		"el.textContent='✗ Fehler: '+e;"
		"el.className='verbindung-ergebnis verbindung-err';"
		"});"
		"};"
		"})();"
	)

	return (
		'<div class="konfig-card">\n'
		'  <h2 class="konfig-titel">Projekt-Konfiguration &amp; &#220;bersicht</h2>\n'

		# ── Abschnitt 1: Verbindungsdaten ────────────────────────────────
		'  <div class="konfig-sektion">\n'
		'    <div class="konfig-sektion-kopf">\n'
		'      <span class="label" style="margin:0">WordPress-Verbindung</span>\n'
		'      <button id="schloss-btn" class="schloss-btn" type="button">&#128274;</button>\n'
		'    </div>\n'
		'    <form method="POST" action="/konfiguration-speichern">\n'
		'      <div class="konfig-feld-zeile">\n'
		'        <label for="kf-url">WordPress-Domain</label>\n'
		f'        <input id="kf-url" class="wp-verbindungsfeld" type="url" name="WP_URL"\n'
		f'               value="{wp_url}" placeholder="https://ihre-domain.de" disabled>\n'
		'      </div>\n'
		'      <div class="konfig-feld-zeile">\n'
		'        <label for="kf-user">Benutzername</label>\n'
		f'        <input id="kf-user" class="wp-verbindungsfeld" type="text" name="WP_USER"\n'
		f'               value="{wp_user}" placeholder="wp-benutzername" disabled>\n'
		'      </div>\n'
		'      <div class="konfig-feld-zeile">\n'
		'        <label for="kf-pw">Application Password</label>\n'
		f'        <input id="kf-pw" class="wp-verbindungsfeld" type="password" name="WP_APP_PASSWORD"\n'
		f'               placeholder="{html_lib.escape(pw_placeholder)}" disabled>\n'
		'      </div>\n'
		'      <div id="konfig-btn-zeile" class="konfig-btn-zeile" style="display:none">\n'
		'        <button id="konfig-speichern" class="konfig-speichern-btn" type="submit">'
		'Speichern</button>\n'
		'        <button id="verbindung-testen-btn" class="verbindung-test-btn" type="button">'
		'Verbindung testen</button>\n'
		'      </div>\n'
		'      <div id="verbindung-ergebnis" class="verbindung-ergebnis"></div>\n'
		'    </form>\n'
		'    <details>\n'
		'      <summary>Wie erzeuge ich ein Application Password?</summary>\n'
		'      <p class="hilfe-text">In WordPress: <strong>Benutzer &#8594; Profil &#8594; '
		'Anwendungspassw&#246;rter</strong> &#8594; Namen eingeben &#8594; '
		'&#8222;Neues Passwort hinzuf&#252;gen&#8220;. '
		'Das Passwort wird nur einmal angezeigt.</p>\n'
		'    </details>\n'
		'  </div>\n'

		# ── Abschnitt 2: SEO-Kontext ─────────────────────────────────────
		'  <div class="konfig-sektion">\n'
		'    <div class="label" style="margin-top:0">'
		'SEO-Kontext <span class="nur-lesen-badge">seo-kontext.md &nbsp;·&nbsp; nur lesen</span>'
		'</div>\n'
		f'    <pre class="seo-pre">{seo_text}</pre>\n'
		'  </div>\n'

		# ── Abschnitt 3: Pool / vorgeschlagene Tags ───────────────────────
		'  <div class="konfig-sektion">\n'
		'    <div class="label" style="margin-top:0">Schlagwort-Pool</div>\n'
		'    <div class="pool-grid">\n'
		'      <div>\n'
		f'        <div style="font-size:.82rem;color:#666;margin-bottom:.3rem">Pool ({n_pool} Begriffe)</div>\n'
		f'        <textarea readonly>{html_lib.escape(pool_text)}</textarea>\n'
		'      </div>\n'
		'      <div>\n'
		f'        <div style="font-size:.82rem;color:#666;margin-bottom:.3rem">'
		f'Vorgeschlagen ({n_neu} neu) &nbsp;<span style="color:#aaa;font-size:.75rem">&#10003; = im Pool</span></div>\n'
		f'        <textarea readonly>{vorschlag_text}</textarea>\n'
		'      </div>\n'
		'    </div>\n'
		'  </div>\n'

		f'  <script>{js}</script>\n'
		'</div>\n'
	)


def _wechsle_modus(modus: str) -> None:
	"""
	Setzt den aktiven Modus, lädt Proposals, aktualisiert alle Globals.
	Wird sowohl aus main() (--modus-Argument) als auch vom POST /starten
	(Klick auf Startseite) aufgerufen.
	"""
	global _approved_dir, _abgelehnt_log, _zurueckgestellt_datei

	_approved_dir          = APPROVED_DIR / modus
	_abgelehnt_log         = LOG_DIR / f"abgelehnt-{modus}.log"
	_zurueckgestellt_datei = ZURUECKGESTELLT_DIR / f"{modus}.json"
	_approved_dir.mkdir(parents=True, exist_ok=True)

	idx = _artikel_index_cache if _artikel_index_cache else _lade_artikel_index()

	if modus == "tags":
		proposals = _lade_proposals_tags(idx)
	elif modus == "kategorien":
		proposals = _lade_proposals_kategorien(idx)
	else:
		proposals = _lade_proposals_links(idx)

	_zustand["modus"]           = modus
	_zustand["proposals"]       = proposals
	_zustand["index"]           = 0
	_zustand["angenommen"]      = 0
	_zustand["abgelehnt"]       = 0
	_zustand["zurueckgestellt"] = 0

	log.info("Modus: %s  –  %d Proposals geladen", modus, len(proposals))


# ---------------------------------------------------------------------------
# Startseite
# ---------------------------------------------------------------------------


def _seite_start() -> bytes:
	"""Übersichtsseite mit Fortschritt und Modus-Buttons."""
	f = _sammle_fortschritt()

	def _fort_zeile(label: str, ist: int, gesamt: int) -> str:
		pct = round(ist / gesamt * 100) if gesamt else 0
		gef = round(pct / 100 * 14)
		bar = "█" * gef + "░" * (14 - gef)
		return (
			'  <div class="fort-zeile">\n'
			f'    <span class="fort-label">{html_lib.escape(label)}</span>\n'
			f'    <span class="fort-wert">{ist} / {gesamt}</span>\n'
			f'    <span class="fort-bar">{bar}</span>\n'
			f'    <span class="fort-pct">{pct} %</span>\n'
			'  </div>\n'
		)

	warn_html = ""
	if f["proposals_fehler"]:
		warn_html = (
			'  <div class="warn-box">\n'
			f'    &#9888;&nbsp; {f["proposals_fehler"]} Proposal-Datei(en) mit JSON-Fehlern –\n'
			'    bitte <code>proposal_import.py --validate</code> ausführen.\n'
			'  </div>\n'
		)

	seite = (
		_kopf("SEO Crawler – Freigabe")
		+ '<div class="card">\n'
		+ '  <div class="label" style="margin-top:0">SEO Crawler</div>\n'
		+ '  <h1 style="margin-bottom:1.6rem">Freigabe-Übersicht</h1>\n'
		+ warn_html
		+ '  <div class="label">Projektfortschritt</div>\n'
		+ _fort_zeile(
			"Proposals analysiert",
			f["proposals_gueltig"], f["batches_gesamt"],
		)
		+ _fort_zeile(
			"Links freigegeben",
			f["approved_links"], f["links_gesamt"],
		)
		+ _fort_zeile(
			"Tags freigegeben",
			f["approved_tags"], f["tags_gesamt"],
		)
		+ _fort_zeile(
			"Kategorien freigegeben",
			f["approved_kat"], f["kat_gesamt"],
		)
		+ _fort_zeile(
			"WordPress eingespielt",
			f["done_gesamt"],
			f["links_gesamt"] + f["tags_gesamt"] + f["kat_gesamt"],
		)
		+ '  <div class="label" style="margin-top:1.8rem">Modus wählen</div>\n'
		+ '  <form method="POST" action="/starten">\n'
		+ '    <div class="start-grid">\n'
		+ (
			f'      <button class="btn-start btn-start-links" name="modus" value="links">\n'
			f'        &#128279; Links bearbeiten'
			f'<br><span class="sub">{f["links_gesamt"]} Vorschläge &nbsp;·&nbsp; '
			f'{f["approved_links"]} freigegeben</span>\n'
			f'      </button>\n'
		)
		+ (
			f'      <button class="btn-start btn-start-tags" name="modus" value="tags">\n'
			f'        &#127991; Schlagwörter bearbeiten'
			f'<br><span class="sub">{f["tags_gesamt"]} Vorschläge &nbsp;·&nbsp; '
			f'{f["approved_tags"]} freigegeben</span>\n'
			f'      </button>\n'
		)
		+ (
			f'      <button class="btn-start btn-start-kategorien" name="modus" value="kategorien">\n'
			f'        &#128193; Kategorien bearbeiten'
			f'<br><span class="sub">{f["kat_gesamt"]} Artikel &nbsp;·&nbsp; '
			f'{f["approved_kat"]} freigegeben</span>\n'
			f'      </button>\n'
		)
		+ '    </div>\n'
		+ '  </form>\n'
		# ── WordPress-Übertragung ──────────────────────────────────────────────
		+ '  <hr class="wp-trennlinie">\n'
		+ '  <div class="label">WordPress-Übertragung</div>\n'
		+ _wp_abschnitt_html(f)
		+ '</div>\n'
		+ _konfiguration_card_html()
		+ _fuss()
	)
	return seite.encode("utf-8")


def _wp_abschnitt_html(f: dict) -> str:
	"""HTML für den WordPress-Abschnitt der Startseite."""
	aktiv = f["approved_links"] > 0 and f["approved_tags"] > 0 and f["approved_kat"] > 0
	cls   = "" if aktiv else " wp-ausgegraut"
	return (
		f'  <div class="start-grid{cls}">\n'
		+ (
			'' if aktiv else
			'    <p class="wp-hinweis-inaktiv">Erst alle drei Freigabe-Schritte abschlie&#223;en.</p>\n'
		)
		+ (
			'    <a href="/test-artikel" class="btn-start btn-start-wp-test">\n'
			'      &#128640; Testartikel senden'
			'<br><span class="sub">Einen zuf&#228;lligen Artikel &#252;bertragen und pr&#252;fen</span>\n'
			'    </a>\n'
		)
		+ (
			'    <a href="/alles-senden-bestaetigen" class="btn-start btn-start-wp-alle">\n'
			'      &#9888;&nbsp; Alles senden'
			'<br><span class="sub">Alle freigegebenen Artikel nach WordPress &#252;bertragen</span>\n'
			'    </a>\n'
		)
		+ '  </div>\n'
	)


# ---------------------------------------------------------------------------
# WordPress-Seiten
# ---------------------------------------------------------------------------


def _seite_test_artikel() -> bytes:
	"""
	Zeigt einen zufällig gewählten Artikel mit geplanten Änderungen.
	Bevorzugt Slugs die in allen drei Modi freigegeben sind.
	"""
	global _letzter_test_slug
	slugs_pro_modus = _lade_approved_slugs()
	alle_links  = slugs_pro_modus.get("links", set())
	alle_tags   = slugs_pro_modus.get("tags", set())
	alle_kat    = slugs_pro_modus.get("kategorien", set())

	# Schnittmenge bevorzugen, sonst links
	schnittmenge = alle_links & alle_tags & alle_kat
	pool = list(schnittmenge) if schnittmenge else list(alle_links)

	if not pool:
		return (
			_kopf("Test-Artikel")
			+ '<div class="card leer"><h1>Keine freigegebenen Artikel</h1>'
			+ '<p>Erst Freigabe-Schritte abschlie&#223;en.</p>'
			+ '<a class="back-link" href="/">&#8592; Zur&#252;ck</a></div>\n'
			+ _fuss()
		).encode("utf-8")

	# Letzten Slug möglichst ausschließen
	kandidaten = [s for s in pool if s != _letzter_test_slug] or pool
	slug = random.choice(kandidaten)
	_letzter_test_slug = slug

	proposals = _lade_proposals_fuer_slug(slug)
	# Titel + URL aus artikel_index_cache
	artikel = _artikel_index_cache.get(slug, {})
	titel   = html_lib.escape(artikel.get("title", slug))
	url     = html_lib.escape(artikel.get("url", ""))

	# ── Links ──────────────────────────────────────────────────────────────
	link_zeilen = ""
	for p in proposals["links"]:
		at  = html_lib.escape(p.get("ankertext", "–"))
		zu  = html_lib.escape(p.get("ziel_url", ""))
		zt  = html_lib.escape(p.get("ziel_titel", ""))
		link_zeilen += (
			f'    <li class="aend-zeile">'
			f'&#8220;{at}&#8221;<span class="aend-pfeil">&#8594;</span>'
			f'<a href="{zu}" target="_blank" rel="noopener" '
			f'style="font-size:.82rem;word-break:break-all">{zt or zu}</a></li>\n'
		)
	links_block = (
		'  <div class="label">Neue interne Links</div>\n'
		+ f'  <ul style="list-style:none;padding:0;margin:.3rem 0">\n{link_zeilen}  </ul>\n'
	) if link_zeilen else ""

	# ── Tags ───────────────────────────────────────────────────────────────
	alle_neuen_tags: list[str] = []
	for p in proposals["tags"]:
		t = p.get("schlagwort") or (p.get("neue_schlagwoerter") or [""])[0]
		if t:
			alle_neuen_tags.append(t)
	chips = "".join(
		f'<span class="chip" style="margin:.2rem">{html_lib.escape(t)}</span>'
		for t in alle_neuen_tags
	)
	tags_block = (
		'  <div class="label">Neue Schlagw&#246;rter</div>\n'
		+ f'  <div class="chips" style="margin:.3rem 0">{chips}</div>\n'
	) if chips else ""

	# ── Kategorien ─────────────────────────────────────────────────────────
	kat_block = ""
	if proposals["kategorien"]:
		p0  = proposals["kategorien"][0]
		silo = html_lib.escape(p0.get("silo", "–"))
		cs   = p0.get("cornerstone", False)
		cs_html = (
			'<span class="cornerstone-ja">&#9733; Cornerstone</span>'
			if cs else
			'<span class="cornerstone-nein">Kein Cornerstone</span>'
		)
		kat_block = (
			'  <div class="label">Kategorie &amp; Cornerstone</div>\n'
			+ f'  <div class="kategorie-box" style="margin:.3rem 0">{silo}</div>\n'
			+ f'  <div style="margin:.35rem 0">{cs_html}</div>\n'
		)

	seite = (
		_kopf("Test-Artikel senden")
		+ '<div class="card">\n'
		+ '  <div class="label" style="margin-top:0">WordPress-&#220;bertragung &rsaquo; Test-Artikel</div>\n'
		+ f'  <h1>{titel}</h1>\n'
		+ (f'  <div class="permalink"><a href="{url}" target="_blank" rel="noopener">{url}</a></div>\n' if url else "")
		+ '  <div style="margin:1.2rem 0 .4rem;font-size:.88rem;color:#888">'
		  'Geplante &#196;nderungen f&#252;r diesen Artikel:</div>\n'
		+ links_block + tags_block + kat_block
		+ '  <form method="POST" action="/test-artikel-senden" style="margin-top:1.6rem">\n'
		+ f'    <input type="hidden" name="slug" value="{html_lib.escape(slug)}">\n'
		+ '    <div class="buttons">\n'
		+ '      <button class="btn btn-annehmen" type="submit">&#128640; Diesen Artikel senden</button>\n'
		+ '    </div>\n'
		+ '  </form>\n'
		+ '  <a class="back-link" href="/test-artikel">&#8635; Anderen Artikel w&#228;hlen</a>\n'
		+ '  &nbsp;&nbsp;<a class="back-link" href="/">&#8592; Zur&#252;ck zur &#220;bersicht</a>\n'
		+ '</div>\n'
		+ _fuss()
	)
	return seite.encode("utf-8")


_UPDATES_LOG  = LOG_DIR / "updates.log"
_PROGRESS_FILE = Path("/tmp/seo_crawler_progress.json")

# Ergebnis-Store: Thread schreibt hierein, GET /ergebnis-fertig liest es aus
_transfer_ergebnis: dict | None = None
_transfer_titel: str = ""
_transfer_slug: str = ""


def _seite_fortschritt(titel: str, slug: str) -> bytes:
	"""Sofortantwort nach POST – zeigt Fortschrittsbalken mit JS-Polling."""
	slug_zeile = f'<div style="font-size:.82rem;color:#888;margin-bottom:.8rem">Slug: {html_lib.escape(slug)}</div>\n' if slug else ""
	seite = (
		_kopf("WordPress-Übertragung – läuft…")
		+ '<div class="card">\n'
		+ '  <div class="label" style="margin-top:0">WordPress-&#220;bertragung &rsaquo; Fortschritt</div>\n'
		+ f'  <h1>{html_lib.escape(titel)}</h1>\n'
		+ slug_zeile
		+ '  <div id="fp-status" style="font-size:.9rem;color:#555;margin-bottom:.5rem">Starte…</div>\n'
		+ '  <div style="background:#e0e0e0;border-radius:4px;height:18px;overflow:hidden;margin-bottom:.7rem">\n'
		+ '    <div id="fp-bar" style="height:100%;width:0%;background:#1565c0;transition:width .4s"></div>\n'
		+ '  </div>\n'
		+ '  <div id="fp-detail" style="font-size:.82rem;color:#666"></div>\n'
		+ '</div>\n'
		+ '<script>\n'
		+ '(function(){\n'
		+ '  var t=setInterval(function(){\n'
		+ '    fetch("/fortschritt").then(function(r){return r.json();}).then(function(d){\n'
		+ '      if(d.fertig){clearInterval(t);location.href="/wp-ergebnis";return;}\n'
		+ '      var pct=d.gesamt>0?Math.round(d.erledigt/d.gesamt*100):0;\n'
		+ '      document.getElementById("fp-bar").style.width=pct+"%";\n'
		+ '      document.getElementById("fp-status").textContent=\n'
		+ '        d.erledigt+" von "+d.gesamt+" Artikeln verarbeitet ("+pct+" %)";\n'
		+ '      document.getElementById("fp-detail").innerHTML=\n'
		+ '        (d.aktuell?"Aktuell: "+d.aktuell+"<br>":"")\n'
		+ '        +"OK: "+d.ok+"&nbsp;&nbsp;Fehler: "+d.fehler;\n'
		+ '    }).catch(function(){});\n'
		+ '  },1000);\n'
		+ '})();\n'
		+ '</script>\n'
		+ _fuss()
	)
	return seite.encode("utf-8")
_LOG_OK_RE      = re.compile(r'\bOK\b|\bFertig\b', re.IGNORECASE)
_LOG_FEHLER_RE  = re.compile(r'\bFEHLER\b|\bERROR\b', re.IGNORECASE)
_LOG_WARN_RE    = re.compile(r'\bWARNUNG\b|\bWARNING\b|\bWARN\b', re.IGNORECASE)


def _lese_updates_log(n: int = 100) -> str:
	"""Liest die letzten n Zeilen aus updates.log und gibt HTML zurück."""
	if not _UPDATES_LOG.exists():
		return '<span style="color:#888">(updates.log noch nicht vorhanden)</span>'
	try:
		zeilen = _UPDATES_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
	except OSError:
		return '<span style="color:#888">(Log nicht lesbar)</span>'

	zeilen = zeilen[-n:]
	teile: list[str] = []
	for z in zeilen:
		esc = html_lib.escape(z)
		if _LOG_FEHLER_RE.search(z):
			teile.append(f'<span class="log-fehler">{esc}</span>')
		elif _LOG_WARN_RE.search(z):
			teile.append(f'<span class="log-warnung">{esc}</span>')
		elif _LOG_OK_RE.search(z):
			teile.append(f'<span class="log-ok">{esc}</span>')
		else:
			teile.append(esc)
	return "\n".join(teile)


def _seite_wp_ergebnis(
	titel: str,
	slug_anzeige: str,
	ergebnisse: list[tuple[str, dict]],
) -> bytes:
	"""
	Gemeinsame Ergebnisseite nach WordPress-Übertragung.
	ergebnisse: [(modus_label, {"ok": int, "fehler": int, "log": [str]}), …]
	"""
	zeilen = ""
	for label, res in ergebnisse:
		ok_cls  = "result-ok"     if res["fehler"] == 0 else "result-fehler"
		feh_cls = "result-fehler" if res["fehler"] > 0  else ""
		zeilen += (
			f'  <div style="margin:.5rem 0">\n'
			f'    <strong>{html_lib.escape(label)}:</strong>\n'
			f'    <span class="{ok_cls}">{res["ok"]} OK</span>'
			+ (f' &nbsp; <span class="{feh_cls}">{res["fehler"]} Fehler</span>' if res["fehler"] else "")
			+ '\n'
			f'    <div class="result-box">{html_lib.escape(chr(10).join(res["log"][-60:]))}</div>\n'
			f'  </div>\n'
		)

	seite = (
		_kopf("Ergebnis – WordPress-Übertragung")
		+ '<div class="card">\n'
		+ '  <div class="label" style="margin-top:0">WordPress-&#220;bertragung &rsaquo; Ergebnis</div>\n'
		+ f'  <h1>{html_lib.escape(titel)}</h1>\n'
		+ (f'  <div style="font-size:.85rem;color:#888;margin-bottom:1rem">Slug: {html_lib.escape(slug_anzeige)}</div>\n' if slug_anzeige else "")
		+ zeilen
		+ '  <div class="label" style="margin-top:1.4rem">updates.log – letzte 100 Zeilen</div>\n'
		+ f'  <div class="updates-log">{_lese_updates_log()}</div>\n'
		+ '  <a class="back-link" href="/">&#8592; Zur&#252;ck zur &#220;bersicht</a>\n'
		+ '</div>\n'
		+ _fuss()
	)
	return seite.encode("utf-8")


def _seite_alles_senden_bestaetigen() -> bytes:
	"""Bestätigungsseite vor der Massen-Übertragung."""
	slugs = _lade_approved_slugs()
	n_links = len(slugs.get("links", set()))
	n_tags  = len(slugs.get("tags", set()))
	n_kat   = len(slugs.get("kategorien", set()))

	seite = (
		_kopf("Alles senden – Bestätigung")
		+ '<div class="card">\n'
		+ '  <div class="label" style="margin-top:0">WordPress-&#220;bertragung &rsaquo; Alle Artikel</div>\n'
		+ '  <h1>Alle Artikel &#252;bertragen</h1>\n'
		+ '  <div class="warn-rot">\n'
		+ '    &#9888;&nbsp; <strong>Diese Aktion kann nicht r&#252;ckg&#228;ngig gemacht werden.</strong><br>\n'
		+ '    Alle freigegebenen Proposals werden nach WordPress geschrieben\n'
		+ '    und in den <code>done/</code>-Ordner verschoben.\n'
		+ '  </div>\n'
		+ '  <ul style="margin:.5rem 0 1.4rem 1.2rem;font-size:.95rem">\n'
		+ f'    <li>{n_links} Artikel mit Link-&#196;nderungen</li>\n'
		+ f'    <li>{n_tags} Artikel mit Schlagwort-&#196;nderungen</li>\n'
		+ f'    <li>{n_kat} Artikel mit Kategorie-&#196;nderungen</li>\n'
		+ '  </ul>\n'
		+ '  <form method="POST" action="/alles-senden-ausfuehren">\n'
		+ '    <div class="buttons">\n'
		+ '      <button class="btn btn-ablehnen" type="submit">'
		  '&#9888;&nbsp; Jetzt alles senden</button>\n'
		+ '      <a href="/" class="btn btn-zurueckstellen" '
		  'style="text-align:center;text-decoration:none">Abbrechen</a>\n'
		+ '    </div>\n'
		+ '  </form>\n'
		+ '</div>\n'
		+ _fuss()
	)
	return seite.encode("utf-8")


def _seite_zusammenfassung() -> bytes:
	"""Abschluss-Seite nach dem letzten Vorschlag."""
	n_ok	= _zustand["angenommen"]
	n_no	= _zustand["abgelehnt"]
	# Pfad relativ zum Projektverzeichnis anzeigen
	try:
		pfad_anzeige = str(_approved_dir.relative_to(PROJECT_ROOT))
	except ValueError:
		pfad_anzeige = str(_approved_dir)

	n_wait	= _zustand["zurueckgestellt"]
	try:
		zr_pfad = str(_zurueckgestellt_datei.relative_to(PROJECT_ROOT))
	except ValueError:
		zr_pfad = str(_zurueckgestellt_datei)

	seite = (
		_kopf("Freigabe abgeschlossen")
		+ '<div class="card summary">\n'
		+ '  <h1>Alle Vorschl&#228;ge bearbeitet</h1>\n'
		+ f'  <div class="stat"><span class="stat-ok">{n_ok}</span> angenommen</div>\n'
		+ f'  <div class="stat"><span class="stat-no">{n_no}</span> abgelehnt</div>\n'
		+ f'  <div class="stat" style="color:#e65100;font-weight:700">{n_wait} zur&#252;ckgestellt</div>\n'
		+ f'  <div class="pfad">Angenommene Vorschl&#228;ge: {html_lib.escape(pfad_anzeige)}/</div>\n'
		+ (f'  <div class="pfad">Zur&#252;ckgestellt: {html_lib.escape(zr_pfad)}</div>\n' if n_wait else "")
		+ '  <a class="back-link" href="/">&#8592; Zur&#252;ck zur &#220;bersicht</a>\n'
		+ '</div>\n'
		+ _fuss()
	)
	return seite.encode("utf-8")


def _seite_keine_vorschlaege(modus: str) -> bytes:
	"""Wird angezeigt, wenn für den gewählten Modus keine Einträge gefunden wurden."""
	seite = (
		_kopf("Keine Vorschläge")
		+ '<div class="card leer">\n'
		+ '  <h1>Keine Vorschl&#228;ge gefunden</h1>\n'
		+ f'  <p>F&#252;r Modus <code>{html_lib.escape(modus)}</code> '
		+ 'liegen keine Eintr&#228;ge vor.</p>\n'
		+ '  <p>Zuerst Modul 3 (Proposal-Generator) ausf&#252;hren und '
		+ '<code>proposal_import.py --validate</code> pr&#252;fen.</p>\n'
		+ '  <a class="back-link" href="/">&#8592; Zur&#252;ck zur &#220;bersicht</a>\n'
		+ '</div>\n'
		+ _fuss()
	)
	return seite.encode("utf-8")


# ---------------------------------------------------------------------------
# Entscheidung verarbeiten
# ---------------------------------------------------------------------------


def _verarbeite_entscheidung(index: int, entscheidung: str) -> None:
	"""
	Angenommene Vorschläge → _approved_dir/<slug>-<ts>.json
	Abgelehnte Vorschläge  → _abgelehnt_log (append)
	"""
	proposals = _zustand["proposals"]
	if index >= len(proposals):
		return

	proposal	= proposals[index]
	ts			= datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
	slug		= proposal.get("quell_slug", "unbekannt")
	modus		= _zustand["modus"]

	if entscheidung == "annehmen":
		dateiname	= f"{slug}-{ts}.json"
		ausgabe		= _approved_dir / dateiname
		ausgabe.write_text(
			json.dumps(proposal, ensure_ascii=False, indent=2),
			encoding="utf-8",
		)
		_zustand["angenommen"] += 1
		log.info("ANGENOMMEN       [%s]  → %s", slug, dateiname)

	elif entscheidung == "ablehnen":
		# Modus-spezifische Log-Zeile
		if modus == "links":
			detail = (
				f"  Ankertext: \"{proposal.get('ankertext', '')}\""
				f"  Ziel: {proposal.get('ziel_url', '')}"
			)
		elif modus == "tags":
			detail = f"  Tags: {', '.join(proposal.get('neue_schlagwoerter', []))}"
		else:
			detail = (
				f"  Silo: {proposal.get('silo', '')}"
				f"  Cornerstone: {proposal.get('cornerstone', False)}"
			)

		zeile = f"{ts}  [{slug}]{detail}\n"
		with _abgelehnt_log.open("a", encoding="utf-8") as f:
			f.write(zeile)
		_zustand["abgelehnt"] += 1
		log.info("ABGELEHNT        [%s]", slug)

	elif entscheidung == "zurueckstellen":
		if _nur_zurueckgestellt:
			# In --nur-zurueckgestellt: Eintrag verbleibt in der Datei, kein erneutes Anhängen
			_zustand["zurueckgestellt"] += 1
			log.info("ZURÜCKGESTELLT♻  [%s]  (bleibt in Datei)", slug)
		else:
			# Normalmodus: an Zurückgestellt-Datei anhängen
			vorhandene: list = []
			if _zurueckgestellt_datei.exists():
				try:
					vorhandene = json.loads(_zurueckgestellt_datei.read_text(encoding="utf-8"))
					if not isinstance(vorhandene, list):
						vorhandene = []
				except (json.JSONDecodeError, OSError):
					vorhandene = []
			vorhandene.append({**proposal, "_ts": ts})
			_zurueckgestellt_datei.write_text(
				json.dumps(vorhandene, ensure_ascii=False, indent=2),
				encoding="utf-8",
			)
			_zustand["zurueckgestellt"] += 1
			log.info("ZURÜCKGESTELLT   [%s]  → %s", slug, _zurueckgestellt_datei.name)


# ---------------------------------------------------------------------------
# HTTP-Handler
# ---------------------------------------------------------------------------


class FreigabeHandler(BaseHTTPRequestHandler):
	"""Minimaler HTTP-Handler; verwaltet Freigabe-Workflow per GET/POST."""

	def log_message(self, format: str, *args) -> None:
		# stdlib-Access-Log unterdrücken; eigenes Logging übernimmt
		pass

	def _sende_html(self, body: bytes, status: int = 200) -> None:
		self.send_response(status)
		self.send_header("Content-Type", "text/html; charset=utf-8")
		self.send_header("Content-Length", str(len(body)))
		self.send_header("Cache-Control", "no-store")
		self.end_headers()
		self.wfile.write(body)

	def _redirect(self, pfad: str = "/vorschlag") -> None:
		self.send_response(302)
		self.send_header("Location", pfad)
		self.send_header("Cache-Control", "no-store")
		self.end_headers()

	def do_GET(self) -> None:
		if self.path == "/favicon.ico":
			self.send_response(204)
			self.end_headers()
			return

		if self.path in ("/", ""):
			# Startseite immer anzeigen (auch wenn Modus bereits aktiv)
			self._sende_html(_seite_start())
			return

		if self.path == "/fortschritt":
			if _PROGRESS_FILE.exists():
				try:
					daten = json.loads(_PROGRESS_FILE.read_text(encoding="utf-8"))
					daten["fertig"] = False
				except (OSError, ValueError):
					daten = {"gesamt": 0, "erledigt": 0, "aktuell": "", "ok": 0, "fehler": 0, "fertig": False}
			else:
				daten = {"gesamt": 0, "erledigt": 0, "aktuell": "", "ok": 0, "fehler": 0, "fertig": True}
			body = json.dumps(daten, ensure_ascii=False).encode("utf-8")
			self.send_response(200)
			self.send_header("Content-Type", "application/json; charset=utf-8")
			self.send_header("Content-Length", str(len(body)))
			self.end_headers()
			self.wfile.write(body)
			return

		if self.path == "/wp-ergebnis":
			if _transfer_ergebnis is not None:
				self._sende_html(
					_seite_wp_ergebnis(_transfer_titel, _transfer_slug, _transfer_ergebnis)
				)
			else:
				self._redirect("/")
			return

		if self.path == "/test-artikel":
			self._sende_html(_seite_test_artikel())
			return

		if self.path == "/alles-senden-bestaetigen":
			self._sende_html(_seite_alles_senden_bestaetigen())
			return

		if self.path == "/vorschlag":
			# Ohne aktiven Modus zurück zur Startseite
			if _zustand["modus"] is None:
				self._redirect("/")
				return

			proposals	= _zustand["proposals"]
			idx			= _zustand["index"]

			if not proposals:
				self._sende_html(_seite_keine_vorschlaege(_zustand["modus"]))
			elif idx >= len(proposals):
				self._sende_html(_seite_zusammenfassung())
			else:
				self._sende_html(
					_seite_vorschlag(proposals[idx], idx, len(proposals))
				)
			return

		self.send_error(404)

	def do_POST(self) -> None:
		laenge = int(self.headers.get("Content-Length", 0))
		body   = self.rfile.read(laenge).decode("utf-8")
		params = parse_qs(body)

		if self.path == "/starten":
			modus = params.get("modus", [""])[0]
			if modus in ("links", "tags", "kategorien"):
				_wechsle_modus(modus)
				self._redirect("/vorschlag")
			else:
				self._redirect("/")
			return

		if self.path == "/test-artikel-senden":
			slug = params.get("slug", [""])[0].strip()
			if not slug or not _import_wp_update():
				self._redirect("/")
				return

			def _run_test(slug: str = slug) -> None:
				global _transfer_ergebnis, _transfer_titel, _transfer_slug
				ergebnisse = []
				for m in ("links", "tags", "kategorien"):
					res = _wp_update(m, slug=slug, live=True)  # type: ignore[misc]
					ergebnisse.append((m.capitalize(), res))
				_transfer_titel    = "Testartikel gesendet"
				_transfer_slug     = slug
				_transfer_ergebnis = ergebnisse

			threading.Thread(target=_run_test, daemon=True).start()
			self._sende_html(_seite_fortschritt("Testartikel wird gesendet…", slug))
			return

		if self.path == "/alles-senden-ausfuehren":
			if not _import_wp_update():
				self._redirect("/")
				return

			def _run_alle() -> None:
				global _transfer_ergebnis, _transfer_titel, _transfer_slug
				ergebnisse = []
				for m in ("links", "tags", "kategorien"):
					res = _wp_update(m, slug=None, live=True)  # type: ignore[misc]
					ergebnisse.append((m.capitalize(), res))
				_transfer_titel    = "Alle Artikel gesendet"
				_transfer_slug     = ""
				_transfer_ergebnis = ergebnisse

			threading.Thread(target=_run_alle, daemon=True).start()
			self._sende_html(_seite_fortschritt("Alle Artikel werden gesendet…", ""))
			return

		if self.path == "/verbindung-testen":
			env   = _lese_env_local()
			wp_url  = env.get("WP_URL", "").rstrip("/")
			wp_user = env.get("WP_USER", "")
			wp_pw   = env.get("WP_APP_PASSWORD", "").translate(_WS_TABLE)
			ok      = False
			nachricht = ""
			if not wp_url or not wp_user or not wp_pw:
				nachricht = "✗ Credentials unvollständig – bitte WP_URL, WP_USER und WP_APP_PASSWORD setzen."
			else:
				try:
					import requests as _req
					resp = _req.get(
						f"{wp_url}/wp-json/wp/v2/users/me",
						auth=(wp_user, wp_pw),
						timeout=10,
					)
					if resp.status_code == 200:
						wp_name = resp.json().get("name") or resp.json().get("slug", "?")
						ok = True
						nachricht = f"✓ Verbunden als: {wp_name}"
						log.info("Verbindungstest OK – Benutzer: %s", wp_name)
					elif resp.status_code == 401:
						nachricht = "✗ Authentifizierung fehlgeschlagen (401)"
						log.warning("Verbindungstest: 401 Unauthorized")
					else:
						nachricht = f"✗ Fehler: HTTP {resp.status_code}"
						log.warning("Verbindungstest: HTTP %d", resp.status_code)
				except Exception as exc:
					errmsg = str(exc)
					if "NameResolutionError" in errmsg or "Failed to resolve" in errmsg or "getaddrinfo" in errmsg:
						nachricht = f"✗ Domain nicht erreichbar: {wp_url}"
					elif "ConnectionRefusedError" in errmsg or "Connection refused" in errmsg:
						nachricht = f"✗ Domain nicht erreichbar: Verbindung verweigert"
					elif "timeout" in errmsg.lower() or "timed out" in errmsg.lower():
						nachricht = f"✗ Domain nicht erreichbar: Timeout nach 10 s"
					else:
						nachricht = f"✗ Domain nicht erreichbar: {exc}"
					log.warning("Verbindungstest fehlgeschlagen: %s", exc)
			antwort = json.dumps({"ok": ok, "nachricht": nachricht}, ensure_ascii=False).encode("utf-8")
			self.send_response(200)
			self.send_header("Content-Type", "application/json; charset=utf-8")
			self.send_header("Content-Length", str(len(antwort)))
			self.end_headers()
			self.wfile.write(antwort)
			return

		if self.path == "/konfiguration-speichern":
			neu: dict[str, str] = {}
			wp_url = params.get("WP_URL", [""])[0].strip()
			wp_user = params.get("WP_USER", [""])[0].strip()
			wp_pw   = params.get("WP_APP_PASSWORD", [""])[0].strip()
			if wp_url:
				neu["WP_URL"] = wp_url
			if wp_user:
				neu["WP_USER"] = wp_user
			if wp_pw:
				neu["WP_APP_PASSWORD"] = wp_pw
			if neu:
				try:
					_schreibe_env_local(neu)
					log.info("env.local aktualisiert: %s", list(neu.keys()))
				except OSError as exc:
					log.error("env.local schreiben fehlgeschlagen: %s", exc)
			self._redirect("/")
			return

		if self.path != "/entscheiden":
			self.send_error(404)
			return

		try:
			index = int(params.get("index", ["0"])[0])
		except (ValueError, IndexError):
			index = 0

		entscheidung = params.get("entscheidung", [""])[0]

		# Nur verarbeiten, wenn der Index zum aktuellen Zustand passt
		# (verhindert Doppel-Submit nach Browser-Back)
		if entscheidung == "weiter" and index == _zustand["index"]:
			# Hinweis-Eintrag (verwandte_artikel) – nur Index vorrücken, nichts schreiben
			_zustand["index"] = index + 1
		elif entscheidung in ("annehmen", "ablehnen", "zurueckstellen"):
			if index == _zustand["index"]:
				_verarbeite_entscheidung(index, entscheidung)
				_zustand["index"] = index + 1
				# In --nur-zurueckgestellt: erledigte Indizes für spätere Bereinigung merken
				if _nur_zurueckgestellt and entscheidung in ("annehmen", "ablehnen"):
					_zurueckgestellt_erledigt.add(index)

		self._redirect("/vorschlag")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def _cleanup_zurueckgestellt() -> None:
	"""
	Entfernt in --nur-zurueckgestellt erledigte Einträge aus der Datei.
	Einträge die erneut zurückgestellt wurden (nicht in _zurueckgestellt_erledigt)
	verbleiben in der Datei.
	"""
	if not _zurueckgestellt_erledigt:
		return
	try:
		vorhandene = json.loads(_zurueckgestellt_datei.read_text(encoding="utf-8"))
		if not isinstance(vorhandene, list):
			return
	except (json.JSONDecodeError, OSError):
		return
	verbleibend = [e for i, e in enumerate(vorhandene) if i not in _zurueckgestellt_erledigt]
	_zurueckgestellt_datei.write_text(
		json.dumps(verbleibend, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)
	log.info(
		"Zurückgestellt-Datei bereinigt: %d erledigt entfernt, %d verbleiben",
		len(_zurueckgestellt_erledigt), len(verbleibend),
	)


def main() -> None:
	global _nur_zurueckgestellt, _post_id_index, _artikel_index_cache
	global _approved_dir, _abgelehnt_log, _zurueckgestellt_datei

	parser = argparse.ArgumentParser(
		description="SEO Crawler – Modul 5/8: Lokaler Freigabe-Server"
	)
	parser.add_argument(
		"--modus",
		choices=["links", "tags", "kategorien"],
		default=None,
		metavar="MODUS",
		help=(
			"Direkt in einen Modus starten: links | tags | kategorien. "
			"Ohne --modus öffnet die Startseite."
		),
	)
	parser.add_argument(
		"--nur-zurueckgestellt",
		action="store_true",
		help="Nur zurückgestellte Vorschläge anzeigen (erfordert --modus)",
	)
	parser.add_argument(
		"--port",
		type=int,
		default=8080,
		metavar="PORT",
		help="Port für den lokalen HTTP-Server (Standard: 8080)",
	)
	parser.add_argument(
		"--kein-browser",
		action="store_true",
		help="Browser nicht automatisch öffnen",
	)
	args = parser.parse_args()

	# --nur-zurueckgestellt setzt --modus voraus
	if args.nur_zurueckgestellt and args.modus is None:
		parser.error("--nur-zurueckgestellt erfordert --modus links|tags|kategorien")

	_nur_zurueckgestellt = args.nur_zurueckgestellt

	# Gemeinsamen Artikel-Index einmalig laden; _wechsle_modus() verwendet ihn
	_artikel_index_cache = _lade_artikel_index()
	_post_id_index       = _lade_post_id_index()
	log.info("Artikel-Index: %d Einträge  |  Post-ID-Index: %d Einträge",
	         len(_artikel_index_cache), len(_post_id_index))

	# Direkt-Modus: Proposals sofort laden (wie bisher bei --modus-Argument)
	if args.modus is not None:
		if _nur_zurueckgestellt:
			# --nur-zurueckgestellt: Pfade manuell setzen, aus Datei laden
			_approved_dir          = APPROVED_DIR / args.modus
			_abgelehnt_log         = LOG_DIR / f"abgelehnt-{args.modus}.log"
			_zurueckgestellt_datei = ZURUECKGESTELLT_DIR / f"{args.modus}.json"
			_approved_dir.mkdir(parents=True, exist_ok=True)
			_zustand["modus"] = args.modus
			if not _zurueckgestellt_datei.exists():
				log.warning("Keine zurückgestellten Vorschläge in %s", _zurueckgestellt_datei)
				proposals = []
			else:
				try:
					proposals = json.loads(_zurueckgestellt_datei.read_text(encoding="utf-8"))
					if not isinstance(proposals, list):
						proposals = []
				except (json.JSONDecodeError, OSError) as exc:
					log.error("Zurückgestellt-Datei nicht lesbar: %s", exc)
					proposals = []
			log.info("Zurückgestellte Proposals geladen: %d", len(proposals))
			_zustand["proposals"] = proposals
		else:
			_wechsle_modus(args.modus)

	start_url = f"http://localhost:{args.port}/"
	server    = HTTPServer(("localhost", args.port), FreigabeHandler)

	log.info("=== Freigabe-Server gestartet ===")
	if args.modus:
		log.info("Modus:             %s%s", args.modus,
		         " (zurückgestellt)" if _nur_zurueckgestellt else "")
		log.info("Vorschläge gesamt: %d", len(_zustand["proposals"]))
		log.info("Ausgabe nach:      %s", _approved_dir)
	else:
		log.info("Modus:             Startseite (Modus wählen im Browser)")
	log.info("URL:               %s", start_url)
	log.info("Zum Beenden:       Strg+C")

	if not args.kein_browser:
		webbrowser.open(start_url)

	try:
		server.serve_forever()
	except KeyboardInterrupt:
		log.info(
			"Server gestoppt.  Angenommen: %d  Abgelehnt: %d  Zurückgestellt: %d",
			_zustand["angenommen"],
			_zustand["abgelehnt"],
			_zustand["zurueckgestellt"],
		)
		if _nur_zurueckgestellt:
			_cleanup_zurueckgestellt()
	finally:
		server.server_close()


if __name__ == "__main__":
	main()
