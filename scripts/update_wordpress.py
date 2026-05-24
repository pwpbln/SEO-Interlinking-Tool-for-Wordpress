"""
update_wordpress.py – Modul 6/9: WordPress-Rückspielen

Liest freigegebene Proposals aus output/approved/<modus>/ und trägt
sie via WordPress REST API in die Artikel ein.

SICHERHEITSREGEL: Standard ist immer --dry-run.
WordPress wird nur mit explizitem --live beschrieben.

Modi (--modus):
    links        Verlinkungen setzen (Standard)
    tags         Schlagwörter anhängen
    kategorien   Kategorie + Cornerstone-Status setzen

Ablauf – Modus links:
  1. Alle JSON-Dateien aus output/approved/links/ laden und nach
     quell_slug gruppieren.
  2. Pro Slug: Artikel-HTML einmalig per GET holen.
  3. Jeden Proposal sequenziell auf den HTML-Content anwenden.
  4. Im --live-Modus: geänderten Content per PATCH zurückschreiben.
  5. Verarbeitete Dateien nach output/approved/links/done/ verschieben.

Ablauf – Modus tags:
  1. Alle JSON-Dateien aus output/approved/tags/ laden.
  2. Pro Artikel: bestehende Tag-IDs per GET ermitteln.
  3. Neue Schlagwörter suchen (GET /wp/v2/tags?search=…); nicht
     vorhandene anlegen (POST /wp/v2/tags).
  4. PATCH /wp/v2/posts/{id} mit vereinigten Tag-IDs.
  5. Verarbeitete Dateien nach output/approved/tags/done/ verschieben.

Ablauf – Modus kategorien:
  1. Alle JSON-Dateien aus output/approved/kategorien/ laden.
  2. Silo-Name per SILO_KATEGORIE_MAP auf WP-Kategorie-Slug/Namen
     übersetzen; Kategorie-ID per GET ermitteln.
  3. PATCH /wp/v2/posts/{id} mit categories + ggf. Yoast-Cornerstone-Meta.
  4. Verarbeitete Dateien nach output/approved/kategorien/done/ verschieben.

Aufruf:
    .venv/bin/python scripts/update_wordpress.py
    .venv/bin/python scripts/update_wordpress.py --modus tags
    .venv/bin/python scripts/update_wordpress.py --modus kategorien
    .venv/bin/python scripts/update_wordpress.py --dry-run --modus links
    .venv/bin/python scripts/update_wordpress.py --live --modus links
    .venv/bin/python scripts/update_wordpress.py --live --slug mein-artikel
"""

import argparse
import html as html_lib
import json
import logging
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT		= Path(__file__).resolve().parent.parent
APPROVED_DIR		= PROJECT_ROOT / "output" / "approved"
LINKS_DIR			= APPROVED_DIR / "links"
TAGS_DIR			= APPROVED_DIR / "tags"
KATEGORIEN_DIR		= APPROVED_DIR / "kategorien"
LOG_DIR				= PROJECT_ROOT / "logs"
ENV_FILE			= PROJECT_ROOT / "env.local"
KATEGORIEN_EXPORT	= PROJECT_ROOT / "data" / "taxonomie" / "kategorien-export.json"

LINKS_DIR.mkdir(parents=True, exist_ok=True)
TAGS_DIR.mkdir(parents=True, exist_ok=True)
KATEGORIEN_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Konfiguration – Silo → WordPress-Kategorie-Name
# ---------------------------------------------------------------------------

# Tabelle anpassen, wenn sich Silo-Namen oder WP-Kategorienamen ändern.
# Schlüssel: Silo-String aus den Proposals (exaktes Match, case-sensitiv).
# Wert: Name der WP-Kategorie (wie in WordPress angelegt).
SILO_KATEGORIE_MAP: dict[str, str] = {
	"Theater und Bühne Berlin":	"Theater",
	"Medien und Journalismus":	"Medien",
	"Informatik":				"Informatik",
	# Weitere Silos hier ergänzen
}

# ---------------------------------------------------------------------------
# Konfiguration aus env.local
# ---------------------------------------------------------------------------

load_dotenv(ENV_FILE)

WP_URL			= os.getenv("WP_URL", "").rstrip("/")
WP_USER			= os.getenv("WP_USER", "")
WP_APP_PASSWORD	= os.getenv("WP_APP_PASSWORD", "")

if not all([WP_URL, WP_USER, WP_APP_PASSWORD]):
	print("FEHLER: WP_URL, WP_USER und WP_APP_PASSWORD müssen in env.local stehen.")
	sys.exit(1)

REST_BASE	= f"{WP_URL}/wp-json/wp/v2"
AUTH		= (WP_USER, WP_APP_PASSWORD)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

UPDATE_LOG    = LOG_DIR / "updates.log"
PROGRESS_FILE = Path("/tmp/seo_crawler_progress.json")

