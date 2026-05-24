# SEO Crawler – Projekt-Kontext für Claude Code

Der SEO Crawler ist ein lokal laufendes Python-Toolset, das die
interne Verlinkung einer WordPress-Site systematisch
optimiert – vom automatischen Crawlen der Artikel bis zum geprüften
Rückspielen von KI-generierten Verlinkungsvorschlägen via REST API.

---

## Projektpfad

```
~/Nextcloud_pehl/Web/SEO Crawler/
```

---

## Technischer Stack

| Komponente | Detail |
|---|---|
| Python | 3.13 (`.venv/bin/python` – immer, nie system-`python3`) |
| Embeddings | `fastembed` ≥ 0.5.0 · ONNX Runtime · kein torch |
| Modell | `paraphrase-multilingual-MiniLM-L12-v2` · 384 dim |
| Clustering | `scikit-learn` HDBSCAN · cosine metric |
| Scraping | `requests` + `beautifulsoup4` + `markdownify` |
| Filesystem-Watch | `watchdog` |
| WordPress | REST API + Application Password · kein DB-Zugriff |
| Credentials | `env.local` (nicht eingecheckt) |
| Code-Stil | Tabs · Deutsch · UTF-8 · `ensure_ascii=False` |

---

## Python-Regel

**Immer** `.venv/bin/python` verwenden:

```bash
.venv/bin/python scripts/crawler.py
.venv/bin/python scripts/embeddings.py --force
```

Niemals `python3` oder `python` aus dem System-PATH – das falsche
Interpreter-Umgebung führt zu Import-Fehlern bei fastembed/numpy.

---

## Rollenkonvention

**Entwickler-Sitzungen** (Claude Code, dieses Tool):
- Infrastruktur bauen und reparieren
- Skripte schreiben, debuggen, testen
- Batch-Dateien neu generieren
- Logs auswerten

**Linguisten-Sitzungen** (Claude Desktop App):
- Batch-Markdown-Dateien aus `analysis/batches/` einlesen
- Verlinkungsvorschläge als JSON-Array ausgeben
- Ausgabe via `speichere_proposals.py` oder manuell nach
  `analysis/proposals/batch-XX-proposals.json` speichern

Die beiden Rollen mischen sich nicht: Claude Code schreibt keinen
Fließtext-Analyse-Output, Claude Desktop kennt keine Skript-Infrastruktur.

---

## Aktueller Stand

### Fertige Module

| Modul | Skript | Status |
|---|---|---|
| 1 – Crawler | `crawler.py` | ✓ fertig · 101 Artikel gecrawlt |
| 2 – Embeddings | `embeddings.py` | ✓ fertig · 101 Vektoren berechnet |
| 2b – Clustering | `clustering.py` | ✓ fertig |
| 3 – Batch-Vorbereitung | `batch_vorbereitung.py` | ✓ fertig · 21 Batches erzeugt |
| 3b – Proposal-Validator | `proposal_import.py` | ✓ fertig · inkl. Auto-Repair |
| 5 – Freigabe-Server | `freigabe_server.py` | ✓ fertig |
| 6 – WordPress-Sync | `update_wordpress.py` | ✓ fertig |

### Batch-Fortschritt

<!-- STATUS:START -->
*Aktualisieren: `.venv/bin/python scripts/status.py --update-claude`*

| Stufe | Stand | Fortschritt |
|---|---|---|
| Batches erstellt | 0 | – |
| Proposals valide | 0 / 0 Batches | `░░░░░░░░░░░░░░░` 0 % |
| Freigegeben | 0 / 0 | `░░░░░░░░░░░░░░░` 0 % |
| WordPress eingespielt | 0 / 0 | `░░░░░░░░░░░░░░░` 0 % |

**Gesamtfortschritt:** `░░░░░░░░░░░░░░░░░░░░` **0 %**
*(Gewichtung: Analyse 50 % · Freigabe 30 % · WordPress 20 %)*
<!-- STATUS:END -->

### Offene Aufgaben

- [ ] batch-05 bis batch-21 in Claude Desktop analysieren und Proposals speichern
- [ ] `proposal_import.py --validate` nach jedem neuen Batch ausführen
- [ ] `freigabe_server.py` starten und alle validierten Proposals durchgehen
- [ ] `update_wordpress.py --dry-run` prüfen, dann mit `--live` rückspielen
- [ ] Taxonomie-Duplikate in WordPress bereinigen (siehe `seo-kontext.md`)
- [ ] Silo 3 (Informatik) durch neue Artikel aufbauen

---

## Sicherheitsregeln

1. **`--dry-run` immer zuerst** – `update_wordpress.py` schreibt nur mit
   explizitem `--live`. Niemals `--live` ohne vorherigen Trockenlauf.

2. **Kein direkter Datenbankzugriff** – alle WordPress-Änderungen
   ausschließlich via REST API.

3. **Credentials nur in `env.local`** – nie in Code, nie in
   Commit-Messages, nie in Logs. Datei ist nicht eingecheckt.

4. **Freigabepflicht** – jeder Verlinkungsvorschlag wird einzeln im
   Browser freigegeben (`freigabe_server.py`). Kein Batch-Approve.

5. **env.local niemals committen** – vor jedem `git add` prüfen.

---

## Wichtige Dateipfade

```
env.local                               Credentials (WP_URL, WP_USER, WP_APP_PASSWORD)
seo-kontext.md                          SEO-Briefing: Silos, Zielgruppe, Verlinkungsregeln
data/parsed/                            Gecrawlte Artikel als JSON
data/embeddings/vectors.npz             Embedding-Vektoren (NumPy)
data/embeddings/index.json              Vektor-Index → Slug/URL/Titel
analysis/batches/batch-01.md … 21.md    Batch-Dateien für Claude Desktop
analysis/proposals/batch-XX-proposals.json  KI-Vorschläge (valide JSON-Arrays)
output/approved/                        Freigegebene Proposals (→ Modul 6)
output/approved/done/                   Bereits nach WordPress übertragen
logs/                                   Logdateien aller Module
docs/DOKUMENTATION.md                   Vollständige technische Dokumentation
```

---

## Schnellreferenz – häufige Befehle

```bash
# Installation (einmalig oder nach Paket-Änderungen)
bash install.sh

# Artikel neu crawlen
.venv/bin/python scripts/crawler.py --force

# Embeddings neu berechnen
.venv/bin/python scripts/embeddings.py --force

# Clustering aktualisieren
.venv/bin/python scripts/clustering.py

# Batch-Dateien neu erzeugen
.venv/bin/python scripts/batch_vorbereitung.py --force

# Proposals von Claude Desktop speichern
pbpaste | .venv/bin/python scripts/speichere_proposals.py batch-05

# Alle Proposals validieren
.venv/bin/python scripts/proposal_import.py --validate

# Proposal-Watcher starten (bei aktiver Claude Desktop Sitzung)
.venv/bin/python scripts/proposal_import.py

# Freigabe-Server starten
.venv/bin/python scripts/freigabe_server.py

# Trockenlauf WordPress-Sync
.venv/bin/python scripts/update_wordpress.py

# Live-Sync nach WordPress (erst nach Trockenlauf!)
.venv/bin/python scripts/update_wordpress.py --live

# Linguisten-Prompt kompilieren (nach crawler.py- oder pool_anwenden.py-Lauf)
.venv/bin/python scripts/prompt_kompilieren.py                        # Modus A: Claude Desktop
.venv/bin/python scripts/prompt_kompilieren.py --modus andere-modelle # Modus B: andere Modelle
```
