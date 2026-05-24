"""
status.py – Projektfortschritt auf einen Blick

Liest Verzeichnisse aus und zeigt den aktuellen Stand aller
Workflow-Stufen: Batch-Analyse → Freigabe → WordPress-Sync.

Gesamtfortschritt-Formel (gewichtet):
  Analyse  50 % – wie viele Batches haben gültige Proposals
  Freigabe 30 % – wie viele Vorschläge wurden freigegeben
  WordPress 20 % – wie viele wurden nach WordPress eingespielt

Aufruf:
    .venv/bin/python scripts/status.py
    .venv/bin/python scripts/status.py --update-claude
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
BATCHES_DIR   = PROJECT_ROOT / "analysis" / "batches"
PROPOSALS_DIR = PROJECT_ROOT / "analysis" / "proposals"
APPROVED_DIR  = PROJECT_ROOT / "output" / "approved"
CLAUDE_MD     = PROJECT_ROOT / "CLAUDE.md"

_MODI = ("links", "tags", "kategorien")

# Sentinel-Marker für den automatisch aktualisierten Block in CLAUDE.md
SENTINEL_START = "<!-- STATUS:START -->"
SENTINEL_END   = "<!-- STATUS:END -->"

# ---------------------------------------------------------------------------
# Daten sammeln
# ---------------------------------------------------------------------------


def sammle_status() -> dict:
	"""Liest alle relevanten Verzeichnisse aus und gibt ein Status-Dict zurück."""

	# 1. Batches
	batches_gesamt = len(list(BATCHES_DIR.glob("batch-*.md")))

	# 2. Proposals – JSON einlesen, Syntax prüfen, Links zählen
	proposals_gueltig    = 0
	proposals_fehler     = 0
	vorschlaege_gesamt   = 0	# einzelne link_vorschlaege-Einträge
	artikel_gesamt       = 0	# Artikel-Einträge (Slugs)

	_steuerzeichen = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

	for pf in sorted(PROPOSALS_DIR.glob("batch-*-proposals.json")):
		try:
			roh   = _steuerzeichen.sub(' ', pf.read_text(encoding="utf-8"))
			daten = json.loads(roh)
			# Wrapper-Format {"proposals": [...]} oder reines Array
			if isinstance(daten, dict) and isinstance(daten.get("proposals"), list):
				eintraege = daten["proposals"]
			elif isinstance(daten, list):
				eintraege = daten
			else:
				proposals_fehler += 1
				continue
			proposals_gueltig += 1
			artikel_gesamt    += len(eintraege)
			for eintrag in eintraege:
				vorschlaege_gesamt += len(eintrag.get("link_vorschlaege", []))
		except (json.JSONDecodeError, OSError):
			proposals_fehler += 1

	# 3. Freigegeben und eingespielt – pro Modus
	def _n(verz: Path) -> int:
		return len([f for f in verz.glob("*.json") if f.is_file()]) if verz.exists() else 0

	approved = {m: _n(APPROVED_DIR / m)          for m in _MODI}
	done     = {m: _n(APPROVED_DIR / m / "done") for m in _MODI}

	approved_gesamt     = sum(approved.values())
	done_gesamt         = sum(done.values())
	freigegebene_gesamt = approved_gesamt + done_gesamt   # approved + done zusammen

	# ---------------------------------------------------------------------------
	# Fortschritt in Prozent
	# ---------------------------------------------------------------------------

	# Stufe 1 – Analyse
	analyse_pct = round(proposals_gueltig / batches_gesamt * 100) if batches_gesamt else 0

	# Stufe 2 – Freigabe: Nenner = Artikel × 3 Modi (maximale Anzahl approvbarer Dateien)
	freigabe_max = artikel_gesamt * len(_MODI) if artikel_gesamt else 1
	freigabe_pct = min(100, round(freigegebene_gesamt / freigabe_max * 100))

	# Stufe 3 – WordPress: eingespielt / Maximum
	wp_pct = min(100, round(done_gesamt / freigabe_max * 100))

	# Gesamtfortschritt: gewichteter Durchschnitt
	gesamt_pct = round(analyse_pct * 0.5 + freigabe_pct * 0.3 + wp_pct * 0.2)

	return {
		"batches_gesamt":      batches_gesamt,
		"proposals_gueltig":   proposals_gueltig,
		"proposals_fehler":    proposals_fehler,
		"artikel_gesamt":      artikel_gesamt,
		"vorschlaege_gesamt":  vorschlaege_gesamt,
		"approved_links":      approved["links"],
		"approved_tags":       approved["tags"],
		"approved_kat":        approved["kategorien"],
		"done_links":          done["links"],
		"done_tags":           done["tags"],
		"done_kat":            done["kategorien"],
		"approved_gesamt":     approved_gesamt,
		"done_gesamt":         done_gesamt,
		"freigegebene_gesamt": freigegebene_gesamt,
		"freigabe_max":        freigabe_max,
		"analyse_pct":         analyse_pct,
		"freigabe_pct":        freigabe_pct,
		"wp_pct":              wp_pct,
		"gesamt_pct":          gesamt_pct,
		"zeitstempel":         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
	}


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------


def balken(pct: int, breite: int = 25) -> str:
	"""Unicode-Fortschrittsbalken: ████░░░░░░"""
	gefuellt = round(pct / 100 * breite)
	return "█" * gefuellt + "░" * (breite - gefuellt)


def drucke_status(s: dict) -> None:
	"""Gibt den Status formatiert im Terminal aus."""

	leer = s["vorschlaege_gesamt"] == 0
	vsg  = s["vorschlaege_gesamt"] or 1  # Division-by-Zero-Schutz in Anzeige

	print()
	print("  ╔══════════════════════════════════════════╗")
	print("  ║   SEO Crawler – Projektfortschritt        ║")
	print("  ╚══════════════════════════════════════════╝")
	print()

	# Stufe 1 – Batch-Analyse
	print(f"  Stufe 1 · Batch-Analyse")
	print(f"    Batches erstellt    {s['batches_gesamt']:>4}")
	print(
		f"    Proposals valide    {s['proposals_gueltig']:>4} / {s['batches_gesamt']}"
		f"   {balken(s['analyse_pct'], 20)}  {s['analyse_pct']:>3} %"
	)
	if s["proposals_fehler"]:
		print(f"    ⚠  {s['proposals_fehler']} Datei(en) mit JSON-Fehlern")
	if s["proposals_gueltig"]:
		print(f"    Link-Vorschläge     {s['vorschlaege_gesamt']:>4}  ({s['artikel_gesamt']} Artikel)")
	print()

	# Stufe 2 – Freigabe
	print(f"  Stufe 2 · Freigabe (freigabe_server.py)")
	if leer:
		print(f"    Ausstehend          noch keine Proposals vorhanden")
	else:
		print(
			f"    Freigegeben         {s['freigegebene_gesamt']:>4} / {s['freigabe_max']}"
			f"   {balken(s['freigabe_pct'], 20)}  {s['freigabe_pct']:>3} %"
		)
		print(f"    · approved   Links {s['approved_links']:>4}  Tags {s['approved_tags']:>4}  Kategorien {s['approved_kat']:>4}")
		print(f"    · eingespielt Links {s['done_links']:>4}  Tags {s['done_tags']:>4}  Kategorien {s['done_kat']:>4}")
	print()

	# Stufe 3 – WordPress
	print(f"  Stufe 3 · WordPress eingespielt")
	if leer:
		print(f"    Ausstehend          noch keine Proposals vorhanden")
	else:
		print(
			f"    Eingespielt         {s['done_gesamt']:>4} / {s['freigabe_max']}"
			f"   {balken(s['wp_pct'], 20)}  {s['wp_pct']:>3} %"
		)
	print()

	# Gesamtfortschritt
	print(f"  ─────────────────────────────────────────────")
	print(
		f"  Gesamtfortschritt   {balken(s['gesamt_pct'], 30)}  {s['gesamt_pct']:>3} %"
	)
	print(f"  (Gewichtung: Analyse 50 % · Freigabe 30 % · WP 20 %)")
	print()
	print(f"  Stand: {s['zeitstempel']}")
	print()


# ---------------------------------------------------------------------------
# CLAUDE.md aktualisieren
# ---------------------------------------------------------------------------


def claude_block(s: dict) -> str:
	"""Erzeugt den Markdown-Block, der in CLAUDE.md eingefügt wird."""

	b_analyse  = balken(s["analyse_pct"],  15)
	b_freigabe = balken(s["freigabe_pct"], 15)
	b_wp       = balken(s["wp_pct"],       15)
	b_gesamt   = balken(s["gesamt_pct"],   20)

	zeilen_proposals = (
		f"{s['proposals_gueltig']} / {s['batches_gesamt']} Batches · "
		f"{s['vorschlaege_gesamt']} Vorschläge · {s['artikel_gesamt']} Artikel"
		if s["proposals_gueltig"] else
		f"0 / {s['batches_gesamt']} Batches"
	)

	warnung = ""
	if s["proposals_fehler"]:
		warnung = f"\n> ⚠ {s['proposals_fehler']} Proposal-Datei(en) mit JSON-Fehlern – `proposal_import.py --validate` ausführen.\n"

	return (
		f"{SENTINEL_START}\n"
		f"*Zuletzt aktualisiert: {s['zeitstempel']} · "
		f"Aktualisieren: `.venv/bin/python scripts/status.py --update-claude`*\n"
		f"{warnung}\n"
		f"| Stufe | Stand | Fortschritt |\n"
		f"|---|---|---|\n"
		f"| Batches erstellt | {s['batches_gesamt']} | – |\n"
		f"| Proposals valide | {zeilen_proposals} | `{b_analyse}` {s['analyse_pct']} % |\n"
		f"| Freigegeben | {s['freigegebene_gesamt']} / {s['freigabe_max']} | `{b_freigabe}` {s['freigabe_pct']} % |\n"
		f"| WordPress eingespielt | {s['done_gesamt']} / {s['freigabe_max']} | `{b_wp}` {s['wp_pct']} % |\n"
		f"\n"
		f"**Gesamtfortschritt:** `{b_gesamt}` **{s['gesamt_pct']} %**\n"
		f"*(Gewichtung: Analyse 50 % · Freigabe 30 % · WordPress 20 %)*\n"
		f"{SENTINEL_END}"
	)


def aktualisiere_claude_md(s: dict) -> None:
	"""Ersetzt den Sentinel-Block in CLAUDE.md durch aktuelle Daten."""

	if not CLAUDE_MD.exists():
		print(f"FEHLER: {CLAUDE_MD} nicht gefunden.", file=sys.stderr)
		sys.exit(1)

	inhalt = CLAUDE_MD.read_text(encoding="utf-8")

	if SENTINEL_START not in inhalt or SENTINEL_END not in inhalt:
		print(
			f"FEHLER: Sentinel-Marker nicht in {CLAUDE_MD.name} gefunden.\n"
			f"  Erwartet: {SENTINEL_START!r} … {SENTINEL_END!r}",
			file=sys.stderr,
		)
		sys.exit(1)

	muster = re.compile(
		re.escape(SENTINEL_START) + r".*?" + re.escape(SENTINEL_END),
		re.DOTALL,
	)
	neu = muster.sub(claude_block(s), inhalt)
	CLAUDE_MD.write_text(neu, encoding="utf-8")
	print(f"  CLAUDE.md aktualisiert.")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Projektfortschritt anzeigen"
	)
	parser.add_argument(
		"--update-claude",
		action="store_true",
		help="Zusätzlich den Status-Block in CLAUDE.md aktualisieren",
	)
	args = parser.parse_args()

	s = sammle_status()
	drucke_status(s)

	if args.update_claude:
		aktualisiere_claude_md(s)


if __name__ == "__main__":
	main()
