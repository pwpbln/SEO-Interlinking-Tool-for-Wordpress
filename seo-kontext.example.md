# SEO-Kontext-Briefing – Vorlage
# Zweck: Dieses Dokument wird jeder KI-Analyse-Sitzung vorangestellt.
# Es definiert Positionierung, Zielgruppe und redaktionelle Ziele.
#
# ANLEITUNG: Kopieren Sie diese Datei zu seo-kontext.md und passen Sie
# alle Abschnitte an Ihre Website an. seo-kontext.md ist in .gitignore
# eingetragen und wird nicht ins Repository eingecheckt.

---

## Persona und Stimme

Der Autor ist [NAME] – [BERUFSBEZEICHNUNG] mit Sitz in [STADT]. Die Website
ist ein [BESCHREIBUNG]. Die Texte erscheinen in [SPRACHE], [STIL].

---

## Themenfelder (SEO-Silos)

### Silo 1 – [THEMA]
**Kern-Suchintention:** [Beschreibung der Suchintention]
**Wichtige Ankerbegriffe:** [Begriff1], [Begriff2], [Begriff3]
**Cornerstones:** [Beschreibung der wichtigsten Artikel]
**Ton:** [Tonalität]

### Silo 2 – [THEMA]
**Kern-Suchintention:** [Beschreibung]
**Wichtige Ankerbegriffe:** [Begriff1], [Begriff2]
**Cornerstones:** [Beschreibung]
**Ton:** [Tonalität]

### Silo 3 – [THEMA]
**Kern-Suchintention:** [Beschreibung]
**Wichtige Ankerbegriffe:** [Begriff1], [Begriff2]
**Cornerstones:** [Beschreibung]
**Ton:** [Tonalität]

---

## Zielgruppe

- [Zielgruppe 1]
- [Zielgruppe 2]
- [Zielgruppe 3]

**Vorwissen:** [Beschreibung des Wissensstandes]

---

## Verlinkungsstrategie

### Cornerstone-Hierarchie
- Cornerstone-Artikel sollen besonders viele interne Links erhalten.
- Jeder Nicht-Cornerstone-Artikel soll mindestens einen Link zu einem
  thematisch verwandten Cornerstone enthalten.
- Siloübergreifende Links sind erlaubt, wenn ein echter inhaltlicher
  Zusammenhang besteht.

### Regeln für neue Links
1. Nur verlinken, wenn im selben Absatz noch kein Link auf die
   Zieldomain gesetzt ist.
2. Ankertexte müssen natürlich im Satzfluss stehen – keine
   erzwungenen Schlüsselwort-Einfügungen.
3. Bestehende Links nicht duplizieren – pro Artikel maximal
   ein Link pro Zielartikel.
4. Keine Links auf Artikel setzen, die inhaltlich nicht passen,
   nur um die Verlinkungsdichte zu erhöhen.

---

## Bestehende Taxonomie – Kategoriebaum (Stand [DATUM])

```
[Kategorie 1]    (XX Artikel)
[Kategorie 2]    (XX Artikel)
  └ [Unterkategorie]  (XX Artikel)
```

---

## Ausschlüsse

- [Thema, das nicht optimiert werden soll]
- Kein Keyword-Stuffing
- [Weitere Ausschlüsse]

---

## Technische Rahmenbedingungen

- CMS: WordPress, [SPRACHE]
- Theme: [THEME-NAME]
- Rückspielen: ausschließlich via WordPress REST API mit
  Application Password – kein direkter Datenbankzugriff
- Freigabepflicht: jede Änderung muss manuell bestätigt werden
- Projektverzeichnis: ~/[PFAD]/SEO Crawler/
