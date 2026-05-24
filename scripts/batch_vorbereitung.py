"""
batch_vorbereitung.py – Modul 3: Batch-Vorbereiter

Liest alle geparsten Artikel aus data/parsed/ und erstellt
Markdown-Dateien in analysis/batches/, die direkt in die
Claude Desktop-App kopiert werden können.

Jede Batch-Datei enthält:
  - Aufgabenbeschreibung mit erwartetem JSON-Ausgabeformat
  - Inhalt von seo-kontext.md als Kontext-Einleitung
  - Pro Artikel: Slug, Titel, Permalink, Kategorien, Tags, Fließtext (≤ 800 Wörter)
  - Vollständige Artikel-Referenzliste am Ende (alle 101+ Artikel)

Das erwartete Ausgabeformat der KI ist kompatibel mit
freigabe_server.py (Modul 5) und update_wordpress.py (Modul 6).

Aufruf:
    python scripts/batch_vorbereitung.py
    python scripts/batch_vorbereitung.py --batch-size 10
    python scripts/batch_vorbereitung.py --batch-size 10 --force
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT		= Path(__file__).resolve().parent.parent
PARSED_DIR			= PROJECT_ROOT / "data" / "parsed"
BATCHES_DIR			= PROJECT_ROOT / "analysis" / "batches"
LOG_DIR				= PROJECT_ROOT / "logs"
SEO_KONTEXT			= PROJECT_ROOT / "seo-kontext.md"
POOL_FINAL_DATEI	= PROJECT_ROOT / "data" / "taxonomie" / "pool-final.json"
KATEGORIEN_DATEI	= PROJECT_ROOT / "data" / "taxonomie" / "kategorien-export.json"

BATCHES_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / "env.local")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "batch_vorbereitung.log"

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
# Konstanten
# ---------------------------------------------------------------------------

TRENNLINIE		= "---"
MAX_WOERTER		= 850
DEFAULT_BATCH	= 4

# Silo-Schlüsselwörter für die Pool-Filterung (bereits slug-normalisiert,
# d.h. Kleinschreibung, Umlaute als ae/oe/ue/ss, keine Sonderzeichen).
SILO_SCHLUESSELWOERTER: dict[str, list[str]] = {
	"theater":		["theater", "buehne", "brecht", "ensemble", "schauspiel",
					 "regie", "dramaturgie", "buehnenbild", "intendanz"],
	"medien":		["journalismus", "medien", "politik", "redaktion",
					 "kommunikation", "recherche", "interview", "podcast"],
	"informatik":	["informatik", "web", "digital", "server", "linux",
					 "python", "wordpress", "software", "code"],
	"berlin":		["berlin", "hauptstadt", "ost-berlin", "prenzlauer",
					 "karl-marx", "urban", "mitte", "kreuzberg"],
}
POOL_MAX_BEGRIFFE = 60

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def lade_artikel() -> list[dict]:
	"""
	Liest alle JSON-Dateien aus data/parsed/ und sortiert sie
	nach Veröffentlichungsdatum (neueste zuerst; leere Daten ans Ende).
	"""
	dateien = sorted(PARSED_DIR.glob("*.json"))
	if not dateien:
		log.error("Keine JSON-Dateien in %s – zuerst crawler.py ausführen.", PARSED_DIR)
		sys.exit(1)

	artikel: list[dict] = []
	for datei in dateien:
		try:
			daten = json.loads(datei.read_text(encoding="utf-8"))
			artikel.append(daten)
		except (json.JSONDecodeError, OSError) as exc:
			log.warning("Datei übersprungen (%s): %s", datei.name, exc)

	# Neueste zuerst; Artikel ohne Datum ans Ende
	artikel.sort(
		key=lambda a: a.get("published") or "0000",
		reverse=True,
	)
	log.info("Artikel geladen: %d", len(artikel))
	return artikel


def lade_seo_kontext() -> str:
	"""Liest seo-kontext.md; gibt leeren String zurück wenn nicht vorhanden."""
	if not SEO_KONTEXT.exists():
		log.warning("seo-kontext.md nicht gefunden – Einleitung wird übersprungen.")
		return ""
	return SEO_KONTEXT.read_text(encoding="utf-8").strip()


def _normalisiere_anführungszeichen(text: str) -> str:
	"""
	Gerade " im Fließtext → typografische „ (U+201E) und " (U+201C).
	Bereits vorhandene typografische Zeichen („/") werden mitgezählt,
	damit gemischter Text (z. B. „Wort" mit geradem Schließer) korrekt
	behandelt wird.
	"""
	result = []
	offen = False
	for c in text:
		if c == '\u201e':       # „ typografisch öffnend → Zustand merken, Zeichen behalten
			offen = True
			result.append(c)
		elif c == '\u201c':     # " typografisch schließend → Zustand merken, Zeichen behalten
			offen = False
			result.append(c)
		elif c == '"':          # gerades Anführungszeichen → ersetzen
			result.append('\u201e' if not offen else '\u201c')
			offen = not offen
		else:
			result.append(c)
	return ''.join(result)


def bereinige_markdown(text: str) -> str:
	"""
	Vorverarbeitungsstufe: bereinigt Markdown-Artefakte, bevor der
	Text an Claude übergeben wird.

	Entfernt:
	  - Nackte URLs ohne Ankertext (http/https nicht in [text](url))
	  - Markdown-Fettschrift- und Kursiv-Marker (**, __, *, _)
	  - Heading-Marker (#…) – Überschriftentext bleibt erhalten
	  - Doppelte Leerzeichen
	  - Mehr als eine aufeinanderfolgende Leerzeile
	Normalisiert:
	  - Gerade Anführungszeichen → typografische deutsche Anführungszeichen
	"""
	# 1. Nackte URLs: http(s)://... die NICHT Teil von [text](url) sind.
	#    Negativ-Lookbehind auf '(' schließt Markdown-Link-Ziele aus.
	text = re.sub(r'(?<!\()https?://\S+', '', text)

	# 2. Fettschrift-Marker **text** → text  /  __text__ → text
	text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
	text = re.sub(r'__(.+?)__',     r'\1', text, flags=re.DOTALL)

	# 3. Kursiv-Marker *text* → text  /  _text_ → text
	#    Kurzes Lookahead/behind verhindert, dass Leerzeichen-umgebene
	#    Sterne (z. B. Aufzählungspunkte) versehentlich greifen.
	text = re.sub(r'(?<!\*)\*(?!\*|\s)(.+?)(?<!\s)\*(?!\*)', r'\1', text)
	text = re.sub(r'(?<!_)_(?!_|\s)(.+?)(?<!\s)_(?!_)',      r'\1', text)

	# 4. Heading-Marker entfernen, Überschriftentext behalten
	#    "## Berliner Ensemble" → "Berliner Ensemble"
	text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

	# 5. Doppelte (und mehr) Leerzeichen → eines
	text = re.sub(r'[ \t]{2,}', ' ', text)

	# 6. Mehr als eine Leerzeile → genau eine
	text = re.sub(r'\n{3,}', '\n\n', text)

	# 7. Gerade Anführungszeichen → typografische deutsche Anführungszeichen
	text = _normalisiere_anführungszeichen(text)

	return text.strip()


def kuerze_auf_satz(text: str, max_woerter: int = MAX_WOERTER) -> tuple[str, int]:
	"""
	Kürzt den Text auf den letzten vollständigen Satz vor der
	max_woerter-Grenze.

	Strategie:
	  - Kandidat = erste max_woerter Wörter als String
	  - Letztes Satzende (.!?) vor dem Ende suchen; dabei wird ein
	    Satzende erkannt, wenn Punkt/Ausrufe-/Fragezeichen (optional
	    gefolgt von schließendem Anführungszeichen) vor Whitespace+
	    Großbuchstabe oder Zeilenumbruch oder Stringende steht.
	  - Kein Satzende gefunden → letzter Absatzumbruch als Grenze
	  - Kein Absatz → harter Schnitt (Fallback, wird geloggt)

	Rückgabe: (gekürzter Text, Anzahl entfernter Wörter)
	"""
	woerter = text.split()
	gesamt  = len(woerter)

	if gesamt <= max_woerter:
		return text, 0

	kandidat = " ".join(woerter[:max_woerter])

	# Satzende-Muster (funktioniert für Deutsch inkl. Anführungszeichen)
	satzende = re.compile(
		r'[.!?][»"""\u201d\u2019\']?'   # Punkt/! /? + opt. schließendes Anführungszeichen
		r'(?=\s+[A-ZÄÖÜA-Z]'            # gefolgt von Leerzeichen + Großbuchstabe
		r'|\s*\n'                        # oder Zeilenumbruch
		r'|\s*$)'                        # oder Stringende
	)

	treffer = list(satzende.finditer(kandidat))
	if treffer:
		schnitt  = treffer[-1].end()
		gekuerzt = kandidat[:schnitt].rstrip()
	else:
		# Fallback 1: letzter Absatzumbruch
		absaetze = list(re.finditer(r'\n\n', kandidat))
		if absaetze:
			gekuerzt = kandidat[:absaetze[-1].start()].rstrip()
		else:
			# Fallback 2: harter Schnitt
			log.debug("Kein Satzende in %d Wörtern gefunden – harter Schnitt.", max_woerter)
			gekuerzt = kandidat

	entfernt = gesamt - len(gekuerzt.split())
	return gekuerzt, entfernt


def formatiere_liste(werte: list[str]) -> str:
	"""Komma-separierte Liste oder '–' wenn leer."""
	return ", ".join(werte) if werte else "–"


def lade_kategorien_wp() -> list[dict]:
	"""
	Liest kategorien-export.json; gibt leere Liste zurück wenn nicht vorhanden.
	Erwartetes Format: flaches Array mit {id, name, parent, ...}.
	"""
	if not KATEGORIEN_DATEI.exists():
		log.debug("kategorien-export.json nicht gefunden – Kategorien-Block wird übersprungen.")
		return []
	try:
		daten = json.loads(KATEGORIEN_DATEI.read_text(encoding="utf-8"))
		return daten if isinstance(daten, list) else []
	except Exception as exc:
		log.warning("kategorien-export.json nicht lesbar: %s", exc)
		return []


def lade_pool_begriffe() -> list[dict]:
	"""Liest pool-final.json; gibt leere Liste zurück wenn nicht vorhanden."""
	if not POOL_FINAL_DATEI.exists():
		log.debug("pool-final.json nicht gefunden – Pool-Abschnitt wird übersprungen.")
		return []
	try:
		pool = json.loads(POOL_FINAL_DATEI.read_text(encoding="utf-8"))
		return pool if isinstance(pool, list) else []
	except Exception as exc:
		log.warning("pool-final.json nicht lesbar: %s", exc)
		return []


def _normalisiere_slug(text: str) -> str:
	"""
	Wandelt einen beliebigen String in das WordPress-Slug-Format um:
	Kleinschreibung, Umlaute → ae/oe/ue/ss, Leerzeichen → Bindestrich,
	alle übrigen Sonderzeichen entfernt, mehrfache Bindestriche zusammengefasst.
	Wird auf Pool-Slugs UND auf Silo-Schlüsselwörter angewendet.
	"""
	t = text.lower()
	for src, dst in (('ä','ae'),('ö','oe'),('ü','ue'),('ß','ss')):
		t = t.replace(src, dst)
	t = t.replace(' ', '-')
	t = re.sub(r'[^a-z0-9\-]', '', t)
	t = re.sub(r'-+', '-', t)
	return t.strip('-')


def _filtere_pool_begriffe(
	artikel_im_batch: list[dict],
	pool: list[dict],
	anzahl: int = POOL_MAX_BEGRIFFE,
) -> list[str]:
	"""
	Gibt bis zu `anzahl` Pool-Begriffe zurück, priorisiert nach Silo-Relevanz.

	Schritt 1 – Silo-Erkennung:
	  Normalisiert die Artikel-Kategorien und prüft, welche Silos vertreten sind.

	Schritt 2 – Slug-basierte Filterung:
	  Prüft den (ggf. generierten) Slug jedes Pool-Eintrags auf Teilstring-
	  Übereinstimmung mit den aktiven Silo-Schlüsselwörtern.
	  Vergleich ausschließlich auf Slug-Ebene – nie auf Name-Ebene.

	Schritt 3 – Auffüllen:
	  Füllt auf `anzahl` mit den restlichen Begriffen (alphabetisch) auf.
	"""
	if not pool:
		return []

	# Schritt 1: aktive Silos aus Artikel-Kategorien ermitteln
	aktive_silos: set[str] = set()
	for a in artikel_im_batch:
		for cat in a.get("categories", []):
			cat_slug = _normalisiere_slug(cat)
			for silo, keywords in SILO_SCHLUESSELWOERTER.items():
				if any(kw in cat_slug for kw in keywords):
					aktive_silos.add(silo)

	# Vereinigte Keyword-Liste der aktiven Silos
	aktive_kw: list[str] = []
	for silo in aktive_silos:
		aktive_kw.extend(SILO_SCHLUESSELWOERTER[silo])

	# Schritt 2: Pool-Einträge klassifizieren (Duplikate per name.lower() dedupliziert)
	silo_treffer: list[str] = []
	rest:         list[str] = []
	gesehen:      set[str]  = set()

	for eintrag in pool:
		name = eintrag.get("name", "").strip()
		if not name:
			continue
		key = name.lower()
		if key in gesehen:
			continue
		gesehen.add(key)

		# Slug aus Pool-Eintrag verwenden; fehlt er, aus dem Namen generieren
		slug = (eintrag.get("slug") or "").strip() or _normalisiere_slug(name)

		if aktive_kw and any(kw in slug for kw in aktive_kw):
			silo_treffer.append(name)
		else:
			rest.append(name)

	# Schritt 3: alphabetisch sortieren, auffüllen
	silo_treffer.sort(key=str.lower)
	rest_sortiert = sorted(rest, key=str.lower)
	fehlend = anzahl - len(silo_treffer)
	ergebnis = silo_treffer + (rest_sortiert[:fehlend] if fehlend > 0 else [])
	return ergebnis[:anzahl]


# ---------------------------------------------------------------------------
# Markdown-Bausteine
# ---------------------------------------------------------------------------


def _statistik_block(artikel_im_batch: list[dict]) -> str:
	"""
	Kompakte Batch-Statistik für den Überblick am Anfang der Datei.

	Berechnet:
	  - Anzahl Artikel im Batch
	  - Durchschnittliche Fließtextlänge in Wörtern (nach Bereinigung,
	    vor Kürzung – zeigt die echte Inhaltsdichte)
	  - Anzahl Artikel ohne interne Links (Verlinkungslücken)
	"""
	anzahl = len(artikel_im_batch)

	# Textlängen nach Bereinigung, vor Kürzung
	laengen = [
		len(bereinige_markdown(a.get("markdown", "")).split())
		for a in artikel_im_batch
	]
	avg_laenge = round(sum(laengen) / anzahl) if anzahl else 0

	# Verlinkungslücken: Artikel ohne interne Links
	ohne_links = sum(
		1 for a in artikel_im_batch
		if not a.get("internal_links")
	)
	luecken_hinweis = f" ⚠ {ohne_links} ohne interne Links" if ohne_links else ""

	return (
		f"> **Batch-Statistik:** {anzahl} Artikel"
		f" · ⌀ {avg_laenge} Wörter/Artikel"
		f"{luecken_hinweis}\n"
	)


def _aufgaben_block(batch_nr: int, batch_gesamt: int, artikel_anzahl: int) -> str:
	"""
	Aufgabenbeschreibung mit erwartetem JSON-Ausgabeformat.
	Schema ist 1:1 kompatibel mit proposal_import.py (Modul 3b).
	"""
	return f"""\
# Aufgabe

Analysiere die folgenden {artikel_anzahl} Artikel (Batch {batch_nr} von {batch_gesamt}) \
auf Möglichkeiten für **interne Verlinkungen** gemäß dem SEO-Kontext unten.

Erstelle außerdem Vorschläge für neue Schlagwörter, weise WordPress-Kategorien zu \
und ordne jeden Artikel seinem SEO-Silo zu. Markiere Cornerstone-Artikel.

Gib das Ergebnis als **ein einziges JSON-Array** zurück – ein Objekt pro Artikel.
Überspringe Artikel ohne sinnvolle Verlinkungsmöglichkeit.
Halte die Verlinkungsregeln aus dem SEO-Kontext ein.

```json
[
  {{
    "slug":               "artikel-slug",
    "silo":               "Theater und Bühne Berlin",
    "kategorien":         ["Theater", "Berliner Ensemble"],
    "neue_schlagwoerter": ["Schlagwort1", "Schlagwort2"],
    "link_vorschlaege": [
      {{
        "kontext_satz": "Vollständiger Satz, in dem der Link gesetzt wird.",
        "ankertext":    "Der zu verlinkende Text im Satz",
        "ziel_url":     "https://...",
        "begruendung":  "Ein Satz: warum dieser Link semantisch passt."
      }}
    ],
    "cornerstone": false
  }}
]
```

Gültige Silo-Werte: Theater und Bühne Berlin | Medien und Journalismus | \
Informatik und Technologie | Berlin als Stadtpersönlichkeit

**Kategorien:** Wähle aus der Liste „Verfügbare WordPress-Kategorien" am Ende dieser Datei. \
Verwende exakt die dort angegebenen Namen. Bis zu 3 Kategorien pro Artikel.
**Schlagwörter:** Bevorzuge Begriffe aus dem Pool am Ende dieser Datei. \
Nur neue vorschlagen, wenn dort nichts Passendes vorhanden ist.
**Begründungen:** ein prägnanter Satz genügt.
Einen einzigen JSON-Codeblock ausgeben. Keine Erläuterungen außerhalb des Codeblocks.
"""


def _seo_kontext_block(seo_kontext: str) -> str:
	if not seo_kontext:
		return ""
	return f"""\
{TRENNLINIE}

# SEO-Kontext

{seo_kontext}
"""


def _artikel_block(artikel: dict, lfd_nr: int) -> str:
	"""
	Formatiert einen einzelnen Artikel für das Batch-Dokument.
	Wendet Bereinigung und Satz-genaue Kürzung auf den Fließtext an.
	"""
	titel		= artikel.get("title", "(ohne Titel)")
	slug		= artikel.get("slug", "")
	url			= artikel.get("url", "")
	published	= artikel.get("published", "")
	kategorien	= formatiere_liste(artikel.get("categories", []))
	tags		= formatiere_liste(artikel.get("tags", []))
	markdown	= artikel.get("markdown", "").strip()

	# Datum auf YYYY-MM-DD kürzen wenn ISO-Format vorliegt
	datum_kurz = published[:10] if len(published) >= 10 else published

	# 1. Markdown bereinigen
	markdown_sauber = bereinige_markdown(markdown)

	# 2. Auf letzten vollständigen Satz vor MAX_WOERTER kürzen
	text_gekuerzt, entfernt = kuerze_auf_satz(markdown_sauber)
	kuerzel_hinweis = (
		f"\n\n*(… {entfernt} weitere Wörter gekürzt)*"
		if entfernt > 0
		else ""
	)

	return f"""\
{TRENNLINIE}

## {lfd_nr}. {titel}

| Feld         | Wert |
|:-------------|:-----|
| Slug         | `{slug}` |
| Permalink    | {url} |
| Veröffentlicht | {datum_kurz} |
| Kategorien   | {kategorien} |
| Schlagwörter | {tags} |

{text_gekuerzt}{kuerzel_hinweis}
"""


def _referenz_block(alle_artikel: list[dict]) -> str:
	"""
	Vollständige Artikel-Referenzliste am Ende jeder Batch-Datei.
	Dient als Nachschlagewerk für Verlinkungsziele.
	"""
	zeilen = [
		f"{TRENNLINIE}\n",
		"# Referenz: Alle Artikel (Verlinkungsziele)\n",
		f"*{len(alle_artikel)} Artikel gesamt – für Verlinkungsvorschläge*\n",
	]
	for a in alle_artikel:
		slug	= a.get("slug", "")
		titel	= a.get("title", "(ohne Titel)")
		url		= a.get("url", "")
		zeilen.append(f"- `{slug}` – {titel}  \n  {url}")

	return "\n".join(zeilen) + "\n"


# ---------------------------------------------------------------------------
# Interne-Links-Block (nach Referenzliste)
# ---------------------------------------------------------------------------


def _interne_links_block(artikel_im_batch: list[dict]) -> str:
	"""
	Listet die bereits vorhandenen internen Links pro Batch-Artikel auf.
	Quelle: Feld 'interne_links' (Fallback: 'internal_links') in den
	geparsten JSON-Dateien aus data/parsed/.

	Format eines Link-Eintrags (flexibel):
	  {ankertext/text/anchor, url/href}  oder  einfacher String
	"""
	zeilen = [
		f"{TRENNLINIE}\n",
		"## Bereits vorhandene interne Links\n",
		"*Diese Links existieren bereits – bitte keine Duplikate vorschlagen.*\n",
	]

	for artikel in artikel_im_batch:
		slug  = artikel.get("slug", "")
		links = artikel.get("interne_links") or artikel.get("internal_links") or []

		zeilen.append(f"\n### {slug}\n")

		if not links:
			zeilen.append("Keine internen Links vorhanden.\n")
			continue

		for link in links:
			if isinstance(link, dict):
				ankertext = (
					link.get("ankertext")
					or link.get("text")
					or link.get("anchor")
					or "–"
				)
				url = link.get("url") or link.get("href") or "–"
				zeilen.append(f"- {ankertext} → {url}")
			elif isinstance(link, str):
				zeilen.append(f"- {link}")

	return "\n".join(zeilen) + "\n"


# ---------------------------------------------------------------------------
# Schlagwort-Pool-Block (nach Interne-Links-Block)
# ---------------------------------------------------------------------------


def _schlagwort_pool_block(artikel_im_batch: list[dict], pool: list[dict]) -> str:
	"""
	Fügt den Schlagwort-Pool-Abschnitt ein – silo-gefiltert, slug-basiert.
	Gibt leeren String zurück wenn der Pool leer oder nicht vorhanden ist.
	"""
	begriffe = _filtere_pool_begriffe(artikel_im_batch, pool)
	if not begriffe:
		return ""

	liste = ", ".join(begriffe)
	return (
		f"{TRENNLINIE}\n\n"
		"## Bestehender Schlagwort-Pool\n\n"
		"Bestehende Schlagwörter – **bevorzugt verwenden**. "
		"Neue Schlagwörter nur vorschlagen, wenn hier nichts Passendes vorhanden ist:\n\n"
		f"{liste}\n"
	)



# ---------------------------------------------------------------------------
# WordPress-Kategorien-Block
# ---------------------------------------------------------------------------


def _kategorien_block(kategorien: list[dict]) -> str:
	"""
	Gibt den Kategorien-Block mit hierarchischer Darstellung zurück.
	Voraussetzung: flaches Array mit {id, name, parent} aus kategorien-export.json.
	Gibt leeren String zurück wenn die Liste leer ist.
	"""
	if not kategorien:
		return ""

	# Hauptkategorien (parent == 0 oder parent fehlt)
	hauptkat:   list[dict] = [k for k in kategorien if not k.get("parent")]
	unterkat_by_parent: dict[int, list[dict]] = {}
	for k in kategorien:
		p = k.get("parent") or 0
		if p:
			unterkat_by_parent.setdefault(p, []).append(k)

	zeilen: list[str] = []
	for hk in sorted(hauptkat, key=lambda k: k.get("name", "").lower()):
		hk_id   = hk.get("id", "?")
		hk_name = hk.get("name", "–")
		zeilen.append(f"- {hk_name} (ID: {hk_id})")
		for uk in sorted(unterkat_by_parent.get(hk_id, []),
		                 key=lambda k: k.get("name", "").lower()):
			uk_id   = uk.get("id", "?")
			uk_name = uk.get("name", "–")
			zeilen.append(f"  └ {uk_name} (ID: {uk_id})")

	return (
		f"{TRENNLINIE}\n\n"
		"## Verfügbare WordPress-Kategorien\n\n"
		"Verwende für das Feld `kategorien` exakt diese Namen:\n\n"
		+ "\n".join(zeilen)
		+ "\n"
	)


# ---------------------------------------------------------------------------
# Batch-Datei schreiben
# ---------------------------------------------------------------------------


def schreibe_batch(
	batch_nr: int,
	batch_gesamt: int,
	artikel_im_batch: list[dict],
	alle_artikel: list[dict],
	seo_kontext: str,
	force: bool,
	global_start: int = 1,
	pool: list[dict] | None = None,
	kategorien: list[dict] | None = None,
) -> Path:
	"""
	Erstellt eine Batch-Markdown-Datei.
	Überspringt bereits vorhandene Dateien, außer bei --force.
	global_start: globale laufende Nummer des ersten Artikels im Batch.
	"""
	dateiname	= f"batch-{batch_nr:02d}.md"
	pfad		= BATCHES_DIR / dateiname

	if pfad.exists() and not force:
		log.info("SKIP  %s  (bereits vorhanden, --force zum Überschreiben)", dateiname)
		return pfad

	ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

	# Kopfzeile – global_start statt der fehlerhaften lokalen Berechnung
	artikel_start	= global_start
	artikel_ende	= global_start + len(artikel_im_batch) - 1
	kopf = (
		f"[//]: # (SEO Crawler – {dateiname})\n"
		f"[//]: # (Erstellt: {ts})\n"
		f"[//]: # (Artikel {artikel_start}–{artikel_ende} von {len(alle_artikel)})\n\n"
	)

	# Statistik-Block (steht ganz oben, nach dem Metadaten-Kommentar)
	statistik = _statistik_block(artikel_im_batch)

	# Aufgabenblock
	aufgabe = _aufgaben_block(batch_nr, batch_gesamt, len(artikel_im_batch))

	# SEO-Kontext
	kontext = _seo_kontext_block(seo_kontext)

	# Artikel-Blöcke (globale laufende Nummer)
	artikel_sektionen = ""
	for i, artikel in enumerate(artikel_im_batch, start=artikel_start):
		artikel_sektionen += _artikel_block(artikel, i)

	# WordPress-Kategorien (vor dem Pool-Block)
	kategorien_block = _kategorien_block(kategorien or [])

	# Schlagwort-Pool (vor der Referenzliste)
	pool_block = _schlagwort_pool_block(artikel_im_batch, pool or [])

	# Referenzliste
	referenz = _referenz_block(alle_artikel)

	# Vorhandene interne Links (Duplikat-Schutz für den Linguisten)
	interne_links = _interne_links_block(artikel_im_batch)

	inhalt = (
		kopf + statistik + "\n"
		+ aufgabe + kontext + artikel_sektionen
		+ kategorien_block + ("\n" if kategorien_block else "")
		+ pool_block + ("\n" if pool_block else "")
		+ referenz + "\n"
		+ interne_links
	)

	pfad.write_text(inhalt, encoding="utf-8")
	log.info("OK    %s  (%d Artikel)", dateiname, len(artikel_im_batch))
	return pfad


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Modul 3: Batch-Vorbereiter für Claude Desktop"
	)
	parser.add_argument(
		"--batch-size",
		type=int,
		default=DEFAULT_BATCH,
		metavar="N",
		help=f"Artikel pro Batch (Standard: {DEFAULT_BATCH})",
	)
	parser.add_argument(
		"--force",
		action="store_true",
		help="Bereits vorhandene Batch-Dateien überschreiben",
	)
	args = parser.parse_args()

	if args.batch_size < 1:
		log.error("--batch-size muss mindestens 1 sein.")
		sys.exit(1)

	log.info("=== Batch-Vorbereitung gestartet ===")

	alle_artikel	= lade_artikel()
	seo_kontext		= lade_seo_kontext()
	pool_begriffe	= lade_pool_begriffe()
	kategorien_wp	= lade_kategorien_wp()
	if pool_begriffe:
		log.info("Schlagwort-Pool geladen: %d Begriffe", len(pool_begriffe))
	else:
		log.info("Schlagwort-Pool nicht verfügbar – pool-final.json fehlt oder leer.")
	if kategorien_wp:
		log.info("WordPress-Kategorien geladen: %d", len(kategorien_wp))
	else:
		log.info("WordPress-Kategorien nicht verfügbar – kategorien-export.json fehlt.")

	# Artikel in Batches aufteilen
	batches: list[list[dict]] = [
		alle_artikel[i : i + args.batch_size]
		for i in range(0, len(alle_artikel), args.batch_size)
	]
	batch_gesamt = len(batches)

	log.info(
		"Batches: %d  |  Batch-Größe: %d  |  Artikel gesamt: %d",
		batch_gesamt, args.batch_size, len(alle_artikel),
	)

	# Bei --force: veraltete Batch-Dateien löschen, die nicht mehr
	# zur neuen Batch-Anzahl gehören (verhindert Lücken und Zombie-Dateien).
	if args.force:
		neue_namen = {f"batch-{nr:02d}.md" for nr in range(1, batch_gesamt + 1)}
		for alte_datei in BATCHES_DIR.glob("batch-*.md"):
			if alte_datei.name not in neue_namen:
				alte_datei.unlink()
				log.info("GELÖSCHT  %s  (nicht mehr benötigt)", alte_datei.name)

	erstellte: list[Path] = []
	global_start = 1
	for nr, batch in enumerate(batches, start=1):
		pfad = schreibe_batch(
			batch_nr		= nr,
			batch_gesamt	= batch_gesamt,
			artikel_im_batch= batch,
			alle_artikel	= alle_artikel,
			seo_kontext		= seo_kontext,
			force			= args.force,
			global_start	= global_start,
			pool			= pool_begriffe,
			kategorien		= kategorien_wp,
		)
		erstellte.append(pfad)
		global_start += len(batch)

	# Zusammenfassung
	log.info("=== Fertig ===")
	print()
	print(f"  Batches erstellt:  {batch_gesamt}")
	print(f"  Artikel gesamt:    {len(alle_artikel)}")
	print(f"  Artikel pro Batch: {args.batch_size}")
	print(f"  Ausgabe:           {BATCHES_DIR}")
	print()


if __name__ == "__main__":
	main()
