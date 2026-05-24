# SEO Crawler – Technische Dokumentation

Stand: Mai 2026  
Projekt: `~/Nextcloud/Web/SEO Crawler/`  
Python: 3.13 · venv: `.venv/`

---

## Inhaltsverzeichnis

1. [Projektzweck](#1-projektzweck)
2. [Verzeichnisstruktur](#2-verzeichnisstruktur)
3. [Modulübersicht und Datenfluss](#3-modulübersicht-und-datenfluss)
4. [Skripte im Detail](#4-skripte-im-detail)
5. [Datenformate](#5-datenformate)
6. [Installation](#6-installation)
7. [Abhängigkeiten](#7-abhängigkeiten)
8. [Bekannte Probleme und Lösungen](#8-bekannte-probleme-und-lösungen)
9. [Sicherheitsregeln](#9-sicherheitsregeln)

---

## 1. Projektzweck

Der SEO Crawler ist ein lokal laufendes Python-Toolset für die systematische
SEO-Optimierung der WordPress-Site `ihre-domain.de`. Es automatisiert den
Datenfluss von der Artikel-Extraktion bis zur geprüften Verlinkung:

- **Crawlen** aller Artikel via WordPress-Sitemap
- **Semantisches Embedding** und **Clustering** zur Themengruppierung
- **Batch-Vorbereitung** für die manuelle KI-Analyse in Claude Desktop
- **Import und Validierung** der KI-Vorschläge
- **Browser-basierte Freigabe** jedes einzelnen Verlinkungsvorschlags
- **Rückspielen** genehmigter Links via WordPress REST API

Jede Änderung an WordPress-Inhalten erfordert eine manuelle Freigabe.
Es gibt keinen direkten Datenbankzugriff.

---

## 2. Verzeichnisstruktur

```
SEO Crawler/
│
├── scripts/                    Alle ausführbaren Module
│   ├── crawler.py              Modul 1: Sitemap-Crawler
│   ├── embeddings.py           Modul 2: Vektor-Berechnung
│   ├── clustering.py           Modul 2b: Semantisches Clustering
│   ├── batch_vorbereitung.py   Modul 3: Batch-Dateien für Claude Desktop
│   ├── proposal_import.py      Modul 3b: Proposal-Validator und Watcher
│   ├── freigabe_server.py      Modul 5: Lokaler Freigabe-Server (Browser-UI)
│   ├── update_wordpress.py     Modul 6: WordPress-Rückspielen via REST API
│   └── speichere_proposals.py  Hilfsskript: Proposals von stdin speichern
│
├── data/
│   ├── parsed/                 JSON-Dateien je Artikel (Modul 1 → Modul 2)
│   └── embeddings/
│       ├── vectors.npz         NumPy-Array aller Embedding-Vektoren
│       └── index.json          Mapping Vektor-Index → Slug/URL/Titel
│
├── analysis/
│   ├── batches/                Markdown-Batch-Dateien für Claude Desktop
│   ├── clusters/
│   │   ├── cluster-ergebnis.json   Maschinenlesbares Clustering-Ergebnis
│   │   └── cluster-bericht.txt     Menschenlesbarer Cluster-Bericht
│   └── proposals/              KI-generierte Verlinkungsvorschläge (JSON)
│
├── output/
│   └── approved/               Freigegebene Proposals (Modul 5 → Modul 6)
│       └── done/               Bereits nach WordPress übertragene Proposals
│
├── logs/                       Logdateien aller Module
│
├── docs/
│   └── DOKUMENTATION.md        Diese Datei
│
├── seo-kontext.md              SEO-Briefing (Silos, Zielgruppe, Regeln)
├── env.local                   Credentials (nicht eingecheckt)
├── .env.example                Vorlage für env.local
├── requirements.txt            Python-Abhängigkeiten
├── install.sh                  Installationsskript (Reihenfolge erzwungen)
└── CLAUDE.md                   Projekt-Kontext für Claude Code Sitzungen
```

---

## 3. Modulübersicht und Datenfluss

```
WordPress-Site
     │
     │  Sitemap-Crawl (REST API + HTML)
     ▼
[Modul 1] crawler.py
     │  data/parsed/<slug>.json  (je Artikel: Titel, Markdown, Tags, Links …)
     ▼
[Modul 2] embeddings.py
     │  data/embeddings/vectors.npz
     │  data/embeddings/index.json
     ▼
[Modul 2b] clustering.py          ← optional, zur Themenübersicht
     │  analysis/clusters/cluster-ergebnis.json
     │  analysis/clusters/cluster-bericht.txt
     │
     ├──────────────────────────────────────────────┐
     ▼                                              │
[Modul 3] batch_vorbereitung.py                    │
     │  analysis/batches/batch-01.md … batch-21.md │
     │                                             │
     │  ← manuell: Batch in Claude Desktop einfügen │
     │  ← Claude Desktop erzeugt JSON-Proposals     │
     ▼                                              │
[Modul 3b] proposal_import.py                      │ index.json
     │  Validiert + repariert analysis/proposals/   │ (Verlinkungsziele)
     │  batch-XX-proposals.json                     │
     ▼                                              │
[Modul 5] freigabe_server.py  (localhost:8080)     │
     │  Zeigt jeden Vorschlag im Browser            │
     │  J → output/approved/<slug>-<ts>.json        │
     │  N → logs/abgelehnt.log                      │
     ▼
[Modul 6] update_wordpress.py
     │  --dry-run (Standard): zeigt geplante Änderungen
     │  --live: PATCH /wp-json/wp/v2/posts/{id}
     ▼
WordPress-Site (Artikel mit neuen internen Links)
     output/approved/done/  ← verarbeitete Proposals verschoben
```

**Sonderfall: Proposals manuell speichern**

Claude Desktop gibt keinen direkten Datei-Output. Das Hilfsskript
`speichere_proposals.py` nimmt JSON von stdin und speichert es korrekt:

```bash
pbpaste | .venv/bin/python scripts/speichere_proposals.py batch-05
```

---

## 4. Skripte im Detail

### Modul 1 – `crawler.py`

Liest die WordPress-Sitemap (`/post-sitemap.xml`, optional `/page-sitemap.xml`),
ruft jeden Artikel-URL auf und extrahiert Metadaten + Fließtext.

**Aufruf:**
```bash
.venv/bin/python scripts/crawler.py                   # alle Artikel, Skip wenn vorhanden
.venv/bin/python scripts/crawler.py --force           # alle neu crawlen
.venv/bin/python scripts/crawler.py --include-pages   # auch Seiten (nicht nur Posts)
.venv/bin/python scripts/crawler.py --url URL         # einzelne URL testen
```

**Ausgabe:** `data/parsed/<slug>.json` pro Artikel  
**Log:** `logs/crawler.log`  
**Credentials:** `WP_URL`, `WP_USER`, `WP_APP_PASSWORD` aus `env.local`

**Besonderheiten:**
- Kategorien und Tags werden per WordPress REST API aufgelöst
  (`/wp-json/wp/v2/categories`, `/wp-json/wp/v2/tags`) mit lokalem Cache
- Markdown-Konvertierung via `markdownify`; gerade Anführungszeichen `"`
  werden dabei sofort zu typografischen `„…"` normalisiert
- Pause von 1,5 s zwischen Requests (`REQUEST_DELAY`)
- Interne Links werden aus dem Hauptcontent extrahiert

---

### Modul 2 – `embeddings.py`

Berechnet für jeden geparsten Artikel einen 384-dimensionalen
Embedding-Vektor mit dem ONNX-Modell `paraphrase-multilingual-MiniLM-L12-v2`
(kein torch notwendig, läuft auf macOS Intel + Python 3.13).

**Aufruf:**
```bash
.venv/bin/python scripts/embeddings.py          # überspringt bekannte Slugs
.venv/bin/python scripts/embeddings.py --force  # alles neu berechnen
```

**Eingabe:** `data/parsed/*.json`  
**Ausgabe:** `data/embeddings/vectors.npz`, `data/embeddings/index.json`  
**Log:** `logs/embeddings.log`

**Modell:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- 384 Dimensionen, Deutsch + Englisch
- ~220 MB ONNX-Modell, wird beim ersten Lauf automatisch heruntergeladen
- Kein torch, kein CUDA – reine ONNX Runtime via `fastembed`

**Input-Text:** Titel + erste 500 Wörter des Markdown-Fließtexts

---

### Modul 2b – `clustering.py`

Gruppiert die Artikel-Vektoren mit HDBSCAN (Hierarchical Density-Based
Spatial Clustering) zu semantischen Themen-Clustern.

**Aufruf:**
```bash
.venv/bin/python scripts/clustering.py
.venv/bin/python scripts/clustering.py --min-cluster-size 3
.venv/bin/python scripts/clustering.py --metric euclidean
```

| Flag | Standard | Bedeutung |
|---|---|---|
| `--min-cluster-size N` | `5` | Minimale Artikelzahl pro Cluster |
| `--metric` | `cosine` | Distanzmetrik (`cosine` oder `euclidean`) |

**Ausgabe:**
- `analysis/clusters/cluster-ergebnis.json` – maschinenlesbar
- `analysis/clusters/cluster-bericht.txt` – menschenlesbare Übersicht  
**Log:** `logs/clustering.log`

---

### Modul 3 – `batch_vorbereitung.py`

Erzeugt Markdown-Batch-Dateien, die in Claude Desktop eingefügt werden.
Jede Datei enthält das SEO-Briefing, Artikelinhalte und das erwartete
JSON-Ausgabeformat für Verlinkungsvorschläge.

**Aufruf:**
```bash
.venv/bin/python scripts/batch_vorbereitung.py
.venv/bin/python scripts/batch_vorbereitung.py --batch-size 10
.venv/bin/python scripts/batch_vorbereitung.py --force
```

| Flag | Standard | Bedeutung |
|---|---|---|
| `--batch-size N` | `5` | Artikel pro Batch-Datei |
| `--force` | – | Vorhandene Dateien überschreiben; löscht veraltete Batch-Nummern |

**Ausgabe:** `analysis/batches/batch-01.md` … `batch-NN.md`  
**Log:** `logs/batch_vorbereitung.log`

**Verarbeitung pro Artikel:**
1. `bereinige_markdown()` – entfernt URLs, Fettschrift-, Kursivmarker,
   Heading-Zeichen; normalisiert Anführungszeichen zu `„…"`
2. `kuerze_auf_satz()` – kürzt auf letzten vollständigen Satz vor 500 Wörtern
3. Batch-Statistik-Block am Kopf jeder Datei

**Erwartetes Ausgabeformat (JSON-Array von Claude Desktop):**
```json
[
  {
    "slug": "artikel-slug",
    "silo": "Theater und Bühne Berlin",
    "neue_schlagwoerter": ["Tag1", "Tag2"],
    "link_vorschlaege": [
      {
        "kontext_satz": "Vollständiger Satz, der den Ankertext enthält.",
        "ankertext": "Ankertext",
        "ziel_url": "https://ihre-domain.de/blog/ziel-artikel/",
        "begruendung": "Ein Satz Begründung."
      }
    ],
    "cornerstone": false
  }
]
```

---

### Modul 3b – `proposal_import.py`

Validiert Proposal-JSON-Dateien und überwacht den `analysis/proposals/`-Ordner
per Watchdog auf neue oder geänderte Dateien.

**Aufruf:**
```bash
.venv/bin/python scripts/proposal_import.py             # Watcher-Modus
.venv/bin/python scripts/proposal_import.py --validate  # alle Dateien prüfen
```

**Eingabe:** `analysis/proposals/batch-XX-proposals.json`  
**Log:** `logs/proposal_import.log`

**Validierungsschritte:**
1. JSON syntaktisch korrekt (mit Auto-Repair, s. u.)
2. Root-Element ist ein Array
3. Pflichtfelder und Typen je Artikel-Eintrag
4. Silo-Name einer der vier bekannten SEO-Silos
5. Pflichtfelder je Link-Vorschlag
6. Keine Duplikat-Slugs innerhalb einer Datei
7. Keine Duplikat-Slugs über alle Proposal-Dateien hinweg

**Auto-Repair für JSON-Anführungszeichen (zweistufig):**

*Stufe 1 – State-Machine-Normalisierer* (läuft immer vor dem Parsen):
Traversiert den JSON-Text zeichenweise und escapet ungescapte `"` innerhalb
von Stringwerten. Heuristik: Ein `"` gilt als strukturelles String-Ende,
wenn das nächste Nicht-Whitespace-Zeichen `:`, `,`, `]` oder `}` ist.

*Stufe 2 – Iterativer Fallback* (nur bei verbleibendem Parse-Fehler):
Sucht rückwärts vom Fehler-Offset das erste nicht-gescapte `"` und escapet
es; wiederholt bis die Datei valide ist oder kein Kandidat mehr gefunden wird.

Bei erfolgreicher Reparatur wird die Datei mit dem korrigierten Inhalt
überschrieben und die Korrektur ins Log geschrieben.

---

### Hilfsskript – `speichere_proposals.py`

Speichert JSON-Output von Claude Desktop nach `analysis/proposals/`.
Versteht sowohl ein fertiges JSON-Array als auch mehrere
` ```json ``` `-Codeblöcke, die automatisch zu einem Array zusammengeführt werden.

**Aufruf:**
```bash
pbpaste | .venv/bin/python scripts/speichere_proposals.py batch-05
pbpaste | .venv/bin/python scripts/speichere_proposals.py batch-05 --force
```

---

### Modul 5 – `freigabe_server.py`

Lokaler HTTP-Server (kein externes Framework). Zeigt jeden
Verlinkungsvorschlag einzeln im Browser und wartet auf Freigabe.

**Aufruf:**
```bash
.venv/bin/python scripts/freigabe_server.py
.venv/bin/python scripts/freigabe_server.py --port 9090
.venv/bin/python scripts/freigabe_server.py --kein-browser
```

| Flag | Standard | Bedeutung |
|---|---|---|
| `--port PORT` | `8080` | Lokaler HTTP-Port |
| `--kein-browser` | – | Browser nicht automatisch öffnen |

**Bedienung:**
- `J` oder Klick „Annehmen" → speichert nach `output/approved/<slug>-<ts>.json`
- `N` oder Klick „Ablehnen" → schreibt in `logs/abgelehnt.log`
- PRG-Pattern (Post/Redirect/Get) verhindert Doppel-Submit bei Browser-Zurück

**Eingabe:** alle `analysis/proposals/batch-*-proposals.json`

---

### Modul 6 – `update_wordpress.py`

Liest freigegebene Proposals aus `output/approved/` und trägt die Links
via WordPress REST API in die Artikel-HTML-Inhalte ein.

**Aufruf:**
```bash
.venv/bin/python scripts/update_wordpress.py              # Trockenlauf (Standard)
.venv/bin/python scripts/update_wordpress.py --dry-run    # Trockenlauf (explizit)
.venv/bin/python scripts/update_wordpress.py --live       # schreibt nach WordPress
.venv/bin/python scripts/update_wordpress.py --live --slug mein-artikel
```

**Sicherheitsregel:** `--dry-run` ist der Default. WordPress wird
**ausschließlich mit explizitem `--live`** beschrieben.

**Status-Codes je Proposal:**

| Code | Bedeutung |
|---|---|
| `ok` | Link erfolgreich gesetzt |
| `dry_run` | Trockenlauf – würde gesetzt werden |
| `nicht_gefunden` | Kontext-Satz nicht im Artikel gefunden |
| `mehrdeutig` | Kontext-Satz kommt mehrfach vor |
| `bereits_vorhanden` | Ziel-URL bereits im Artikel verlinkt |
| `ankertext_fehlt` | Ankertext nicht im Kontext-Satz |
| `ersetzung_fehlgeschlagen` | Regex-Ersetzung schlug fehl |
| `api_fehler` | WordPress REST API nicht erreichbar / Fehler |

**Verarbeitungsablauf:**
1. `output/approved/*.json` nach `quell_slug` gruppieren
2. Pro Slug: HTML einmalig per `GET /wp/v2/posts?slug=...` laden
3. Proposals sequenziell mit BeautifulSoup + Regex auf HTML anwenden
4. Im `--live`-Modus: `PATCH /wp/v2/posts/{id}` einmalig pro Artikel
5. Verarbeitete Dateien nach `output/approved/done/` verschieben

---

## 5. Datenformate

### `data/parsed/<slug>.json`

```json
{
  "url": "https://ihre-domain.de/blog/artikel-slug/",
  "slug": "artikel-slug",
  "title": "Artikeltitel",
  "published": "2024-03-15T10:00:00+00:00",
  "categories": ["Theater, Theaterkritik", "Texte"],
  "tags": ["Berliner Ensemble", "Brecht"],
  "internal_links": ["https://ihre-domain.de/blog/anderer-artikel/"],
  "markdown": "Fließtext des Artikels als Markdown …",
  "crawled_at": "2026-05-19T17:00:00+00:00"
}
```

### `data/embeddings/index.json`

```json
{
  "0": {"slug": "artikel-slug", "url": "https://…", "title": "Titel"},
  "1": {"slug": "anderer-slug", "url": "https://…", "title": "Anderer Titel"}
}
```

### `analysis/proposals/batch-XX-proposals.json`

```json
[
  {
    "slug": "quell-artikel-slug",
    "silo": "Theater und Bühne Berlin",
    "neue_schlagwoerter": ["Schlagwort 1", "Schlagwort 2"],
    "link_vorschlaege": [
      {
        "kontext_satz": "Vollständiger Satz mit dem Ankertext darin.",
        "ankertext": "Ankertext",
        "ziel_url": "https://ihre-domain.de/blog/ziel/",
        "begruendung": "Ein Satz."
      }
    ],
    "cornerstone": false
  }
]
```

### `output/approved/<quell_slug>-<timestamp>.json`

Einzelner Link-Vorschlag; enthält zusätzlich `quell_slug` und `quell_url`
für die spätere Zuordnung in Modul 6.

---

## 6. Installation

### Voraussetzungen

- macOS (Intel oder Apple Silicon) oder Linux
- Python 3.13
- Angelegtes Virtual Environment: `python3 -m venv .venv`

### Installationsschritte

```bash
# 1. Ins Projektverzeichnis wechseln
cd ~/Nextcloud/Web/SEO\ Crawler/

# 2. Virtual Environment anlegen (einmalig)
python3 -m venv .venv

# 3. Abhängigkeiten installieren (Reihenfolge ist zwingend)
bash install.sh

# 4. Credentials anlegen
cp .env.example env.local
# env.local mit Editor öffnen und WP_URL, WP_USER, WP_APP_PASSWORD eintragen
```

### Warum `install.sh` statt `pip install -r requirements.txt` direkt?

`fastembed` setzt `numpy` voraus. Bei direkter Installation über
`requirements.txt` kann es zu Import-Fehlern kommen, weil numpy noch
nicht initialisiert ist, wenn fastembed versucht es zu importieren.
`install.sh` erzwingt die Reihenfolge: numpy → fastembed → Rest.

```bash
# install.sh –Ablauf
[1/3] numpy==2.2.1            # Basis für alle numerischen Pakete
[2/3] fastembed>=0.5.0        # ONNX Runtime, kein torch
[3/3] requirements.txt        # restliche Pakete
```

### Upgrade aller Pakete

```bash
bash install.sh --upgrade
```

---

## 7. Abhängigkeiten

| Paket | Version | Zweck |
|---|---|---|
| `requests` | 2.32.3 | HTTP-Requests (Crawler, WordPress API) |
| `beautifulsoup4` | 4.12.3 | HTML-Parsing und -Manipulation |
| `markdownify` | 0.13.1 | HTML → Markdown-Konvertierung |
| `lxml` | 5.3.0 | HTML-Parser für BeautifulSoup |
| `python-dotenv` | 1.0.1 | `env.local` laden |
| `numpy` | 2.2.1 | Vektor-Operationen, npz-Dateien |
| `fastembed` | ≥ 0.5.0 | ONNX-basierte Embeddings (kein torch) |
| `scikit-learn` | ≥ 1.3.0 | HDBSCAN-Clustering |
| `watchdog` | ≥ 4.0.0 | Filesystem-Überwachung (Proposal-Watcher) |

**Embedding-Modell** (wird beim ersten `embeddings.py`-Lauf heruntergeladen):  
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (~220 MB)

---

## 8. Bekannte Probleme und Lösungen

### torch / sentence-transformers nicht installierbar auf macOS Intel + Python 3.13

**Symptom:**
```
ERROR: Could not find a version that satisfies the requirement torch
```

**Ursache:** PyTorch liefert kein Binary für die Kombination
macOS Intel + Python 3.13.

**Lösung:** `sentence-transformers` ist durch `fastembed` ersetzt.
fastembed nutzt ONNX Runtime und benötigt kein torch.
Das Modell `paraphrase-multilingual-MiniLM-L12-v2` ist qualitativ
gleichwertig und läuft auf allen Plattformen.

---

### fastembed-Modell `intfloat/multilingual-e5-small` nicht gefunden

**Symptom:**
```
ValueError: Model intfloat/multilingual-e5-small is not supported in TextEmbedding.
```

**Ursache:** fastembed ≥ 0.5.0 unterstützt dieses Modell nicht mehr.

**Lösung:** Modell in `scripts/embeddings.py` auf
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` geändert
(gleiche Dimensionalität: 384, ebenfalls multilingual).

---

### fastembed-Installation schlägt mit Python 3.13 fehl

**Symptom:**
```
ERROR: Could not find a version that satisfies the requirement fastembed==0.4.1
```

**Ursache:** fastembed < 0.5.0 setzt Python `< 3.13` voraus.

**Lösung:** `install.sh` und `requirements.txt` verwenden `fastembed>=0.5.0`.
Die erste kompatible Version ist 0.5.0; aktuell wird 0.8.0 installiert.

---

### Ungescapte Anführungszeichen in Proposal-JSON

**Symptom:**
```
JSONDecodeError: Expecting ',' delimiter: line 22 column 63
```

**Ursache:** Claude Desktop schreibt in `kontext_satz`-Feldern Werktitel
wie `„Was ihr wollt"` mit typografischem öffnendem `„`, aber geradem
schließendem `"` (U+0022). Das bricht den JSON-Parser.

**Dreistufige Lösung:**

1. **Quelle bereinigen – `crawler.py`:** Das Markdown-Feld wird beim Crawlen
   sofort normalisiert (`"…"` → `„…"`). Neue Crawl-Läufe erzeugen saubere Daten.

2. **Quelle bereinigen – `batch_vorbereitung.py`:** `bereinige_markdown()`
   normalisiert Anführungszeichen als letzten Schritt. Claude Desktop sieht
   nur noch typografische Zeichen und gibt sie auch so zurück.

3. **Fallback – `proposal_import.py`:** State-Machine-Normalisierer escapet
   ungescapte `"` in JSON-Stringwerten vor jedem Parsen. Iterativer
   Auto-Repair als zweite Fallback-Stufe. Korrekturen werden geloggt und
   die Datei wird überschrieben.

---

### numpy-Importfehler bei direkter pip-Installation

**Symptom:**
```
ImportError: numpy.core.multiarray failed to import
```

**Ursache:** fastembed wird installiert, bevor numpy vollständig
initialisiert ist.

**Lösung:** Immer `bash install.sh` statt `pip install -r requirements.txt`
direkt verwenden.

---

## 9. Sicherheitsregeln

| Regel | Details |
|---|---|
| **Credentials ausschließlich in `env.local`** | Nie in Code, nie in Git. `.env.example` zeigt nur die Schlüsselnamen. |
| **`--dry-run` ist der Default** | `update_wordpress.py` schreibt nie ohne explizites `--live`. |
| **Kein direkter Datenbankzugriff** | Alle WordPress-Änderungen laufen über die REST API mit Application Password. |
| **Freigabepflicht** | Jeder Verlinkungsvorschlag durchläuft `freigabe_server.py` (manuell J/N). |
| **Kein system-python** | Immer `.venv/bin/python` verwenden, nie `python3` aus dem System-PATH. |
| **env.local nicht einchecken** | `.gitignore` muss `env.local` enthalten. |
