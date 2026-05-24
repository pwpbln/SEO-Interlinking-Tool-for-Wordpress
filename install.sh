#!/bin/bash
# install.sh – Abhängigkeiten für SEO Crawler installieren
#
# Reihenfolge ist zwingend:
#   1. numpy     – wird von fastembed und scikit-learn vorausgesetzt
#   2. fastembed – lädt ONNX-Modell beim ersten Lauf herunter (~90 MB)
#   3. Rest      – requirements.txt (numpy + fastembed sind dann bereits erfüllt)
#
# Aufruf:
#   bash install.sh
#   bash install.sh --upgrade   # alle Pakete auf neueste Version heben

set -euo pipefail

PYTHON=".venv/bin/python"

# Flags auswerten
UPGRADE_FLAG=""
if [[ "${1:-}" == "--upgrade" ]]; then
	UPGRADE_FLAG="--upgrade"
	echo "Modus: --upgrade (alle Pakete werden aktualisiert)"
fi

# Venv prüfen
if [[ ! -x "$PYTHON" ]]; then
	echo "FEHLER: $PYTHON nicht gefunden."
	echo "Venv anlegen mit:  python3 -m venv .venv"
	exit 1
fi

echo
echo "=== SEO Crawler – Installation ==="
echo "Python: $("$PYTHON" --version)"
echo

# 1. numpy zuerst – Basis für alle numerischen Pakete
echo "[1/3] numpy..."
"$PYTHON" -m pip install $UPGRADE_FLAG "numpy==2.2.1"

# 2. fastembed – ONNX-Runtime-basierte Embeddings, kein torch nötig
#    Ersetzt sentence-transformers (torch hat kein macOS Intel + Python 3.13 Binary)
echo
echo "[2/3] fastembed (ONNX Runtime, kein torch)..."
"$PYTHON" -m pip install $UPGRADE_FLAG "fastembed>=0.5.0"

# 3. Restliche Abhängigkeiten
echo
echo "[3/3] Restliche Abhängigkeiten (requirements.txt)..."
"$PYTHON" -m pip install $UPGRADE_FLAG -r requirements.txt

echo
echo "=== Installation abgeschlossen ==="
echo
echo "Hinweis: Das Embedding-Modell (~90 MB) wird beim ersten Lauf"
echo "von embeddings.py automatisch heruntergeladen."
echo
echo "Funktionstest:"
echo "  .venv/bin/python scripts/embeddings.py --help"
echo
