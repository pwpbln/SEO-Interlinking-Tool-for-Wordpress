# SEO Crawler – Bedienungsanleitung
**Stand: Mai 2026**

---

## Was dieses Tool tut

Der SEO Crawler ist ein redaktionelles Werkzeug für WordPress-Betreiber. Es liest Ihre
gesamte Website aus, analysiert die Inhalte mit Hilfe eines KI-Sprachmodells und
schlägt Ihnen vor: welche Artikel intern miteinander verlinkt werden sollten, welche
Schlagwörter sinnvoller wären und welchen SEO-Themenschwerpunkten Ihre Artikel
zugeordnet werden sollten.

Keine Änderung geschieht automatisch. Sie entscheiden jeden Vorschlag einzeln.
Erst nach Ihrer Freigabe werden Änderungen in WordPress eingespielt.

---

## Voraussetzungen

- macOS (Intel oder Apple Silicon) mit Python 3.13
- WordPress mit aktivierter REST API (Standard bei allen modernen Installationen)
- Claude Pro oder höher für die Analyse-Phase
- Claude Desktop App mit aktiviertem Filesystem-MCP-Server

---

## Schritt 1 – WordPress Application Password einrichten

Das Tool greift nie direkt auf die Datenbank zu. Es kommuniziert ausschließlich über
die offizielle WordPress REST API. Dafür brauchen Sie ein sogenanntes
Application Password.

So erzeugen Sie es:

1. Melden Sie sich in Ihrem WordPress-Backend an.
2. Gehen Sie zu **Benutzer → Profil**.
3. Scrollen Sie ganz nach unten zum Abschnitt **Anwendungspasswörter**.
4. Tragen Sie als Namen „SEO Crawler" ein und klicken Sie auf
   **Neues Anwendungspasswort hinzufügen**.
5. WordPress zeigt Ihnen einmalig das Passwort an – kopieren Sie es sofort.
   Es hat die Form: `xxxx xxxx xxxx xxxx xxxx xxxx`

Tragen Sie die Credentials in die Datei `env.local` im Projektverzeichnis ein:

```
WP_URL=https://ihre-domain.de
WP_USER=ihr-wordpress-benutzername
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

**Sicherheitshinweis:** Die `env.local`-Datei ist in `.gitignore` eingetragen
und verlässt Ihren Computer nicht.

---

## Schritt 2 – Installation

Öffnen Sie das Terminal, wechseln Sie ins Projektverzeichnis und führen Sie aus:

```bash
cd ~/Nextcloud/Web/SEO\ Crawler
bash install.sh
```

Das installiert alle Abhängigkeiten in die lokale virtuelle Umgebung.
Dauert beim ersten Mal einige Minuten.

**Wichtig:** Alle folgenden Befehle immer mit `.venv/bin/python` starten,
nicht mit dem System-Python. Das ist der Unterschied zwischen dem richtigen
und dem falschen Python auf macOS.

---

## Schritt 3 – Taxonomie aus WordPress abrufen

Dieser Schritt holt Ihre aktuellen Schlagwörter und Kategorien direkt aus WordPress:

```bash
.venv/bin/python scripts/taxonomie_export.py
```

Ergebnis: `data/taxonomie/schlagwoerter-export.json` und
`data/taxonomie/kategorien-export.json`

---

## Schritt 4 – Website crawlen

```bash
.venv/bin/python scripts/crawler.py
```

Der Crawler liest Ihre Sitemap, ruft jeden Artikel ab und speichert ihn als
strukturierte JSON-Datei unter `data/parsed/`. Zwischen jedem Artikel wartet
er 1,5 Sekunden – das ist robots.txt-konform und belastet Ihren Server nicht.

Bei einem erneuten Durchlauf werden bereits vorhandene Artikel übersprungen.
Mit `--force` werden alle neu gecrawlt.

Fortschritt und Fehler werden in `logs/crawler.log` protokolliert.

---

## Schritt 5 – Semantische Vektoren berechnen (optional, empfohlen)

```bash
.venv/bin/python scripts/embeddings.py
```

Das berechnet für jeden Artikel einen mathematischen Fingerabdruck seiner
Bedeutung. Läuft vollständig lokal auf Ihrem Mac, dauert einige Minuten,
verbraucht keine KI-Tokens. Das Ergebnis liegt unter `data/embeddings/`.

Optional – thematische Cluster berechnen:

```bash
.venv/bin/python scripts/clustering.py
```

Zeigt, welche Artikel thematisch zusammengehören. Hilfreiche Orientierung,
kein Pflichtschritt.

---

## Schritt 6 – Batches für die Analyse vorbereiten

```bash
.venv/bin/python scripts/batch_vorbereitung.py
```

Teilt Ihre Artikel in handhabbare Gruppen von fünf auf und legt sie als
lesbare Markdown-Dateien unter `analysis/batches/` ab. Jede Datei enthält
bereits Ihre SEO-Ziele aus der `seo-kontext.md` als Kontext für das
Sprachmodell.

---

## Schritt 7 – Schlagwort-Pool konsolidieren

```bash
.venv/bin/python scripts/schlagwort_konsolidierung.py
```

Bereinigt bestehende Schlagwörter um exakte und Slug-Duplikate.
Sprachliche Grenzfälle werden für die Linguisten-Phase vorbereitet.

---

## Schritt 8 – Die Linguisten-Phase

Das ist der inhaltliche Kern des Projekts. Hier kommt ein Sprachmodell
zum Einsatz – kein Skript, sondern ein geführtes Gespräch.

### Welches Modell ist geeignet?

Stand Mai 2026 sind für deutschsprachige Textanalyse drei Modelle
besonders geeignet:

**Claude Sonnet 4.6 / Opus 4.7** (Anthropic) – beste Wahl für deutschen
Fließtext. Versteht Nuancen, feuilletonistischen Stil und semantische
Zusammenhänge in deutschen Texten zuverlässig. Für die Batch-Analyse
reicht Sonnet; für Grenzfall-Entscheidungen bei Schlagwörtern ist
Opus präziser.

**Gemini 2.5 Pro** (Google) – gute Alternative, besonders kosteneffizient
bei großen Mengen. Deutsches Sprachverständnis gut, aber etwas weniger
nuanciert als Claude bei feuilletonistischen Texten.

**GPT-5** (OpenAI) – stark bei faktenintensiven Texten und Recherche,
etwas schwächer bei stilistisch anspruchsvollen deutschen Texten.

Für dieses Projekt empfehlen wir Claude in der Desktop App – sie ist
bereits für den Dateizugriff eingerichtet.

### Wie aufwändig ist die Linguisten-Phase?

Bei 101 Artikeln in 21 Batches à fünf Artikel rechnen Sie mit:

- Ca. 8 Minuten pro Batch-Analyse
- 21 Batches = ca. 3 Stunden Gesamt-Laufzeit
- Ihr Zeitaufwand: Batch starten, Ergebnis prüfen, nächsten starten
- Tatsächliche Arbeitszeit für Sie: ca. 30 Minuten

### Ablauf der Linguisten-Phase

Öffnen Sie die Claude Desktop App. Der Filesystem-MCP-Server muss
verbunden sein (erkennbar am Tool-Symbol im Eingabefeld).

Geben Sie für jeden Batch folgenden Prompt ein – ändern Sie nur die Zahl:

```
Du bist Linguist mit Schwerpunkt deutschsprachige Medien
und SEO-Semantik. Wirtschaftlich mit Tokens: Keine
Erklärungen, keine Prosa, kein Kommentar.

Lies analysis/batches/batch-01.md und seo-kontext.md.

Erstelle das JSON-Array nach dem bekannten Schema und
speichere das Ergebnis als:
analysis/proposals/batch-01-proposals.json

Gib danach nur eine einzeilige Bestätigung aus:
batch-01-proposals.json gespeichert – X Artikel, Y Vorschläge
```

Das Modell liest die Dateien selbst, analysiert und speichert das Ergebnis
direkt ins Projektverzeichnis. Sie müssen nichts kopieren.

Validieren Sie nach jedem Batch:

```bash
.venv/bin/python scripts/proposal_import.py --validate
```

Aktuellen Fortschritt prüfen:

```bash
.venv/bin/python scripts/status.py
```

### Schlagwort-Grenzfälle durch den Linguisten entscheiden

Nach der Batch-Analyse gibt es eine Datei `data/taxonomie/grenzfaelle.json`
mit sprachlichen Zweifelsfällen – Paare wie „Beobachtung / Betrachtung"
oder „Freizeit / Freiheit". Der Linguist entscheidet, welcher Begriff
bleibt. Auch das geschieht per Prompt in der Desktop App.

---

## Schritt 9 – Freigabe im Browser

Starten Sie den lokalen Freigabe-Server:

```bash
# Interne Verlinkungen freigeben
.venv/bin/python scripts/freigabe_server.py --modus links

# Schlagwörter freigeben
.venv/bin/python scripts/freigabe_server.py --modus tags

# Kategorien freigeben
.venv/bin/python scripts/freigabe_server.py --modus kategorien
```

Der Browser öffnet sich automatisch auf `http://localhost:8080`. Sie sehen
jeden Vorschlag einzeln: den betroffenen Artikel, den Kontext-Satz, den
vorgeschlagenen Ankertext und die Begründung des Sprachmodells.

Tastenkürzel: `J` für annehmen, `N` für ablehnen.

Jede Entscheidung wird sofort gespeichert. Sie können den Server jederzeit
beenden und später weitermachen – kein Datenverlust.