# Laufender Fortschrittsstand – wird von verarbeite_modus_* befüllt
_fp: dict = {}


def _fp_w() -> None:
	"""Schreibt _fp atomar in PROGRESS_FILE; ignoriert OS-Fehler."""
	try:
		PROGRESS_FILE.write_text(json.dumps(_fp, ensure_ascii=False), encoding="utf-8")
	except OSError:
		pass

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s  %(levelname)-7s  %(message)s",
	datefmt="%Y-%m-%d %H:%M:%S",
	handlers=[
		logging.FileHandler(UPDATE_LOG, encoding="utf-8"),
		logging.StreamHandler(sys.stdout),
	],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP-Session
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({
	"User-Agent": "SEOCrawler/1.0 (update_wordpress; respectful)",
})

# ---------------------------------------------------------------------------
# Gemeinsame Hilfsfunktionen – WordPress REST API
# ---------------------------------------------------------------------------


def hole_post(slug: str, post_id: int | None = None) -> dict | None:
	"""
	Ruft einen WordPress-Post ab.
	Wenn post_id gegeben ist, wird GET /wp/v2/posts/{post_id} bevorzugt
	(schneller, stabil); bei Fehler Fallback auf Slug-Suche.

	context=edit ist zwingend – nur damit liefert WordPress content.raw,
	unabhängig von _fields-Filtern.
	"""
	# context=edit liefert content.raw; _fields einschränken um Payload klein zu halten
	params_id   = {"context": "edit", "_fields": "id,slug,link,status,tags,categories,meta,content"}
	params_slug = {"context": "edit", "_fields": "id,slug,link,status,tags,categories,meta,content",
	               "slug": slug, "per_page": 2}

	if post_id is not None:
		try:
			resp = SESSION.get(
				f"{REST_BASE}/posts/{post_id}",
				params=params_id,
				auth=AUTH,
				timeout=20,
			)
			resp.raise_for_status()
			return resp.json()
		except requests.RequestException as exc:
			log.warning(
				"GET /posts/%d fehlgeschlagen (%s) – versuche Slug-Suche.",
				post_id, exc,
			)

	try:
		resp = SESSION.get(
			f"{REST_BASE}/posts",
			params=params_slug,
			auth=AUTH,
			timeout=20,
		)
		resp.raise_for_status()
		posts = resp.json()
	except requests.RequestException as exc:
		log.error("GET-Fehler (slug=%s): %s", slug, exc)
		return None

	if not posts:
		log.warning("Kein Post gefunden für Slug: %s", slug)
		return None

	if len(posts) > 1:
		log.warning(
			"Mehrere Posts mit Slug=%s gefunden – nehme den ersten (ID=%d).",
			slug, posts[0]["id"],
		)
	return posts[0]


def sende_patch(post_id: int, daten: dict) -> bool:
	"""
	Schickt einen PATCH-Request für einen Post.
	`daten` kann content, tags, categories, meta etc. enthalten.
	Gibt True bei HTTP 2xx zurück.
	"""
	try:
		resp = SESSION.patch(
			f"{REST_BASE}/posts/{post_id}",
			json=daten,
			auth=AUTH,
			timeout=30,
		)
		resp.raise_for_status()
		return True
	except requests.RequestException as exc:
		log.error("PATCH-Fehler (ID=%d): %s", post_id, exc)
		return False


def verschiebe_nach_done(pfade: list[Path], done_dir: Path) -> None:
	"""Verschiebt verarbeitete Dateien nach <modus_dir>/done/."""
	done_dir.mkdir(parents=True, exist_ok=True)
	for pfad in pfade:
		ziel = done_dir / pfad.name
		if ziel.exists():
			ts		= datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
			ziel	= done_dir / f"{pfad.stem}_{ts}{pfad.suffix}"
		shutil.move(str(pfad), str(ziel))
		log.debug("Verschoben: %s → done/%s", pfad.name, ziel.name)


# ---------------------------------------------------------------------------
# Modus links – HTML-Verlinkungen setzen
# ---------------------------------------------------------------------------

# Status-Codes
STATUS_OK				= "ok"
STATUS_DRY_RUN			= "dry_run"
STATUS_NICHT_GEFUNDEN	= "nicht_gefunden"
STATUS_MEHRDEUTIG		= "mehrdeutig"
STATUS_BEREITS_VORHANDEN = "bereits_vorhanden"
STATUS_ANKERTEXT_FEHLT	= "ankertext_fehlt"
STATUS_FEHLENDE_FELDER	= "fehlende_felder"
STATUS_ERSETZUNG_FEHLER	= "ersetzung_fehlgeschlagen"


def _normiere(text: str) -> str:
	"""Normiert Whitespace für robusten Textvergleich."""
	return " ".join(text.split())


