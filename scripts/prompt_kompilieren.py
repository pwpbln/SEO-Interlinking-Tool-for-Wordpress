"""
prompt_kompilieren.py – Prompt-Compiler

Liest docs/linguist-prompt.md als Vorlage und erstellt
docs/linguist-prompt-compiled.md als einsatzbereite Prompt-Datei.

Modus A – Claude Desktop App (Standard):
    .venv/bin/python scripts/prompt_kompilieren.py

    Lesebefehle statt Dateiinhalte; der Linguist liest
    seo-kontext.md und die Batch-Datei selbst.

Modus B – Andere Modelle ohne Dateizugriff:
    .venv/bin/python scripts/prompt_kompilieren.py --modus andere-modelle

    SEO-Kontext und Schlagwort-Pool direkt eingebettet;
    Batch-Inhalt als Platzhalter.

Quellen (nur Lesezugriff):
    docs/linguist-prompt.md
    seo-kontext.md
    data/taxonomie/pool-final.json

Ausgabe (überschreibt vorhandene Datei):
    docs/linguist-prompt-compiled.md
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT      = Path(__file__).resolve().parent.parent
DOCS_DIR          = PROJECT_ROOT / "docs"
VORLAGE           = DOCS_DIR / "linguist-prompt.md"
AUSGABE           = DOCS_DIR / "linguist-prompt-compiled.md"
SEO_KONTEXT       = PROJECT_ROOT / "seo-kontext.md"
POOL_DATEI        = PROJECT_ROOT / "data" / "taxonomie" / "pool-final.json"
KATEGORIEN_DATEI  = PROJECT_ROOT / "data" / "taxonomie" / "kategorien-export.json"
LOG_DIR           = PROJECT_ROOT / "logs"

LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "prompt_kompilieren.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# Marker: alles vor diesem Text ist der unveränderliche Prompt-Kern.
# Der Rest wird je nach Modus ersetzt.
BATCH_MARKER = '\nLesen Sie "analysis/batches/'

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def lade_vorlage() -> tuple[str, str]:
    """
    Liest linguist-prompt.md und trennt in Kern und Batch-Abschnitt.
    Rückgabe: (kern, batch_abschnitt)
    """
    if not VORLAGE.exists():
        log.error("Vorlage nicht gefunden: %s", VORLAGE)
        sys.exit(1)
    text = VORLAGE.read_text(encoding="utf-8")
    idx = text.find(BATCH_MARKER)
    if idx == -1:
        log.error(
            "Trennmarker nicht gefunden in %s.\n"
            "  Erwartet: Zeile die mit 'Lesen Sie \"analysis/batches/' beginnt.",
            VORLAGE.name,
        )
        sys.exit(1)
    return text[:idx].rstrip(), text[idx:]


def lade_seo_kontext() -> str:
    if not SEO_KONTEXT.exists():
        log.warning("seo-kontext.md nicht gefunden – Abschnitt bleibt leer.")
        return "(nicht verfügbar)"
    return SEO_KONTEXT.read_text(encoding="utf-8").strip()


def lade_kategorien() -> list[dict]:
    if not KATEGORIEN_DATEI.exists():
        log.warning("kategorien-export.json nicht gefunden – Kategorien-Liste bleibt leer.")
        return []
    try:
        daten = json.loads(KATEGORIEN_DATEI.read_text(encoding="utf-8"))
        return daten if isinstance(daten, list) else []
    except Exception as exc:
        log.warning("kategorien-export.json nicht lesbar: %s", exc)
        return []


def formatiere_kategorien_flach(kategorien: list[dict]) -> str:
    """Flache Liste: 'Name (ID: X)', alphabetisch nach Name."""
    zeilen = [
        f"{k.get('name', '').strip()} (ID: {k.get('id', '?')})"
        for k in sorted(kategorien, key=lambda k: k.get("name", "").lower())
        if k.get("name", "").strip()
    ]
    return "\n".join(zeilen)


def lade_pool() -> list[dict]:
    if not POOL_DATEI.exists():
        log.warning("pool-final.json nicht gefunden – leere Pool-Liste.")
        return []
    try:
        pool = json.loads(POOL_DATEI.read_text(encoding="utf-8"))
        return pool if isinstance(pool, list) else []
    except Exception as exc:
        log.warning("pool-final.json nicht lesbar: %s", exc)
        return []


def formatiere_pool(pool: list[dict]) -> str:
    """
    Flache Liste, alphabetisch, ein Begriff pro Zeile.
    Format: "Begriff (N)" wenn wp_count oder count > 0, sonst nur "Begriff".
    """
    zeilen: list[str] = []
    for eintrag in sorted(pool, key=lambda e: e.get("name", "").lower()):
        name = eintrag.get("name", "").strip()
        if not name:
            continue
        n = eintrag.get("wp_count") or eintrag.get("count") or 0
        zeilen.append(f"{name} ({n})" if n else name)
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Modus A – Claude Desktop App
# ---------------------------------------------------------------------------

BLOCK_MODUS_A = """\