Freigegebene Vorschläge landen in `output/approved/`.

---

## Schritt 10 – Rückspielen in WordPress

Führen Sie immer zuerst einen Trockenlauf durch – er schreibt nichts,
zeigt aber genau was passieren würde:

```bash
.venv/bin/python scripts/update_wordpress.py --modus links --dry-run
.venv/bin/python scripts/update_wordpress.py --modus tags --dry-run
.venv/bin/python scripts/update_wordpress.py --modus kategorien --dry-run
```

Prüfen Sie die Ausgabe. Wenn alles plausibel aussieht:

```bash
.venv/bin/python scripts/update_wordpress.py --modus links --live
.venv/bin/python scripts/update_wordpress.py --modus tags --live
.venv/bin/python scripts/update_wordpress.py --modus kategorien --live
```

### Was das Tool dabei tut

**Interne Links (`--modus links`):**
Das Skript sucht den genauen Kontext-Satz im Artikel-HTML, prüft ob
der Ankertext darin vorkommt und ob an dieser Stelle noch kein Link
gesetzt ist. Nur dann wird der HTML-Link eingefügt. Der geänderte
Artikel-Content wird via `PATCH /wp/v2/posts/{id}` zurückgespielt.

**Schlagwörter (`--modus tags`):**
Neue Schlagwörter werden zuerst über `/wp/v2/tags` in WordPress angelegt
(falls noch nicht vorhanden), dann dem Artikel zugewiesen. Bestehende
Schlagwörter bleiben erhalten – das Tool ergänzt, es löscht nicht.

Schlagwörter löschen ist ein gesonderter, bewusster Schritt. Das
Skript unterstützt `--aufraumen` als explizites Flag, das Schlagwörter
entfernt die keinem Artikel mehr zugeordnet sind. Das ist optional
und sollte mit Bedacht eingesetzt werden – eine manuelle Kontrolle
in WordPress vorher ist empfehlenswert.

**Kategorien (`--modus kategorien`):**
Das Skript ordnet den Artikel der vorgeschlagenen Kategorie zu und
setzt bei Cornerstone-Artikeln zusätzlich das Yoast-SEO-Merkmal
`_yoast_wpseo_is_cornerstone`.

Jeder Vorgang wird in `logs/updates.log` protokolliert. Verarbeitete
Dateien wandern nach `output/approved/done/` – sie bleiben erhalten,
werden aber nicht ein zweites Mal verarbeitet.

---

## Wie die internen Verlinkungen entstehen

Der Prozess läuft in drei Stufen:

**Stufe 1 – Mathematische Vorbereitung (lokal):**
Die Embeddings berechnen für jeden Artikel einen Vektor. Artikel mit
ähnlichem Thema haben ähnliche Vektoren. Das Clustering-Skript erkennt
daraus thematische Gruppen – diese Information fließt in die Batch-Dateien
als Orientierung ein.

**Stufe 2 – Sprachliche Analyse (Linguist):**
Das Sprachmodell liest den Fließtext und erkennt Stellen, an denen ein
Link inhaltlich sinnvoll wäre. Es benennt den genauen Satz, den
vorgeschlagenen Ankertext und den Zielartikel mit Begründung.
Entscheidend ist dabei das Sprachverständnis: Der Link muss natürlich
im Satzfluss stehen und der Zielartikel muss wirklich passen.

**Stufe 3 – Mechanische Umsetzung (Skript):**
Das Update-Skript sucht den Kontext-Satz im HTML des Artikels mit
BeautifulSoup, prüft ob der Ankertext dort vorkommt, und ersetzt
`Ankertext` durch `<a href="ziel-url">Ankertext</a>`. Nur bei
eindeutigem Treffer – bei Mehrdeutigkeit oder fehlendem Satz wird
ein Warnhinweis ins Log geschrieben und nichts geändert.

---

## Aktuellen Stand prüfen

```bash
.venv/bin/python scripts/status.py
```

Gibt eine Übersicht: wie viele Batches analysiert, wie viele Vorschläge
freigegeben, wie viele in WordPress eingespielt. Fortschritt in Prozent.

---

## Typische Fehlermeldungen

| Fehler | Ursache | Lösung |
|---|---|---|
| `WP_URL fehlt in env.local` | Credentials nicht eingetragen | `env.local` befüllen |
| `ModuleNotFoundError` | Falsche Python-Umgebung | `.venv/bin/python` statt `python3` |
| `nicht_gefunden` im Log | Kontext-Satz nicht im Artikel | Vorschlag war veraltet, ignorieren |
| `mehrdeutig` im Log | Satz kommt mehrfach vor | Manuell in WordPress setzen |
| JSON-Fehler in Proposal | Anführungszeichen in KI-Ausgabe | Auto-Repair läuft automatisch |
