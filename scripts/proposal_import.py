"""
proposal_import.py – Modul 3b: Proposal-Validator und Watcher

Zwei Modi:
  1. Watcher-Modus (Standard): Überwacht analysis/proposals/ auf neue
     oder geänderte JSON-Dateien via watchdog und validiert sie sofort.
  2. Validierungs-Modus (--validate): Prüft alle vorhandenen
     Proposal-Dateien und gibt einen Statusbericht aus.

Erwartetes Dateiformat:  analysis/proposals/batch-XX-proposals.json
Dateiinhalt: JSON-Array mit einem Objekt pro Artikel

    [
      {
        "slug":               "artikel-slug",
        "silo":               "Theater und Bühne Berlin",
        "neue_schlagwoerter": ["Tag1", "Tag2"],
        "link_vorschlaege": [
          {
            "kontext_satz": "...",
            "ankertext":    "...",
            "ziel_url":     "https://...",
            "begruendung":  "..."
          }
        ],
        "cornerstone": false
      }
    ]

Aufruf:
    python scripts/proposal_import.py               # Watcher-Modus
    python scripts/proposal_import.py --validate    # alle Dateien prüfen
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT	= Path(__file__).resolve().parent.parent
PROPOSALS_DIR	= PROJECT_ROOT / "analysis" / "proposals"
BATCHES_DIR		= PROJECT_ROOT / "analysis" / "batches"
LOG_DIR			= PROJECT_ROOT / "logs"

PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / "env.local")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "proposal_import.log"

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
# Schema-Konstanten
# ---------------------------------------------------------------------------

# Bekannte SEO-Silos aus seo-kontext.md
GUELTIGE_SILOS: frozenset[str] = frozenset({
	"Theater und Bühne Berlin",
	"Medien und Journalismus",
	"Informatik und Technologie",
	"Berlin als Stadtpersönlichkeit",
})

# Pflichtfelder pro Artikel-Eintrag mit erwartetem Typ
ARTIKEL_PFLICHT: dict[str, type] = {
	"slug":               str,
	"silo":               str,
	"neue_schlagwoerter": list,
	"link_vorschlaege":   list,
	"cornerstone":        bool,
}

# Pflichtfelder pro Link-Vorschlag
LINK_PFLICHT: dict[str, type] = {
	"kontext_satz":	str,
	"ankertext":	str,
	"ziel_url":		str,
	"begruendung":	str,
}

# Dateinamenmuster: batch-01-proposals.json, batch-21-proposals.json …
PROPOSAL_MUSTER = re.compile(r"^batch-(\d{2})-proposals\.json$")

# Steuerzeichen U+0000–U+001F außer \t \n \r
_STEUERZEICHEN = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def _bereinige_steuerzeichen(text: str) -> str:
	"""Ersetzt JSON-invalide Steuerzeichen durch Leerzeichen."""
	return _STEUERZEICHEN.sub(' ', text)


def _extrahiere_proposals_array(daten: object) -> tuple[list | None, dict | None]:
	"""
	Unterstützt reines Array und Wrapper-Objekt:
	  {"linguist_meta": {...}, "proposals": [...]}
	Rückgabe: (proposals_liste, linguist_meta_oder_None)
	"""
	if isinstance(daten, list):
		return daten, None
	if isinstance(daten, dict) and isinstance(daten.get("proposals"), list):
		return daten["proposals"], daten.get("linguist_meta")
	return None, None


# ---------------------------------------------------------------------------
# JSON-Reparatur
# ---------------------------------------------------------------------------


def _normalisiere_json_anführungszeichen(text: str) -> str:
	"""
	State-Machine: escapet ungescapte gerade " innerhalb von JSON-Stringwerten.

	Heuristik für strukturelles String-Ende: ein " gilt als schließendes
	JSON-Anführungszeichen, wenn das nächste Nicht-Whitespace-Zeichen
	eines von  : , ] }  ist oder EOF erreicht wird.
	Alle anderen " innerhalb eines Strings werden zu \".
	"""
	result = []
	in_string = False
	i = 0
	n = len(text)
	while i < n:
		c = text[i]
		if not in_string:
			result.append(c)
			if c == '"':
				in_string = True
		else:
			if c == '\\':
				result.append(c)
				i += 1
				if i < n:
					result.append(text[i])
			elif c == '"':
				# Lookahead: nächstes Nicht-Whitespace-Zeichen bestimmen
				j = i + 1
				while j < n and text[j] in ' \t\r\n':
					j += 1
				if j >= n or text[j] in ':,]}':
					result.append(c)
					in_string = False
				else:
					result.append('\\')
					result.append(c)
			else:
				result.append(c)
		i += 1
	return ''.join(result)


def _repariere_json_anführungszeichen(text: str, pfad: Path) -> tuple[str, int]:
	"""
	Versucht, ungescapte gerade Anführungszeichen innerhalb von JSON-Stringwerten
	zu beheben, indem sie iterativ durch \" ersetzt werden.

	Algorithmus: parse → bei JSONDecodeError von der Fehlerposition rückwärts das
	erste nicht-gescapte " suchen → escapen → wiederholen bis parse gelingt oder
	kein kandidat mehr gefunden wird.

	Rückgabe: (reparierter_text, anzahl_reparaturen)
	Wirft json.JSONDecodeError, wenn die Reparatur scheitert.
	"""
	repairs = 0
	while True:
		try:
			json.loads(text)
			return text, repairs
		except json.JSONDecodeError as exc:
			found = False
			for p in range(exc.pos - 1, max(0, exc.pos - 300), -1):
				if text[p] != '"':
					continue
				# Anzahl unmittelbar vorangehender Backslashes zählen
				k, bs = p - 1, 0
				while k >= 0 and text[k] == '\\':
					bs += 1
					k -= 1
				if bs % 2 == 0:  # nicht bereits escapet
					ctx = text[max(0, p - 25):p + 25].replace('\n', ' ')
					log.info(
						"Auto-Repair %s: ungescaptes \" bei pos %d escapet  …%s…",
						pfad.name, p, ctx,
					)
					text = text[:p] + '\\"' + text[p + 1:]
					repairs += 1
					found = True
					break
			if not found:
				raise  # Reparatur nicht möglich – originalen Fehler weitergeben


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _batch_nr_aus_dateiname(dateiname: str) -> int | None:
	"""Extrahiert die Batch-Nummer aus 'batch-05-proposals.json' → 5."""
	m = PROPOSAL_MUSTER.match(dateiname)
	return int(m.group(1)) if m else None


def _ist_proposal_datei(dateiname: str) -> bool:
	return PROPOSAL_MUSTER.match(dateiname) is not None


def lade_alle_slugs(ausschliessen: Path | None = None) -> dict[str, str]:
	"""
	Lädt alle Slugs aus allen vorhandenen Proposal-Dateien.
	Rückgabe: {slug: dateiname} – für Duplikat-Erkennung.
	Dateipfad in ausschliessen wird dabei ignoriert (für Re-Validation).
	"""
	slugs: dict[str, str] = {}
	for datei in PROPOSALS_DIR.glob("batch-*-proposals.json"):
		if ausschliessen and datei.resolve() == ausschliessen.resolve():
			continue
		try:
			roh = _bereinige_steuerzeichen(datei.read_text(encoding="utf-8"))
			eintraege, _ = _extrahiere_proposals_array(json.loads(roh))
			if eintraege is None:
				continue
			for e in eintraege:
				slug = e.get("slug", "")
				if slug:
					slugs[slug] = datei.name
		except (json.JSONDecodeError, OSError):
			pass
	return slugs


# ---------------------------------------------------------------------------
# Validierung
# ---------------------------------------------------------------------------


def validiere_datei(pfad: Path) -> dict:
	"""
	Validiert eine Proposal-Datei vollständig.

	Rückgabe:
	  {
	    "ok":              bool,
	    "fehler":          [str, ...],    # Schema- und Struktur-Fehler
	    "warnungen":       [str, ...],    # unbekannte Silos, fehlende optionale Felder
	    "duplikate":       [str, ...],    # Slugs die bereits in anderen Dateien vorkommen
	    "artikel_anzahl":  int,
	    "vorschlaege_anzahl": int,
	    "schlagwoerter_anzahl": int,
	    "cornerstones":    int,
	    "silos":           [str, ...],
	  }
	"""
	fehler:    list[str] = []
	warnungen: list[str] = []
	duplikate: list[str] = []

	# 1. JSON-Struktur prüfen – bei Anführungszeichen-Fehler Auto-Repair versuchen
	_leer = {"ok": False, "fehler": [], "warnungen": [], "duplikate": [],
	          "linguist_meta": None,
	          "artikel_anzahl": 0, "vorschlaege_anzahl": 0,
	          "schlagwoerter_anzahl": 0, "cornerstones": 0, "silos": []}
	try:
		inhalt = pfad.read_text(encoding="utf-8")
	except (OSError, UnicodeDecodeError) as exc:
		return {**_leer, "fehler": [f"Datei nicht lesbar: {exc}"]}

	# FIX 3: Steuerzeichen vor dem Parsing entfernen
	inhalt = _bereinige_steuerzeichen(inhalt)

	# Normalisierung: State-Machine escapet ungescapte " in Stringwerten
	normalisiert = _normalisiere_json_anführungszeichen(inhalt)
	if normalisiert != inhalt:
		log.info(
			"Normalisierung %s: ungescapte Anführungszeichen korrigiert.",
			pfad.name,
		)

	try:
		roh = json.loads(normalisiert)
		if normalisiert != inhalt:
			pfad.write_text(normalisiert, encoding="utf-8")
			log.warning(
				"Normalisierung %s: Datei mit escápten Anführungszeichen überschrieben.",
				pfad.name,
			)
	except json.JSONDecodeError:
		try:
			repariert, anzahl = _repariere_json_anführungszeichen(normalisiert, pfad)
			pfad.write_text(repariert, encoding="utf-8")
			log.warning(
				"Auto-Repair %s: %d weiteres Anführungszeichen(n) nach Normalisierung"
				" korrigiert und Datei überschrieben.",
				pfad.name, anzahl,
			)
			roh = json.loads(repariert)
		except json.JSONDecodeError as exc:
			return {**_leer, "fehler": [f"JSON ungültig: {exc}"]}

	# FIX 1: Wrapper-Format {"linguist_meta": {...}, "proposals": [...]}
	daten, linguist_meta = _extrahiere_proposals_array(roh)
	if daten is None:
		fehler.append(
			f"Root-Element ist weder ein Array noch ein Wrapper-Objekt mit "
			f"'proposals'-Schlüssel (erhalten: {type(roh).__name__})"
		)
		return {**_leer, "fehler": fehler}
	if linguist_meta:
		log.info(
			"Wrapper-Format erkannt in %s – linguist_meta: %s",
			pfad.name,
			", ".join(f"{k}={v!r}" for k, v in linguist_meta.items()),
		)

	# Bekannte Slugs aus anderen Dateien für Duplikat-Check
	bekannte_slugs = lade_alle_slugs(ausschliessen=pfad)
	silos_gefunden: set[str] = set()
	vorschlaege_gesamt	= 0
	schlagwoerter_gesamt = 0
	cornerstones		= 0
	slugs_in_datei: set[str] = set()

	for idx, eintrag in enumerate(daten):
		prefix = f"Eintrag {idx + 1}"

		# 3. Pflichtfelder und Typen prüfen
		for feld, erwarteter_typ in ARTIKEL_PFLICHT.items():
			if feld not in eintrag:
				fehler.append(f"{prefix}: Pflichtfeld '{feld}' fehlt")
				continue
			wert = eintrag[feld]
			if not isinstance(wert, erwarteter_typ):
				fehler.append(
					f"{prefix}.{feld}: Typ falsch "
					f"(erwartet {erwarteter_typ.__name__}, "
					f"erhalten {type(wert).__name__})"
				)

		slug = eintrag.get("slug", "")

		# 4. Duplikate innerhalb der Datei
		if slug:
			if slug in slugs_in_datei:
				fehler.append(f"{prefix}: Slug '{slug}' kommt mehrfach in dieser Datei vor")
			slugs_in_datei.add(slug)

		# 5. Duplikate zu anderen Dateien
		if slug and slug in bekannte_slugs:
			duplikate.append(
				f"Slug '{slug}' bereits in {bekannte_slugs[slug]}"
			)

		# 6. Silo-Validierung
		silo = eintrag.get("silo", "")
		if silo:
			silos_gefunden.add(silo)
			if silo not in GUELTIGE_SILOS:
				warnungen.append(
					f"{prefix}: Unbekannter Silo '{silo}' "
					f"(bekannte: {', '.join(sorted(GUELTIGE_SILOS))})"
				)

		# 7. kategorien (optional): muss ein Array von Strings sein
		kategorien = eintrag.get("kategorien")
		if kategorien is not None:
			if not isinstance(kategorien, list):
				fehler.append(f"{prefix}.kategorien: kein Array")
			else:
				for i, kat in enumerate(kategorien):
					if not isinstance(kat, str):
						fehler.append(f"{prefix}.kategorien[{i}]: kein String")

		# 9. neue_schlagwoerter: alle Einträge müssen Strings sein
		for i, tag in enumerate(eintrag.get("neue_schlagwoerter", [])):
			if not isinstance(tag, str):
				fehler.append(f"{prefix}.neue_schlagwoerter[{i}]: kein String")
			else:
				schlagwoerter_gesamt += 1

		# 10. link_vorschlaege prüfen
		link_vorschlaege = eintrag.get("link_vorschlaege", [])
		if not isinstance(link_vorschlaege, list):
			fehler.append(f"{prefix}.link_vorschlaege: kein Array")
		else:
			for li, link in enumerate(link_vorschlaege):
				lprefix = f"{prefix}.link_vorschlaege[{li}]"
				if not isinstance(link, dict):
					fehler.append(f"{lprefix}: kein Objekt")
					continue
				for feld, erwarteter_typ in LINK_PFLICHT.items():
					if feld not in link:
						fehler.append(f"{lprefix}: Pflichtfeld '{feld}' fehlt")
					elif not isinstance(link[feld], erwarteter_typ):
						fehler.append(
							f"{lprefix}.{feld}: Typ falsch "
							f"(erwartet {erwarteter_typ.__name__})"
						)
			vorschlaege_gesamt += len(link_vorschlaege)

		# 11. verwandte_artikel (optional) prüfen + Auto-Repair
		verwandte = eintrag.get("verwandte_artikel")
		if verwandte is not None:
			if not isinstance(verwandte, list):
				fehler.append(f"{prefix}.verwandte_artikel: kein Array")
			else:
				va_repariert = False
				for vi, va in enumerate(verwandte):
					vp = f"{prefix}.verwandte_artikel[{vi}]"
					if not isinstance(va, dict):
						fehler.append(f"{vp}: kein Objekt")
						continue
					# Auto-Repair: Alias-Feldnamen angleichen (nur String-Werte)
					if "titel" not in va and isinstance(va.get("name"), str):
						va["titel"] = va.pop("name");  va_repariert = True
					for alias in ("link", "href"):
						if "url" not in va and isinstance(va.get(alias), str):
							va["url"] = va.pop(alias);  va_repariert = True
					# Pflichtfelder prüfen
					for feld in ("titel", "url", "begruendung"):
						if feld not in va:
							msg = f"{vp}: Pflichtfeld '{feld}' fehlt"
							warnungen.append(msg)
							fehler.append(msg)
				if va_repariert:
					log.info(
						"Auto-Repair %s: Feldnamen in verwandte_artikel angeglichen.",
						pfad.name,
					)

		# 12. cornerstone zählen
		if eintrag.get("cornerstone") is True:
			cornerstones += 1

	ok = len(fehler) == 0

	return {
		"ok":                   ok,
		"fehler":               fehler,
		"warnungen":            warnungen,
		"duplikate":            duplikate,
		"linguist_meta":        linguist_meta,
		"artikel_anzahl":       len(daten),
		"vorschlaege_anzahl":   vorschlaege_gesamt,
		"schlagwoerter_anzahl": schlagwoerter_gesamt,
		"cornerstones":         cornerstones,
		"silos":                sorted(silos_gefunden),
	}


def drucke_ergebnis(dateiname: str, ergebnis: dict) -> None:
	"""Gibt das Validierungsergebnis einzeilig (OK) oder mehrzeilig (Fehler) aus."""
	n_art	= ergebnis["artikel_anzahl"]
	n_vor	= ergebnis["vorschlaege_anzahl"]
	n_tag	= ergebnis["schlagwoerter_anzahl"]
	n_cs	= ergebnis["cornerstones"]
	silos	= ", ".join(ergebnis["silos"]) or "–"

	meta = ergebnis.get("linguist_meta") or {}
	meta_str = ("  [" + ", ".join(f"{k}={v!r}" for k, v in meta.items()) + "]") if meta else ""

	if ergebnis["ok"] and not ergebnis["duplikate"] and not ergebnis["warnungen"]:
		log.info(
			"OK    %s: %d Artikel, %d Vorschläge, %d Schlagwörter, "
			"%d Cornerstone(s) – Silos: %s",
			dateiname, n_art, n_vor, n_tag, n_cs, silos,
		)
		print(f"  {dateiname}: {n_art} Artikel, {n_vor} Vorschläge – OK{meta_str}")
	else:
		status = "WARNUNG" if ergebnis["ok"] else "FEHLER"
		log.warning("%s  %s", status, dateiname)
		print(f"  {dateiname}: {n_art} Artikel, {n_vor} Vorschläge – {status}")

		for f in ergebnis["fehler"]:
			log.error("    FEHLER: %s", f)
			print(f"    ✗ {f}")
		for w in ergebnis["warnungen"]:
			log.warning("    WARNUNG: %s", w)
			print(f"    ⚠ {w}")
		for d in ergebnis["duplikate"]:
			log.warning("    DUPLIKAT: %s", d)
			print(f"    ⟳ {d}")


# ---------------------------------------------------------------------------
# Statusbericht: Batch-Übersicht
# ---------------------------------------------------------------------------


def statusbericht() -> None:
	"""
	Zeigt eine Übersicht aller Batch-Dateien und markiert,
	welche Proposal-Dateien fehlen.
	"""
	batch_dateien = sorted(BATCHES_DIR.glob("batch-*.md"))
	proposal_dateien = {
		f.name: f
		for f in PROPOSALS_DIR.glob("batch-*-proposals.json")
	}

	if not batch_dateien:
		print("  Keine Batch-Dateien in analysis/batches/ gefunden.")
		return

	vorhanden = sum(
		1 for b in batch_dateien
		if f"batch-{b.stem.split('-')[1]}-proposals.json" in proposal_dateien
	)

	print()
	print(
		f"  Batch-Übersicht: {len(batch_dateien)} Batches, "
		f"{vorhanden} Proposal-Dateien vorhanden"
	)
	print(f"  {'─' * 52}")

	for batch_pfad in batch_dateien:
		# Nummer aus "batch-05.md" extrahieren
		teile = batch_pfad.stem.split("-")
		if len(teile) < 2:
			continue
		nr_str		= teile[1]
		prop_name	= f"batch-{nr_str}-proposals.json"

		if prop_name in proposal_dateien:
			print(f"  ✓ {batch_pfad.name}  → {prop_name}")
		else:
			print(f"  – {batch_pfad.name}  (fehlt: {prop_name})")

	print()

	# Proposal-Dateien ohne passende Batch-Datei (verwaist)
	batch_nummern = set()
	for b in batch_dateien:
		teile = b.stem.split("-")
		if len(teile) >= 2:
			batch_nummern.add(teile[1])

	verwaist = [
		name for name in proposal_dateien
		if (m := PROPOSAL_MUSTER.match(name)) and m.group(1) not in batch_nummern
	]
	if verwaist:
		print("  Verwaiste Proposal-Dateien (kein passender Batch):")
		for name in sorted(verwaist):
			print(f"  ⚠ {name}")
		print()


# ---------------------------------------------------------------------------
# Validierungs-Modus (--validate)
# ---------------------------------------------------------------------------


def validiere_alle() -> None:
	"""Prüft alle vorhandenen Proposal-Dateien und gibt einen Statusbericht aus."""
	dateien = sorted(PROPOSALS_DIR.glob("batch-*-proposals.json"))

	print()
	print(f"  Validierung: {len(dateien)} Datei(en) gefunden")
	print()

	if not dateien:
		print("  Keine Proposal-Dateien in analysis/proposals/ gefunden.")
		print("  Batch-Dateien aus analysis/batches/ in Claude Desktop öffnen,")
		print("  Ausgabe als batch-XX-proposals.json speichern.")
	else:
		gesamt_artikel		= 0
		gesamt_vorschlaege	= 0
		gesamt_fehler		= 0

		for datei in dateien:
			ergebnis = validiere_datei(datei)
			drucke_ergebnis(datei.name, ergebnis)
			gesamt_artikel		+= ergebnis["artikel_anzahl"]
			gesamt_vorschlaege	+= ergebnis["vorschlaege_anzahl"]
			if not ergebnis["ok"] or ergebnis["duplikate"]:
				gesamt_fehler += 1

		print()
		print(f"  Gesamt: {gesamt_artikel} Artikel, {gesamt_vorschlaege} Vorschläge", end="")
		if gesamt_fehler == 0:
			print(" – alle valide")
		else:
			print(f" – {gesamt_fehler} Datei(en) mit Fehlern")

	statusbericht()


# ---------------------------------------------------------------------------
# Watcher-Modus
# ---------------------------------------------------------------------------


def _starte_watcher() -> None:
	"""Startet watchdog-Observer für analysis/proposals/."""
	try:
		from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileSystemEventHandler
		from watchdog.observers import Observer
	except ImportError:
		log.error(
			"watchdog nicht installiert. Bitte ausführen: "
			"pip install watchdog"
		)
		print()
		print("  FEHLER: watchdog nicht installiert.")
		print("  Installation:  pip install watchdog")
		print("  Alternative:   python scripts/proposal_import.py --validate")
		sys.exit(1)

	class _Handler(FileSystemEventHandler):
		def _verarbeite(self, pfad_str: str) -> None:
			pfad = Path(pfad_str)
			if not _ist_proposal_datei(pfad.name):
				return
			# Kurze Pause: Schreibvorgang abwarten
			time.sleep(0.3)
			log.info("Neue/geänderte Datei erkannt: %s", pfad.name)
			ergebnis = validiere_datei(pfad)
			drucke_ergebnis(pfad.name, ergebnis)

		def on_created(self, event: FileCreatedEvent) -> None:
			if not event.is_directory:
				self._verarbeite(event.src_path)

		def on_modified(self, event: FileModifiedEvent) -> None:
			if not event.is_directory:
				self._verarbeite(event.src_path)

	observer = Observer()
	observer.schedule(_Handler(), str(PROPOSALS_DIR), recursive=False)
	observer.start()

	log.info("=== Watcher aktiv ===")
	log.info("Überwacht: %s", PROPOSALS_DIR)
	log.info("Warte auf neue Proposal-Dateien … (Strg+C zum Beenden)")
	print()
	print(f"  Watcher läuft – überwacht {PROPOSALS_DIR.relative_to(PROJECT_ROOT)}")
	print("  Strg+C zum Beenden")
	print()

	try:
		while True:
			time.sleep(1)
	except KeyboardInterrupt:
		log.info("Watcher gestoppt.")
	finally:
		observer.stop()
		observer.join()


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Modul 3b: Proposal-Validator und Watcher"
	)
	parser.add_argument(
		"--validate",
		action="store_true",
		help="Alle vorhandenen Proposal-Dateien prüfen und Statusbericht ausgeben",
	)
	args = parser.parse_args()

	if args.validate:
		log.info("=== Validierungs-Modus ===")
		validiere_alle()
	else:
		_starte_watcher()


if __name__ == "__main__":
	main()
