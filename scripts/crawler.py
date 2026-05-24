"""
crawler.py – Modul 1: Sitemap-Crawler

Liest die WordPress-Sitemap, filtert Artikel-URLs und speichert
jeden Artikel als JSON-Datei unter data/parsed/<slug>.json.

Aufruf:
    python scripts/crawler.py                        # nur post-sitemap
    python scripts/crawler.py --include-pages        # auch page-sitemap
    python scripts/crawler.py --url URL              # einzelne Test-URL
    python scripts/crawler.py --force                # vorhandene überschreiben
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from markdownify import markdownify as md

try:
	from prompt_kompilieren import kompiliere as _kompiliere_prompt
except ImportError:
	_kompiliere_prompt = None

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "parsed"
LOG_DIR = PROJECT_ROOT / "logs"
ENV_FILE = PROJECT_ROOT / "env.local"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = LOG_DIR / "crawler.log"

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
# Konfiguration aus .env
# ---------------------------------------------------------------------------

load_dotenv(ENV_FILE)

WP_URL = os.getenv("WP_URL", "").rstrip("/")
WP_USER = os.getenv("WP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")

if not WP_URL:
    log.error("WP_URL fehlt in .env – Abbruch.")
    sys.exit(1)

# REST-API-Anreicherung nur möglich, wenn Credentials vorhanden
_REST_AUTH: tuple[str, str] | None = (
    (WP_USER, WP_APP_PASSWORD) if WP_USER and WP_APP_PASSWORD else None
)

SITEMAP_URL = f"{WP_URL}/sitemap.xml"

# Sitemaps, aus denen Artikel-URLs stammen (Präfix-Filter).
# page-sitemap wird nur aufgenommen, wenn --include-pages gesetzt ist.
ARTICLE_SITEMAP_HINTS = {"post-sitemap"}
PAGE_SITEMAP_HINTS = {"page-sitemap"}

# Zwischen-Requests-Pause in Sekunden (robots.txt-konform)
REQUEST_DELAY = 1.5

# ---------------------------------------------------------------------------
# HTTP-Session
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "SEOCrawler/1.0 (bot; respectful)"
        ),
        "Accept-Language": "de,en;q=0.9",
    }
)

# ---------------------------------------------------------------------------
# REST-API-Term-Cache
# ---------------------------------------------------------------------------

# Lokale Caches: term_id → name, um pro Crawl-Lauf nur einmal abzufragen
_cat_cache: dict[int, str] = {}
_tag_cache: dict[int, str] = {}


def _resolve_terms(ids: list[int], endpoint: str, cache: dict[int, str]) -> list[str]:
    """
    Löst eine Liste von Term-IDs gegen den angegebenen REST-Endpunkt auf.
    Bereits aufgelöste IDs werden aus dem Cache bedient.
    Unbekannte IDs werden gebündelt in einer einzigen API-Anfrage geholt.
    """
    missing = [i for i in ids if i not in cache]
    if missing and _REST_AUTH:
        id_str = ",".join(str(i) for i in missing)
        try:
            resp = SESSION.get(
                f"{WP_URL}/wp-json/wp/v2/{endpoint}",
                params={"include": id_str, "per_page": 100},
                auth=_REST_AUTH,
                timeout=15,
            )
            resp.raise_for_status()
            for term in resp.json():
                cache[term["id"]] = term["name"]
        except Exception as exc:
            log.warning("Term-Auflösung fehlgeschlagen (%s): %s", endpoint, exc)
    return [cache[i] for i in ids if i in cache]


def fetch_api_terms(slug: str) -> dict | None:
    """
    Fragt GET /wp-json/wp/v2/posts?slug={slug} ab und gibt ein Dict mit
    'categories' und 'tags' als Listen von Namen zurück.
    Gibt None zurück, wenn kein Ergebnis oder kein Auth-Credential vorliegt.
    """
    if not _REST_AUTH:
        return None
    try:
        resp = SESSION.get(
            f"{WP_URL}/wp-json/wp/v2/posts",
            params={"slug": slug, "_fields": "id,categories,tags"},
            auth=_REST_AUTH,
            timeout=15,
        )
        resp.raise_for_status()
        posts = resp.json()
    except Exception as exc:
        log.warning("REST-API-Abfrage fehlgeschlagen (slug=%s): %s", slug, exc)
        return None

    if not posts:
        return None

    post = posts[0]
    cat_ids: list[int] = post.get("categories", [])
    tag_ids: list[int] = post.get("tags", [])

    categories = _resolve_terms(cat_ids, "categories", _cat_cache)
    tags = _resolve_terms(tag_ids, "tags", _tag_cache)

    return {
        "post_id":    post.get("id"),
        "categories": sorted(categories),
        "tags":       sorted(tags),
    }


# ---------------------------------------------------------------------------
# Hilfsfunktionen – Sitemap
# ---------------------------------------------------------------------------


def fetch_xml(url: str) -> BeautifulSoup | None:
    """Lädt eine XML-URL und gibt ein BeautifulSoup-Objekt zurück."""
    try:
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.content, "lxml-xml")
    except requests.RequestException as exc:
        log.error("XML-Abruf fehlgeschlagen: %s  →  %s", url, exc)
        return None


def collect_article_urls(sitemap_url: str, include_pages: bool = False) -> list[str]:
    """
    Liest die Root-Sitemap und alle verlinkten Unter-Sitemaps.
    Standardmäßig nur post-sitemap; mit include_pages=True auch page-sitemap.
    """
    root = fetch_xml(sitemap_url)
    if root is None:
        return []

    allowed_hints = set(ARTICLE_SITEMAP_HINTS)
    if include_pages:
        allowed_hints |= PAGE_SITEMAP_HINTS

    urls: list[str] = []

    sitemap_locs = root.find_all("sitemap")
    if sitemap_locs:
        for sitemap_tag in sitemap_locs:
            loc = sitemap_tag.find("loc")
            if loc is None:
                continue
            child_url = loc.text.strip()
            child_name = Path(urlparse(child_url).path).stem  # z. B. "post-sitemap"
            if not any(hint in child_name for hint in allowed_hints):
                log.info("Sitemap übersprungen: %s", child_url)
                continue
            log.info("Lese Unter-Sitemap: %s", child_url)
            child_soup = fetch_xml(child_url)
            if child_soup:
                for url_tag in child_soup.find_all("url"):
                    loc_tag = url_tag.find("loc")
                    if loc_tag:
                        urls.append(loc_tag.text.strip())
    else:
        # Flache Sitemap ohne Index-Ebene
        for url_tag in root.find_all("url"):
            loc_tag = url_tag.find("loc")
            if loc_tag:
                urls.append(loc_tag.text.strip())

    log.info("URLs gesammelt: %d", len(urls))
    return urls


# ---------------------------------------------------------------------------
# Hilfsfunktionen – Artikel-Parsing
# ---------------------------------------------------------------------------


def slug_from_url(url: str) -> str:
    """Extrahiert den letzten Pfad-Abschnitt als Slug."""
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] or "index"


def extract_meta_terms(soup: BeautifulSoup, base_url: str) -> dict:
    """
    Liest WordPress-Kategorien und Schlagwörter aus:
    1. <meta name="article:tag"> / <meta property="article:section">
    2. body-Klassen (category-*, tag-*)
    3. Link-rel="category tag" im Head
    """
    categories: list[str] = []
    tags: list[str] = []

    # Open-Graph / Standard-Meta
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        content = meta.get("content", "").strip()
        if not content:
            continue
        if prop in ("article:section",):
            categories.append(content)
        elif prop in ("article:tag",):
            tags.append(content)

    # <link rel="category"> und <link rel="tag">
    for link in soup.find_all("link", rel=True):
        rels = link.get("rel", [])
        title = link.get("title", "").strip()
        if not title:
            continue
        if "category" in rels:
            categories.append(title)
        elif "tag" in rels:
            tags.append(title)

    # body-Klassen als Fallback
    body = soup.find("body")
    if body:
        classes = body.get("class", [])
        for cls in classes:
            m = re.match(r"^category-(.+)$", cls)
            if m:
                categories.append(m.group(1).replace("-", " "))
            m = re.match(r"^tag-(.+)$", cls)
            if m:
                tags.append(m.group(1).replace("-", " "))

    return {
        "categories": sorted(set(categories)),
        "tags": sorted(set(tags)),
    }


# Selektor-Liste für Elemente, die NICHT zum Fließtext gehören
_NOISE_SELECTORS = [
    "nav", "header", "footer", "aside",
    ".sidebar", "#sidebar",
    ".navigation", ".nav", ".menu",
    ".widget", ".widget-area",
    ".comments", "#comments",
    ".post-navigation", ".breadcrumb",
    ".sharedaddy", ".jp-relatedposts",
    # Qi-Theme-spezifisch
    ".qodef-header-wrapper",
    ".qodef-footer-wrapper",
    ".qodef-sidebar",
    ".qodef-post-info",          # Metadaten-Leiste (Datum, Autor)
    ".qodef-post-tags",          # Tag-Liste unter dem Artikel
    ".qodef-post-navigation",
    ".qodef-related-posts",
    # Autorenblock unter dem Artikel
    ".author-info",
    ".author-content",
    ".author-description",
    ".author-social-profiles",
    "script", "style", "noscript",
]

# Content-Selektoren in Prioritätsreihenfolge (Qi-Theme zuerst)
_CONTENT_CANDIDATES = [
    # Qi / qi-zehn
    ".qodef-post-text",
    "article.qodef-blog-item",
    ".qodef-content-inner",
    # Generische WordPress-Kandidaten
    ".entry-content",
    ".post-content",
    "article",
    "main",
]


def extract_main_content(soup: BeautifulSoup) -> BeautifulSoup:
    """Entfernt Rauschen, sucht den Qi-Theme-Inhaltsbereich und gibt ihn zurück."""
    working = BeautifulSoup(str(soup), "lxml")

    for selector in _NOISE_SELECTORS:
        for el in working.select(selector):
            el.decompose()

    for selector in _CONTENT_CANDIDATES:
        candidate = working.select_one(selector)
        if candidate:
            return candidate

    return working.find("body") or working


def extract_internal_links(
    content_el: BeautifulSoup, base_domain: str
) -> list[dict]:
    """Sammelt alle internen Links (gleiche Domain) mit Ankertext und Ziel-URL."""
    links = []
    seen_hrefs: set[str] = set()

    for a in content_el.find_all("a", href=True):
        href = a["href"].strip()
        # Relative URLs auflösen
        if href.startswith("/"):
            href = f"https://{base_domain}{href}"

        parsed = urlparse(href)
        # Nur http/https und gleiche Domain
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lstrip("www.") != base_domain.lstrip("www."):
            continue
        # Duplikate überspringen
        canonical = href.rstrip("/")
        if canonical in seen_hrefs:
            continue
        seen_hrefs.add(canonical)

        anchor = a.get_text(separator=" ", strip=True)
        if anchor:
            links.append({"anchor": anchor, "url": href})

    return links


def parse_article(url: str) -> dict | None:
    """
    Ruft einen Artikel ab und gibt ein strukturiertes Dict zurück.
    Gibt None zurück, wenn der Abruf fehlschlägt.
    """
    try:
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("FEHLER  %s  →  %s", url, exc)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    base_domain = urlparse(WP_URL).netloc

    # Titel (h1 bevorzugt, Fallback: <title>)
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

    # Veröffentlichungsdatum
    pub_date = ""
    date_candidates = [
        soup.find("meta", property="article:published_time"),
        soup.find("meta", attrs={"name": "date"}),
        soup.find("time", attrs={"class": re.compile(r"published|entry-date")}),
        soup.find("time"),
    ]
    for candidate in date_candidates:
        if candidate is None:
            continue
        val = candidate.get("content") or candidate.get("datetime") or ""
        val = val.strip()
        if val:
            pub_date = val
            break

    # Kategorien & Tags: REST-API bevorzugt, Meta-Tags als Fallback
    slug = slug_from_url(url)
    api_terms = fetch_api_terms(slug)
    if api_terms:
        post_id    = api_terms["post_id"]
        categories = api_terms["categories"]
        tags       = api_terms["tags"]
    else:
        post_id    = None
        meta_terms = extract_meta_terms(soup, WP_URL)
        categories = meta_terms["categories"]
        tags       = meta_terms["tags"]

    # Fließtext → Markdown
    content_el = extract_main_content(soup)
    raw_html = str(content_el)
    markdown_text = md(
        raw_html,
        heading_style="ATX",
        bullets="-",
        strip=["img", "figure", "figcaption"],
    ).strip()

    # Interne Links
    internal_links = extract_internal_links(content_el, base_domain)

    return {
        "url":            url,
        "slug":           slug,
        "post_id":        post_id,
        "title":          title,
        "published":      pub_date,
        "categories":     categories,
        "tags":           tags,
        "internal_links": internal_links,
        "markdown":       markdown_text,
        "crawled_at":     datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SEO Crawler – Modul 1: Artikel-Extraktion via Sitemap"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bereits vorhandene JSON-Dateien überschreiben",
    )
    parser.add_argument(
        "--include-pages",
        action="store_true",
        help="Auch page-sitemap einlesen (Standard: nur post-sitemap)",
    )
    parser.add_argument(
        "--url",
        metavar="URL",
        help="Einzelne URL verarbeiten (zum Testen, ignoriert Sitemap)",
    )
    args = parser.parse_args()

    log.info("=== Crawler gestartet ===  WP_URL=%s", WP_URL)

    # Einzelne Test-URL
    if args.url:
        log.info("Test-Modus: einzelne URL  %s", args.url)
        article_urls = [args.url]
    else:
        log.info("Sitemap: %s", SITEMAP_URL)
        if args.include_pages:
            log.info("Flag: --include-pages aktiv")
        if args.force:
            log.info("Flag: --force aktiv")
        article_urls = collect_article_urls(
            SITEMAP_URL, include_pages=args.include_pages
        )
        if not article_urls:
            log.error("Keine Artikel-URLs gefunden – Abbruch.")
            sys.exit(1)

    ok_count = 0
    skip_count = 0
    error_count = 0

    for i, url in enumerate(article_urls, start=1):
        slug = slug_from_url(url)
        out_path = DATA_DIR / f"{slug}.json"

        if out_path.exists() and not args.force:
            log.info("SKIP  [%d/%d]  %s", i, len(article_urls), url)
            skip_count += 1
            continue

        log.info("OK    [%d/%d]  %s", i, len(article_urls), url)
        article = parse_article(url)

        if article is None:
            error_count += 1
        else:
            out_path.write_text(
                json.dumps(article, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            ok_count += 1

        # Pause zwischen Requests (außer nach dem letzten)
        if i < len(article_urls):
            time.sleep(REQUEST_DELAY)

    log.info(
        "=== Fertig ===  OK=%d  SKIP=%d  FEHLER=%d",
        ok_count,
        skip_count,
        error_count,
    )

    if _kompiliere_prompt is not None:
        try:
            _kompiliere_prompt(modus="claude-desktop")
        except Exception as exc:
            log.warning("prompt_kompilieren fehlgeschlagen (nicht kritisch): %s", exc)


if __name__ == "__main__":
    main()