def wende_proposal_an(raw_html: str, proposal: dict) -> tuple[str, str]:
	"""
	Versucht, den vorgeschlagenen Link im HTML-Content einzufügen.

	Strategie:
	  1. BeautifulSoup: Block-Element finden, dessen Text den kontext_satz enthält.
	  2. Eindeutigkeit prüfen (0 Treffer → nicht_gefunden, >1 → mehrdeutig).
	  3. Existierenden Link auf ziel_url prüfen (im ganzen Artikel).
	  4. Ankertext im Block-HTML durch <a href=...>ankertext</a> ersetzen.
	  5. Geändertes Block-HTML im raw_html-String tauschen.

	Rückgabe: (möglicherweise geändertes HTML, Status-String)
	"""
	kontext_satz	= proposal.get("kontext_satz", "").strip()
	ankertext		= proposal.get("ankertext", "").strip()
	ziel_url		= proposal.get("ziel_url", "").strip()

	if not all([kontext_satz, ankertext, ziel_url]):
		return raw_html, STATUS_FEHLENDE_FELDER

	soup			= BeautifulSoup(raw_html, "lxml")
	kontext_norm	= _normiere(kontext_satz)

	treffer: list = [
		tag for tag in soup.find_all(["p", "li", "blockquote", "h2", "h3", "h4", "h5", "td"])
		if kontext_norm in _normiere(tag.get_text())
	]

	if len(treffer) == 0:
		return raw_html, STATUS_NICHT_GEFUNDEN
	if len(treffer) > 1:
		return raw_html, STATUS_MEHRDEUTIG

	treffer_tag	= treffer[0]
	ziel_norm	= ziel_url.rstrip("/")

	for a in soup.find_all("a"):
		if a.get("href", "").rstrip("/") == ziel_norm:
			return raw_html, STATUS_BEREITS_VORHANDEN

	tag_str		= str(treffer_tag)
	anker_pat	= re.compile(re.escape(ankertext), re.IGNORECASE)

	if not anker_pat.search(tag_str):
		return raw_html, STATUS_ANKERTEXT_FEHLT

	for a in treffer_tag.find_all("a"):
		if ankertext.lower() in a.get_text().lower():
			return raw_html, STATUS_BEREITS_VORHANDEN

	link_html = (
		f'<a href="{html_lib.escape(ziel_url)}">'
		f'{html_lib.escape(ankertext)}'
		f'</a>'
	)
	neues_tag_str = anker_pat.sub(link_html, tag_str, count=1)

	if tag_str in raw_html:
		return raw_html.replace(tag_str, neues_tag_str, 1), STATUS_OK

	log.debug(
		"BS4-Tag nicht wörtlich in raw_html – versuche Direkt-Ersetzung "
		"(ankertext=%s)", ankertext
	)
	if anker_pat.search(raw_html):
		return anker_pat.sub(link_html, raw_html, count=1), STATUS_OK

	return raw_html, STATUS_ERSETZUNG_FEHLER


def lade_links_proposals(
	nur_slug: str | None = None,
) -> dict[str, list[tuple[dict, Path]]]:
	"""
	Liest alle JSON-Dateien aus output/approved/links/.
	Gruppiert nach quell_slug: {slug: [(proposal, pfad), …]}
	"""
	dateien = sorted(LINKS_DIR.glob("*.json"))
	if not dateien:
		log.warning("Keine Dateien in %s", LINKS_DIR)
		return {}

	gruppen: dict[str, list[tuple[dict, Path]]] = defaultdict(list)
	for datei in dateien:
		try:
			proposal = json.loads(re.sub(
				r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ',
				datei.read_text(encoding="utf-8")))
		except (json.JSONDecodeError, OSError) as exc:
			log.warning("Datei übersprungen (%s): %s", datei.name, exc)
			continue
		slug = proposal.get("quell_slug", "").strip()
		if not slug:
			log.warning("quell_slug fehlt in %s – übersprungen.", datei.name)
			continue
		if nur_slug and slug != nur_slug:
			continue
		gruppen[slug].append((proposal, datei))

	return dict(gruppen)