Lesen Sie vor der Analyse folgende Dateien:
1. seo-kontext.md im Projektstammverzeichnis
2. analysis/batches/batch-[XX].md (Batch-Datei – Nummer beim jeweiligen Aufruf einsetzen)

Der vollständige Schlagwort-Pool steht im Abschnitt "Bestehender Schlagwort-Pool" \
der Batch-Datei. Nutzen Sie diesen als Controlled Vocabulary.

Die verfügbaren WordPress-Kategorien stehen im Abschnitt "Verfügbare WordPress-Kategorien" \
der Batch-Datei. Verwenden Sie für das Feld `kategorien` exakt die dort aufgeführten Namen."""


# ---------------------------------------------------------------------------
# Modus B – Andere Modelle
# ---------------------------------------------------------------------------


def block_modus_b(seo_kontext: str, pool_liste: str, kategorien_liste: str) -> str:
    kat_abschnitt = (
        f"\n## Verfügbare WordPress-Kategorien\n\n"
        f"Verwenden Sie für das Feld `kategorien` exakt diese Namen:\n\n"
        f"{kategorien_liste}\n"
        if kategorien_liste else ""
    )
    return f"""

## SEO-Kontext

{seo_kontext}
{kat_abschnitt}
## Schlagwort-Pool (Controlled Vocabulary)

{pool_liste}

## Batch-Inhalt

[PLATZHALTER – hier den Batch-Inhalt einfügen]

## Hinweis für andere Modelle

Sie haben keinen direkten Dateizugriff. SEO-Kontext, Kategorien und Schlagwort-Pool \
sind oben eingebettet. Den Batch-Inhalt erhalten Sie separat. Geben Sie das Ergebnis als \
JSON-Codeblock aus. Schreiben Sie den Dateinamen als Überschrift über den Block:

batch-XX-proposals_[MODELLNAME].json"""


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def kompiliere(modus: str = "claude-desktop") -> None:
    """
    Öffentliche API – kann direkt importiert werden:
        from prompt_kompilieren import kompiliere
        kompiliere(modus="claude-desktop")  # oder "andere-modelle"
    """
    kern, _ = lade_vorlage()
    pool    = lade_pool()

    if modus == "andere-modelle":
        seo_kontext      = lade_seo_kontext()
        pool_liste       = formatiere_pool(pool)
        kategorien       = lade_kategorien()
        kategorien_liste = formatiere_kategorien_flach(kategorien)
        anhang           = block_modus_b(seo_kontext, pool_liste, kategorien_liste)
        modus_label      = "Andere Modelle"
    else:
        anhang      = BLOCK_MODUS_A
        modus_label = "Claude Desktop App"
        kategorien  = lade_kategorien()   # nur für Zählung, nicht eingebettet

    inhalt = kern + anhang + "\n"
    AUSGABE.write_text(inhalt, encoding="utf-8")

    eingebettet = modus == "andere-modelle"
    pool_label  = "Pool-Begriffe eingebettet" if eingebettet else "Pool-Begriffe gefunden  "
    kat_label   = "Kategorien eingebettet   " if eingebettet else "Kategorien gefunden     "

    print()
    print(f"  Modus:              {modus_label}")
    print(f"  {pool_label}: {len(pool)}")
    print(f"  {kat_label}: {len(kategorien)}")
    print(f"  Ausgabe:            {AUSGABE}")
    print()
    log.info(
        "=== Fertig ===  Modus: %s  Pool: %d  Kategorien: %d  → %s",
        modus_label, len(pool), len(kategorien), AUSGABE,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SEO Crawler – Prompt-Compiler für linguist-prompt.md"
    )
    parser.add_argument(
        "--modus",
        choices=["claude-desktop", "andere-modelle"],
        default="claude-desktop",
        metavar="MODUS",
        help="claude-desktop (Standard) | andere-modelle",
    )
    args = parser.parse_args()
    kompiliere(modus=args.modus)


if __name__ == "__main__":
    main()
