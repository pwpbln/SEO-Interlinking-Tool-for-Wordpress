"""
clustering.py – Modul 2b: Semantisches Clustering

Liest data/embeddings/vectors.npz und data/embeddings/index.json,
gruppiert die Artikel mit HDBSCAN in semantische Cluster und schreibt
zwei Ausgabedateien:

    analysis/clusters/cluster-ergebnis.json  – maschinenlesbar
    analysis/clusters/cluster-bericht.txt    – für die manuelle Auswertung

Aufruf:
    python scripts/clustering.py
    python scripts/clustering.py --min-cluster-size 3
    python scripts/clustering.py --metric euclidean
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from sklearn.cluster import HDBSCAN

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT	= Path(__file__).resolve().parent.parent
EMBED_DIR		= PROJECT_ROOT / "data" / "embeddings"
CLUSTER_DIR		= PROJECT_ROOT / "analysis" / "clusters"
LOG_DIR			= PROJECT_ROOT / "logs"

VECTORS_FILE	= EMBED_DIR / "vectors.npz"
INDEX_FILE		= EMBED_DIR / "index.json"
RESULT_JSON		= CLUSTER_DIR / "cluster-ergebnis.json"
RESULT_TXT		= CLUSTER_DIR / "cluster-bericht.txt"

CLUSTER_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / "env.local")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "clustering.log"

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
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def lade_vektoren() -> np.ndarray:
	"""Lädt vectors.npz und gibt das float32-Array zurück."""
	if not VECTORS_FILE.exists():
		log.error("Vektoren-Datei nicht gefunden: %s", VECTORS_FILE)
		log.error("Zuerst scripts/embeddings.py ausführen.")
		sys.exit(1)
	data = np.load(VECTORS_FILE)
	return data["vectors"].astype(np.float32)


def lade_index() -> dict:
	"""Lädt index.json und gibt das Dict zurück."""
	if not INDEX_FILE.exists():
		log.error("Index-Datei nicht gefunden: %s", INDEX_FILE)
		sys.exit(1)
	return json.loads(INDEX_FILE.read_text(encoding="utf-8"))


def baue_ergebnis(
	labels: np.ndarray,
	index: dict,
	min_cluster_size: int,
	metric: str,
) -> dict:
	"""
	Fasst Cluster-Labels und Index-Einträge zu einem strukturierten
	Ergebnis-Dict zusammen.
	Label -1 bedeutet 'Rauschen' (keinem Cluster zugeordnet).
	"""
	cluster_map: dict[int, list[dict]] = {}
	rauschen: list[dict] = []

	for pos_str, eintrag in index.items():
		pos = int(pos_str)
		if pos >= len(labels):
			# Index und Vektor-Datei sind inkonsistent
			log.warning("Position %d fehlt in Vektoren-Array – übersprungen.", pos)
			continue

		artikel = {
			"slug":		eintrag.get("slug", ""),
			"titel":	eintrag.get("title", ""),
			"url":		eintrag.get("url", ""),
		}
		label = int(labels[pos])

		if label == -1:
			rauschen.append(artikel)
		else:
			cluster_map.setdefault(label, []).append(artikel)

	# Innerhalb jedes Clusters alphabetisch nach Slug sortieren
	cluster_liste = []
	for label in sorted(cluster_map.keys()):
		artikel_sortiert = sorted(cluster_map[label], key=lambda a: a["slug"])
		cluster_liste.append({
			"cluster_id":		label,
			"anzahl_artikel":	len(artikel_sortiert),
			"artikel":			artikel_sortiert,
		})

	return {
		"meta": {
			"erstellt_am":		datetime.now(timezone.utc).isoformat(),
			"min_cluster_size":	min_cluster_size,
			"metrik":			metric,
			"anzahl_cluster":	len(cluster_liste),
			"artikel_gesamt":	len(index),
			"artikel_rauschen":	len(rauschen),
		},
		"cluster":	cluster_liste,
		"rauschen":	sorted(rauschen, key=lambda a: a["slug"]),
	}


def schreibe_json(ergebnis: dict) -> None:
	"""Schreibt das Ergebnis-Dict als formatiertes JSON."""
	RESULT_JSON.write_text(
		json.dumps(ergebnis, ensure_ascii=False, indent=2),
		encoding="utf-8",
	)
	log.info("JSON geschrieben: %s", RESULT_JSON)


def schreibe_bericht(ergebnis: dict) -> None:
	"""Schreibt eine menschenlesbare Textdatei mit allen Clustern."""
	meta	= ergebnis["meta"]
	cluster	= ergebnis["cluster"]
	rauschen = ergebnis["rauschen"]

	zeilen: list[str] = []

	zeilen.append("=" * 72)
	zeilen.append("SEO CRAWLER – CLUSTER-BERICHT")
	zeilen.append("=" * 72)
	zeilen.append(f"Erstellt am:        {meta['erstellt_am']}")
	zeilen.append(f"Metrik:             {meta['metrik']}")
	zeilen.append(f"min_cluster_size:   {meta['min_cluster_size']}")
	zeilen.append(f"Cluster gefunden:   {meta['anzahl_cluster']}")
	zeilen.append(f"Artikel gesamt:     {meta['artikel_gesamt']}")
	zeilen.append(f"Ohne Cluster:       {meta['artikel_rauschen']}")
	zeilen.append("")

	for c in cluster:
		zeilen.append("-" * 72)
		zeilen.append(
			f"CLUSTER {c['cluster_id']:>3}  ({c['anzahl_artikel']} Artikel)"
		)
		zeilen.append("-" * 72)
		for a in c["artikel"]:
			zeilen.append(f"  [{a['slug']}]")
			zeilen.append(f"    {a['titel']}")
			zeilen.append(f"    {a['url']}")
		zeilen.append("")

	if rauschen:
		zeilen.append("=" * 72)
		zeilen.append(f"RAUSCHEN – {len(rauschen)} Artikel ohne Cluster-Zuordnung")
		zeilen.append("=" * 72)
		for a in rauschen:
			zeilen.append(f"  [{a['slug']}]  {a['titel']}")
		zeilen.append("")

	RESULT_TXT.write_text("\n".join(zeilen), encoding="utf-8")
	log.info("Bericht geschrieben: %s", RESULT_TXT)


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
	parser = argparse.ArgumentParser(
		description="SEO Crawler – Modul 2b: Semantisches Clustering"
	)
	parser.add_argument(
		"--min-cluster-size",
		type=int,
		default=5,
		metavar="N",
		help="Mindestgröße eines Clusters (Standard: 5)",
	)
	parser.add_argument(
		"--metric",
		default="cosine",
		choices=["cosine", "euclidean"],
		help="Distanzmetrik für HDBSCAN (Standard: cosine)",
	)
	args = parser.parse_args()

	log.info("=== Clustering gestartet ===")
	log.info(
		"Parameter: min_cluster_size=%d  metric=%s",
		args.min_cluster_size,
		args.metric,
	)

	# Daten laden
	vektoren = lade_vektoren()
	index    = lade_index()

	log.info("Vektoren geladen: %d × %d", vektoren.shape[0], vektoren.shape[1])

	if len(index) != vektoren.shape[0]:
		log.warning(
			"Inkonsistenz: Index hat %d Einträge, Vektoren-Array hat %d Zeilen.",
			len(index),
			vektoren.shape[0],
		)

	# Mindestens min_cluster_size Artikel nötig, sonst sinnlos
	if vektoren.shape[0] < args.min_cluster_size:
		log.error(
			"Zu wenige Artikel (%d) für min_cluster_size=%d – Abbruch.",
			vektoren.shape[0],
			args.min_cluster_size,
		)
		sys.exit(1)

	# HDBSCAN
	log.info("Starte HDBSCAN …")
	clusterer = HDBSCAN(
		min_cluster_size=args.min_cluster_size,
		metric=args.metric,
		# store_centers für spätere Zentroid-Nutzung aktivieren
		store_centers="centroid",
	)
	clusterer.fit(vektoren)
	labels: np.ndarray = clusterer.labels_

	n_cluster  = len(set(labels)) - (1 if -1 in labels else 0)
	n_rauschen = int(np.sum(labels == -1))

	log.info(
		"Clustering abgeschlossen: %d Cluster, %d Artikel ohne Zuordnung",
		n_cluster,
		n_rauschen,
	)

	# Ergebnis aufbereiten und speichern
	ergebnis = baue_ergebnis(labels, index, args.min_cluster_size, args.metric)
	schreibe_json(ergebnis)
	schreibe_bericht(ergebnis)

	log.info("=== Fertig ===")

	# Kurze Zusammenfassung auf stdout
	print()
	print(f"  Cluster gefunden:  {n_cluster}")
	print(f"  Ohne Cluster:      {n_rauschen}")
	print(f"  JSON:   {RESULT_JSON}")
	print(f"  Bericht: {RESULT_TXT}")
	print()


if __name__ == "__main__":
	main()