def verarbeite_slug_links(
	slug: str,
	proposals_mit_pfad: list[tuple[dict, Path]],
	dry_run: bool,
) -> dict:
	"""
	Holt einen Artikel, wendet alle Link-Proposals an und schreibt zurück.
	Rückgabe: {post_id, ergebnisse, geaendert}
	"""
	log.info("--- Verarbeite Slug: %s (%d Links) ---", slug, len(proposals_mit_pfad))

	ergebnisse: list[dict] = []
	ergebnis_basis = {"post_id": None, "ergebnisse": ergebnisse, "geaendert": False}

	post_id_hint = proposals_mit_pfad[0][0].get("post_id") if proposals_mit_pfad else None
	post = hole_post(slug, post_id_hint)
	if post is None:
		for _, pfad in proposals_mit_pfad:
			ergebnisse.append({"datei": pfad.name, "ankertext": "–", "status": "post_nicht_gefunden"})
		return ergebnis_basis

	post_id		= post["id"]
	raw_html	= post.get("content", {}).get("raw", "")

	if not raw_html:
		rendered     = post.get("content", {}).get("rendered", "")
		rendered_len = len(rendered)
		log.warning(
			"content.raw leer für Slug=%s (ID=%d)  "
			"content.rendered Länge=%d%s.",
			slug, post_id, rendered_len,
			" (rendered ebenfalls leer – Artikel möglicherweise wirklich leer)"
			if not rendered_len else " (rendered vorhanden – raw fehlt wegen fehlender Berechtigung?)",
		)
		log.debug(
			"API-Antwort für Slug=%s  HTTP-Status=200  Felder=%s",
			slug, list(post.keys()),
		)
		log.debug(
			"content-Objekt für Slug=%s: %s",
			slug, {k: (v[:120] + "…" if isinstance(v, str) and len(v) > 120 else v)
			       for k, v in post.get("content", {}).items()},
		)
		for _, pfad in proposals_mit_pfad:
			ergebnisse.append({"datei": pfad.name, "ankertext": "–", "status": "content_leer"})
		return {**ergebnis_basis, "post_id": post_id}

	ergebnis_basis["post_id"]	= post_id
	aktueller_html				= raw_html
	aenderungen					= 0

	for proposal, pfad in proposals_mit_pfad:
		ankertext = proposal.get("ankertext", "?")

		if dry_run:
			_, status = wende_proposal_an(aktueller_html, proposal)
			dry_status = STATUS_DRY_RUN if status == STATUS_OK else status
			log.info("DRY-RUN  [%s]  Ankertext: %-30s  Status: %s", slug, ankertext, dry_status)
			ergebnisse.append({"datei": pfad.name, "ankertext": ankertext, "status": dry_status})
			neues_html, _ = wende_proposal_an(aktueller_html, proposal)
			if status == STATUS_OK:
				aktueller_html = neues_html
		else:
			neues_html, status = wende_proposal_an(aktueller_html, proposal)
			if status == STATUS_OK:
				aktueller_html = neues_html
				aenderungen += 1
				log.info("OK        [%s]  Ankertext: %s", slug, ankertext)
			elif status == STATUS_BEREITS_VORHANDEN:
				log.info("SKIP      [%s]  Ankertext: %-30s  (%s)", slug, ankertext, status)
			else:
				log.warning("WARNUNG   [%s]  Ankertext: %-30s  Status: %s", slug, ankertext, status)
			ergebnisse.append({"datei": pfad.name, "ankertext": ankertext, "status": status})

	if not dry_run and aenderungen > 0:
		log.info("PATCH     [%s]  (ID=%d, %d Änderung(en))", slug, post_id, aenderungen)
		erfolg = sende_patch(post_id, {"content": aktueller_html})
		if not erfolg:
			for e in ergebnisse:
				if e["status"] == STATUS_OK:
					e["status"] = "api_fehler"
			log.error("PATCH fehlgeschlagen – Änderungen nicht gespeichert (slug=%s).", slug)
		else:
			ergebnis_basis["geaendert"] = True
			log.info("PATCH OK  [%s]  ID=%d", slug, post_id)
	elif not dry_run and aenderungen == 0:
		log.info("KEINE ÄNDERUNGEN  [%s]  – kein PATCH nötig.", slug)

	return ergebnis_basis


def verarbeite_modus_links(dry_run: bool, nur_slug: str | None) -> None:
	"""Hauptablauf für --modus links."""
	done_dir = LINKS_DIR / "done"
	gruppen  = lade_links_proposals(nur_slug)
	if not gruppen:
		log.info("Keine Link-Proposals in %s.", LINKS_DIR)
		return

	log.info(
		"Links-Slugs zu verarbeiten: %d  |  Proposals gesamt: %d",
		len(gruppen), sum(len(v) for v in gruppen.values()),
	)

	gesamt_ok = gesamt_skip = gesamt_warnungen = gesamt_patches = 0
	alle_pfade: list[Path] = []

	for slug, proposals_mit_pfad in gruppen.items():
		_fp["aktuell"] = slug; _fp_w()
		pfade = [p for _, p in proposals_mit_pfad]
		alle_pfade.extend(pfade)

		ergebnis = verarbeite_slug_links(slug, proposals_mit_pfad, dry_run)

		for e in ergebnis["ergebnisse"]:
			st = e["status"]
			if st in (STATUS_OK, STATUS_DRY_RUN):
				gesamt_ok += 1
			elif st == STATUS_BEREITS_VORHANDEN:
				gesamt_skip += 1
			else:
				gesamt_warnungen += 1

		if ergebnis["geaendert"]:
			gesamt_patches += 1

		if not dry_run:
			verschiebe_nach_done(pfade, done_dir)

		_fp["erledigt"] = _fp.get("erledigt", 0) + 1
		_fp["ok"] = gesamt_ok; _fp["fehler"] = gesamt_warnungen; _fp_w()

	_drucke_zusammenfassung(
		modus="links", dry_run=dry_run,
		gruppen_anz=len(gruppen),
		ok=gesamt_ok, skip=gesamt_skip, warnungen=gesamt_warnungen,
		patches=gesamt_patches, pfade_anz=len(alle_pfade),
	)


