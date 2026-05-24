"""
embeddings.py – Modul 2: Embedding-Berechnung

Liest alle geparsten Artikel aus data/parsed/, erstellt pro Artikel
einen Embedding-Vektor und speichert alles unter data/embeddings/.

Ausgabe:
    data/embeddings/vectors.npz   – numpy-Array aller Vektoren
    data/embeddings/index.json    – Zuordnung Vektor-Index → slug + url

Aufruf:
    python scripts/embeddings.py           # überspringt bereits berechnete Slugs
    python scripts/embeddings.py --force   # berechnet alle neu
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
EMBED_DIR = PROJECT_ROOT / "data" / "embeddings"
LOG_DIR = PROJECT_ROOT / "logs"

EMBED_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / "env.local")

VECTORS_FILE = EMBED_DIR / "vectors.npz"
INDEX_FILE = EMBED_DIR / "index.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "embeddings.log"

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
# Modell
# ---------------------------------------------------------------------------

MODEL_NAME = "T-Systems-onsite/cross-en-de-roberta-sentence-transformer"
MAX_WORDS = 500

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def build_input_text(article: dict) -> str:
    """Titel + erste MAX_WORDS Wörter des Markdown-Fließtexts."""
    title = article.get("title", "").strip()
    markdown = article.get("markdown", "").strip()
    words = markdown.split()
    truncated = " ".join(words[:MAX_WORDS])
    return f"{title}\n\n{truncated}" if title else truncated


def load_index() -> dict:
    """Liest index.json; gibt leeres Dict zurück, wenn die Datei fehlt."""
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {}


def save_index(index: dict) -> None:
    INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_vectors() -> np.ndarray | None:
    """Liest vectors.npz; gibt None zurück, wenn die Datei fehlt."""
    if VECTORS_FILE.exists():
        data = np.load(VECTORS_FILE)
        return data["vectors"]
    return None


def save_vectors(vectors: np.ndarray) -> None:
    np.savez_compressed(VECTORS_FILE, vectors=vectors)


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SEO Embeddings – Modul 2: Vektor-Berechnung"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Alle Embeddings neu berechnen, auch bereits vorhandene",
    )
    args = parser.parse_args()

    # Vorhandene JSON-Dateien einlesen
    json_files = sorted(PARSED_DIR.glob("*.json"))
    if not json_files:
        log.error("Keine JSON-Dateien in %s – zuerst crawler.py ausführen.", PARSED_DIR)
        sys.exit(1)

    log.info("=== Embeddings gestartet ===")
    log.info("Artikel gefunden: %d", len(json_files))

    # Vorhandenen Index laden
    index: dict = {} if args.force else load_index()
    existing_slugs: set[str] = set(
        entry["slug"] for entry in index.values()
    )

    # Bestehende Vektoren laden (als Python-Liste für einfaches Anhängen)
    existing_vectors: list[np.ndarray] = []
    if not args.force:
        loaded = load_vectors()
        if loaded is not None:
            existing_vectors = list(loaded)
            log.info("Vorhandene Vektoren geladen: %d", len(existing_vectors))
    else:
        log.info("Modus: --force (alles wird neu berechnet)")
        index = {}

    # Artikel filtern, die noch nicht berechnet wurden
    pending: list[tuple[str, dict]] = []
    for jf in json_files:
        article = json.loads(jf.read_text(encoding="utf-8"))
        slug = article.get("slug") or jf.stem
        if slug in existing_slugs and not args.force:
            continue
        pending.append((slug, article))

    if not pending:
        log.info("Alle Slugs bereits vorhanden – nichts zu tun. (--force zum Neuberechnen)")
        return

    log.info("Zu berechnen: %d Artikel", len(pending))

    # Modell laden (einmalig, beim ersten Lauf wird es heruntergeladen)
    log.info("Lade Modell: %s", MODEL_NAME)
    model = SentenceTransformer(MODEL_NAME)
    log.info("Modell geladen.")

    # Embedding-Berechnung mit Fortschrittsanzeige
    new_vectors: list[np.ndarray] = []
    total = len(pending)

    for i, (slug, article) in enumerate(pending, start=1):
        input_text = build_input_text(article)
        vector = model.encode(input_text, normalize_embeddings=True)
        new_vectors.append(vector)

        # Position im finalen Array = vorhandene + neue (0-basiert)
        position = len(existing_vectors) + len(new_vectors) - 1
        index[str(position)] = {
            "slug": slug,
            "url": article.get("url", ""),
            "title": article.get("title", ""),
        }

        log.info(
            "Artikel %d von %d  [%s]",
            i + (len(existing_vectors) if not args.force else 0),
            total + (len(existing_vectors) if not args.force else 0),
            slug,
        )

    # Vektoren zusammenführen und speichern
    all_vectors = existing_vectors + new_vectors
    save_vectors(np.array(all_vectors, dtype=np.float32))
    save_index(index)

    log.info(
        "=== Fertig ===  Gesamt-Vektoren: %d  Neu berechnet: %d",
        len(all_vectors),
        len(new_vectors),
    )
    log.info("Vektoren: %s", VECTORS_FILE)
    log.info("Index:    %s", INDEX_FILE)


if __name__ == "__main__":
    main()