# ---------------------------------------------------------------------------
# Modus tags – Schlagwörter anhängen
# ---------------------------------------------------------------------------


def hole_oder_erstelle_tag(name: str) -> int | None:
	"""
	Sucht ein WP-Tag nach Name; legt es an, wenn nicht vorhanden.
	Rückgabe: Tag-ID oder None bei Fehler.
	"""
	try:
		resp = SESSION.get(
			f"{REST_BASE}/tags",
			params={"search": name, "per_page": 10, "_fields": "id,name"},
			auth=AUTH,
			timeout=15,
		)
		resp.raise_for_status()
		treffer = resp.json()
	except requests.RequestException as exc:
		log.error("GET tags Fehler (name=%s): %s", name, exc)
		return None

	# Exaktes Match suchen (case-insensitiv)
	for t in treffer:
		if t["name"].strip().lower() == name.strip().lower():
			return t["id"]

	# Nicht gefunden → anlegen
	try:
		resp = SESSION.post(
			f"{REST_BASE}/tags",
			json={"name": name},
			auth=AUTH,
			timeout=15,
		)
		resp.raise_for_status()
		neu = resp.json()
		log.info("TAG ERSTELLT  %r  (ID=%d)", name, neu["id"])
		return neu["id"]
	except requests.RequestException as exc:
		log.error("POST tags Fehler (name=%s): %s", name, exc)
		return None


def verarbeite_modus_tags(dry_run: bool, nur_slug: str | None) -> None:
	"""Hauptablauf für --modus tags."""
	done_dir = TAGS_DIR / "done"
	dateien  = sorted(TAGS_DIR.glob("*.json"))

	if not dateien:
		log.info("Keine Tag-Proposals in %s.", TAGS_DIR)
		return

	gesamt_ok = gesamt_fehler = 0
	alle_pfade: list[Path] = []

	for datei in dateien:
		try:
			proposal = json.loads(re.sub(
				r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ',
				datei.read_text(encoding="utf-8")))
		except (json.JSONDecodeError, OSError) as exc:
			log.warning("Datei übersprungen (%s): %s", datei.name, exc)
			continue

		slug = proposal.get("quell_slug", "").strip()
		if not slug or (nur_slug and slug != nur_slug):
			continue

		neue_tags = proposal.get("neue_schlagwoerter", [])
		if not neue_tags:
			log.warning("neue_schlagwoerter fehlt in %s – übersprungen.", datei.name)
			continue

		alle_pfade.append(datei)
		_fp["aktuell"] = slug; _fp_w()
		log.info("--- Tags für Slug: %s  (%d neue) ---", slug, len(neue_tags))

		post_id_hint = proposal.get("post_id")
		post = hole_post(slug, post_id_hint)
		if post is None:
			gesamt_fehler += 1
			continue

		post_id			= post["id"]
		bestehende_ids	= list(post.get("tags", []))

		if dry_run:
			log.info(
				"DRY-RUN  [%s]  würde Tags anhängen: %s",
				slug, ", ".join(neue_tags),
			)
			gesamt_ok += 1
			continue

		# Tag-IDs ermitteln / anlegen
		neue_ids: list[int] = []
		for tag_name in neue_tags:
			tag_id = hole_oder_erstelle_tag(tag_name)
			if tag_id is not None and tag_id not in bestehende_ids:
				neue_ids.append(tag_id)

		if not neue_ids:
			log.info("SKIP  [%s]  Alle Tags bereits vorhanden.", slug)
			gesamt_ok += 1
			verschiebe_nach_done([datei], done_dir)
			continue

		vereinigte_ids = bestehende_ids + neue_ids
		erfolg = sende_patch(post_id, {"tags": vereinigte_ids})

		_fp["erledigt"] = _fp.get("erledigt", 0) + 1
		_fp["ok"] = gesamt_ok; _fp["fehler"] = gesamt_fehler; _fp_w()

		if erfolg:
			log.info(
				"TAGS OK  [%s]  ID=%d  +%d Tag(s) gesetzt",
				slug, post_id, len(neue_ids),
			)
			gesamt_ok += 1
			verschiebe_nach_done([datei], done_dir)
		else:
			gesamt_fehler += 1

	_drucke_zusammenfassung(
		modus="tags", dry_run=dry_run,
		gruppen_anz=len(alle_pfade),
		ok=gesamt_ok, skip=0, warnungen=gesamt_fehler,
		patches=gesamt_ok if not dry_run else 0,
		pfade_anz=len(alle_pfade),
	)


# ---------------------------------------------------------------------------
# Modus kategorien – Kategorie + Cornerstone-Status setzen
# ---------------------------------------------------------------------------


def _lade_kategorien_name_id_map() -> dict[str, int]:
	"""
	Liest kategorien-export.json und gibt ein name→id-Dict zurück.
	Schlüssel sind lowercase-normalisiert für case-insensitiven Vergleich.
	Gibt leeres Dict zurück wenn die Datei fehlt oder nicht lesbar ist.
	"""
	if not KATEGORIEN_EXPORT.exists():
		return {}
	try:
		daten = json.loads(re.sub(
			r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ',
			KATEGORIEN_EXPORT.read_text(encoding="utf-8")))
		if not isinstance(daten, list):
			return {}
		return {k["name"].strip().lower(): k["id"]
		        for k in daten if k.get("name") and k.get("id")}
	except Exception as exc:
		log.warning("kategorien-export.json nicht lesbar: %s", exc)
		return {}


def hole_kategorie_id(name: str) -> int | None:
	"""
	Sucht eine WP-Kategorie nach Name (exaktes Match, case-insensitiv).
	Legt sie NICHT an – Kategorien müssen in WordPress vorhanden sein.
	Rückgabe: Kategorie-ID oder None wenn nicht gefunden.
	"""
	try:
		resp = SESSION.get(
			f"{REST_BASE}/categories",
			params={"search": name, "per_page": 20, "_fields": "id,name"},
			auth=AUTH,
			timeout=15,
		)
		resp.raise_for_status()
		treffer = resp.json()
	except requests.RequestException as exc:
		log.error("GET categories Fehler (name=%s): %s", name, exc)
		return None

	for k in treffer:
		if k["name"].strip().lower() == name.strip().lower():
			return k["id"]

	log.warning(
		"Kategorie %r nicht in WordPress gefunden. "
		"Bitte zuerst in WP anlegen oder SILO_KATEGORIE_MAP prüfen.",
		name,
	)
	return None


def verarbeite_modus_kategorien(dry_run: bool, nur_slug: str | None) -> None:
	"""Hauptablauf für --modus kategorien."""
	done_dir = KATEGORIEN_DIR / "done"
	dateien  = sorted(KATEGORIEN_DIR.glob("*.json"))

	if not dateien:
		log.info("Keine Kategorie-Proposals in %s.", KATEGORIEN_DIR)
		return

	# Name→ID-Lookup aus kategorien-export.json (bevorzugt gegenüber SILO_KATEGORIE_MAP)
	kat_name_id = _lade_kategorien_name_id_map()
	if kat_name_id:
		log.info("kategorien-export.json geladen: %d Kategorien", len(kat_name_id))
	else:
		log.info("kategorien-export.json nicht gefunden – nutze SILO_KATEGORIE_MAP als Fallback.")

	gesamt_ok = gesamt_fehler = gesamt_skip = 0
	alle_pfade: list[Path] = []

	for datei in dateien:
		try:
			proposal = json.loads(re.sub(
				r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ',
				datei.read_text(encoding="utf-8")))
		except (json.JSONDecodeError, OSError) as exc:
			log.warning("Datei übersprungen (%s): %s", datei.name, exc)
			continue

		slug = proposal.get("quell_slug", "").strip()
		if not slug or (nur_slug and slug != nur_slug):
			continue

		_fp["aktuell"] = slug; _fp_w()
		silo		= proposal.get("silo", "").strip()
		cornerstone	= proposal.get("cornerstone", False)

		# Kategorie-IDs ermitteln: bevorzugt aus `kategorien`-Array, sonst SILO_KATEGORIE_MAP
		kat_namen: list[str] = proposal.get("kategorien") or []
		if not isinstance(kat_namen, list):
			kat_namen = []

		if kat_namen and kat_name_id:
			# Neuer Pfad: kategorien-Array aus Proposal + export-Lookup
			wp_kat_ids: list[int] = []
			fehler_namen: list[str] = []
			for kat_name in kat_namen:
				kat_id_lok = kat_name_id.get(kat_name.strip().lower())
				if kat_id_lok is not None:
					wp_kat_ids.append(kat_id_lok)
				else:
					fehler_namen.append(kat_name)
			if fehler_namen:
				log.warning(
					"Slug %s: Kategorien nicht in kategorien-export.json: %s – Artikel übersprungen.",
					slug, ", ".join(repr(n) for n in fehler_namen),
				)
				gesamt_skip += 1
				continue
			wp_kat_label = ", ".join(kat_namen)
		elif kat_namen and not kat_name_id:
			# kategorien vorhanden, aber kein Export – via REST-API nachschlagen
			wp_kat_ids = []
			fehler_namen = []
			for kat_name in kat_namen:
				kat_id_lok = hole_kategorie_id(kat_name)
				if kat_id_lok is not None:
					wp_kat_ids.append(kat_id_lok)
				else:
					fehler_namen.append(kat_name)
			if fehler_namen:
				log.warning(
					"Slug %s: Kategorien nicht in WordPress gefunden: %s – Artikel übersprungen.",
					slug, ", ".join(repr(n) for n in fehler_namen),
				)
				gesamt_skip += 1
				continue
			wp_kat_label = ", ".join(kat_namen)
		else:
			# Fallback: Silo → SILO_KATEGORIE_MAP
			wp_kategorie = SILO_KATEGORIE_MAP.get(silo)
			if not wp_kategorie:
				log.warning(
					"Silo %r hat keinen Eintrag in SILO_KATEGORIE_MAP und kein kategorien-Feld – übersprungen.",
					silo,
				)
				gesamt_skip += 1
				continue
			kat_id_lok = hole_kategorie_id(wp_kategorie)
			if kat_id_lok is None:
				gesamt_skip += 1
				continue
			wp_kat_ids   = [kat_id_lok]
			wp_kat_label = wp_kategorie

		alle_pfade.append(datei)
		log.info(
			"--- Kategorie für Slug: %s  Silo: %s → WP: %s  Cornerstone: %s ---",
			slug, silo, wp_kat_label, cornerstone,
		)

		if dry_run:
			log.info(
				"DRY-RUN  [%s]  würde setzen → Kategorien: %s  Cornerstone: %s",
				slug, wp_kat_label, cornerstone,
			)
			gesamt_ok += 1
			continue

		post_id_hint = proposal.get("post_id")
		post = hole_post(slug, post_id_hint)
		if post is None:
			gesamt_fehler += 1
			continue

		post_id = post["id"]

		# Bestehende Kategorien beibehalten, neue hinzufügen
		bestehende_kats = list(post.get("categories", []))
		for kid in wp_kat_ids:
			if kid not in bestehende_kats:
				bestehende_kats.append(kid)

		patch_daten: dict = {"categories": bestehende_kats}

		# Yoast SEO Cornerstone-Meta (nur wenn Yoast installiert und REST-Meta registriert)
		# _yoast_wpseo_is_cornerstone: "1" = ja, "" = nein
		patch_daten["meta"] = {
			"_yoast_wpseo_is_cornerstone": "1" if cornerstone else "",
		}

		erfolg = sende_patch(post_id, patch_daten)

		if erfolg:
			log.info(
				"KATEGORIE OK  [%s]  ID=%d  Kat-IDs=%s  Cornerstone=%s",
				slug, post_id, wp_kat_ids, cornerstone,
			)
			gesamt_ok += 1
			verschiebe_nach_done([datei], done_dir)
		else:
			gesamt_fehler += 1

		_fp["erledigt"] = _fp.get("erledigt", 0) + 1
		_fp["ok"] = gesamt_ok; _fp["fehler"] = gesamt_fehler; _fp_w()

	_drucke_zusammenfassung(
		modus="kategorien", dry_run=dry_run,
		gruppen_anz=len(alle_pfade),
		ok=gesamt_ok, skip=gesamt_skip, warnungen=gesamt_fehler,
		patches=gesamt_ok if not dry_run else 0,
		pfade_anz=len(alle_pfade),
	)


# ---------------------------------------------------------------------------
# Zusammenfassung drucken
# ---------------------------------------------------------------------------


def _drucke_zusammenfassung(
	*,
	modus: str,
	dry_run: bool,
	gruppen_anz: int,
	ok: int,
	skip: int,
	warnungen: int,
	patches: int,
	pfade_anz: int,
) -> None:
	modus_label = "TROCKENLAUF" if dry_run else "LIVE – schreibt nach WordPress"
	print()
	print("=" * 62)
	print(f"  Modus:              {modus}  [{modus_label}]")
	print(f"  Artikel/Dateien:    {gruppen_anz}")
	print(f"  Erfolgreich:        {ok}")
	if skip:
		print(f"  Übersprungen:       {skip}  (bereits vorhanden / kein Mapping)")
	print(f"  Fehler/Warnungen:   {warnungen}")
	if not dry_run:
		print(f"  PATCH gesendet:     {patches}")
		print(f"  Dateien → done/:    {pfade_anz}")
	print(f"  Log:                {UPDATE_LOG}")
	print("=" * 62)
	print()
	log.info(
		"=== Fertig ===  Modus: %s  OK=%d  SKIP=%d  FEHLER=%d",
		modus, ok, skip, warnungen,
	)


# ---------------------------------------------------------------------------
# Öffentliche API – direkter Funktionsaufruf aus anderen Modulen
# ---------------------------------------------------------------------------


def fuehre_update_aus(
	modus: str,
	slug: str | None = None,
	live: bool = True,
) -> dict:
	"""
	Kapselt die Verarbeitungs-Logik ohne argparse für direkten Import.

	Parameter:
	    modus   "links" | "tags" | "kategorien"
	    slug    Wenn gesetzt, nur diesen Artikel verarbeiten
	    live    True = schreibt nach WordPress; False = Trockenlauf

	Rückgabe:
	    {
	        "ok":     int,         # erfolgreich verarbeitete Einträge
	        "fehler": int,         # fehlgeschlagene Einträge
	        "log":    list[str],   # Log-Zeilen dieser Ausführung
	    }
	"""
	global _fp
	nachrichten: list[str] = []

	class _ListHandler(logging.Handler):
		def emit(self, record: logging.LogRecord) -> None:
			nachrichten.append(self.format(record))

	handler = _ListHandler()
	handler.setFormatter(logging.Formatter("%(levelname)-7s  %(message)s"))
	log.addHandler(handler)

	# Gesamtanzahl vorab zählen für Fortschrittsbalken
	if modus == "links":
		gesamt_n = len(lade_links_proposals(slug))
	elif modus == "tags":
		gesamt_n = len(list(TAGS_DIR.glob("*.json")))
	else:
		gesamt_n = len(list(KATEGORIEN_DIR.glob("*.json")))
	_fp = {"gesamt": gesamt_n, "erledigt": 0, "aktuell": "", "ok": 0, "fehler": 0}
	_fp_w()

	n_ok = n_fehler = 0
	try:
		dry_run = not live
		log.info(
			"=== fuehre_update_aus ===  Modus: %s  Slug: %s  Live: %s",
			modus, slug or "alle", live,
		)
		if modus == "tags":
			verarbeite_modus_tags(dry_run, slug)
		elif modus == "kategorien":
			verarbeite_modus_kategorien(dry_run, slug)
		else:
			verarbeite_modus_links(dry_run, slug)

		# Zähle aus der Fertig-Zeile die _drucke_zusammenfassung immer loggt
		for msg in nachrichten:
			m_ok = re.search(r'\bOK=(\d+)', msg)
			m_f  = re.search(r'\bFEHLER=(\d+)', msg)
			if m_ok and m_f:
				n_ok     = int(m_ok.group(1))
				n_fehler = int(m_f.group(1))
				break

	except Exception as exc:
		nachrichten.append(f"ERROR    Unerwarteter Fehler: {exc}")
		n_fehler += 1
	finally:
		log.removeHandler(handler)
		try:
			PROGRESS_FILE.unlink(missing_ok=True)
		except OSError:
			pass

	return {"ok": n_ok, "fehler": n_fehler, "log": nachrichten}


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description=(
			"SEO Crawler – Modul 6/9: WordPress-Rückspielen\n\n"
			"Standard-Modus ist --dry-run.  WordPress wird nur mit --live beschrieben."
		),
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	parser.add_argument(
		"--modus",
		choices=["links", "tags", "kategorien"],
		default="links",
		metavar="MODUS",
		help="Verarbeitungs-Modus: links (Standard) | tags | kategorien",
	)

	lm = parser.add_mutually_exclusive_group()
	lm.add_argument(
		"--dry-run",
		action="store_true",
		default=True,
		help="Trockenlauf: berechnet Änderungen, schreibt nichts (Standard)",
	)
	lm.add_argument(
		"--live",
		action="store_true",
		default=False,
		help="Live-Modus: schreibt Änderungen wirklich nach WordPress",
	)
	parser.add_argument(
		"--slug",
		metavar="SLUG",
		default=None,
		help="Nur diesen Artikel-Slug verarbeiten",
	)
	args = parser.parse_args()

	dry_run		= not args.live
	modus_label	= "TROCKENLAUF" if dry_run else "LIVE – schreibt nach WordPress"

	log.info("=== update_wordpress gestartet ===  Modus: %s  [%s]", args.modus, modus_label)
	if dry_run:
		log.info("Hinweis: Keine Änderungen an WordPress. --live für echten Schreibzugriff.")

	if args.modus == "tags":
		verarbeite_modus_tags(dry_run, args.slug)
	elif args.modus == "kategorien":
		verarbeite_modus_kategorien(dry_run, args.slug)
	else:
		verarbeite_modus_links(dry_run, args.slug)


if __name__ == "__main__":
	main()
