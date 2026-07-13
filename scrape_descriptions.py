"""TEMPORARY, best-effort backfill of piece descriptions for the works already in
your library — for the new placards feature. Safe to delete afterwards.

For each work it tries, in order:
  1. the work's own source museum API (Cleveland, Art Institute) for a real label /
     catalogue description, then
  2. Wikidata -> English Wikipedia, matched by title AND artist (only accepts a
     painting/artwork whose creator matches), for everything else (imports, Google
     Arts & Culture, Met, Rijks, ...).

Descriptions are written into each work's JSON sidecar. Existing descriptions are
kept unless you pass --force.

    # local (Windows):  point at your library and run
    .venv\\Scripts\\python.exe scrape_descriptions.py --limit 20      # try 20, see how it does
    .venv\\Scripts\\python.exe scrape_descriptions.py                 # the whole library

    # on the Ubuntu server:
    sudo -u gallery env GALLERY_LIBRARY=/var/lib/gallery/library \\
      /opt/gallery/.venv/bin/python /opt/gallery/scrape_descriptions.py

Flags: --force (overwrite), --limit N, --source cma|aic|import|gac|... (only that
source), --sleep S (between works; default 0.7), --dry-run (fetch but don't write).
"""
import argparse
import html as html_mod
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import config, library  # noqa: E402  (also creates dirs)
from app.names import strip_diacritics  # noqa: E402

UA = config.USER_AGENT + " (+description backfill; contact: gallery owner)"
S = requests.Session()
S.headers.update({"User-Agent": UA})
TIMEOUT = 25
MIN_LEN = 40          # ignore uselessly short blurbs
MAX_LEN = 1500        # trim very long extracts to a placard-sized paragraph


def log(msg):
    print(msg, flush=True)


def _get(url, **kw):
    for attempt in range(3):
        try:
            r = S.get(url, timeout=TIMEOUT, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return None


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _tidy(text):
    text = strip_html(text)
    if len(text) > MAX_LEN:
        cut = text[:MAX_LEN].rsplit(". ", 1)[0]
        text = (cut + ".") if cut else text[:MAX_LEN]
    return text if len(text) >= MIN_LEN else ""


def _name_tokens(name):
    return {t for t in re.split(r"[^a-z0-9]+", strip_diacritics(name or "").lower()) if len(t) > 2}


# ---- source museum APIs ------------------------------------------------------

def cma_desc(sid):
    r = _get("https://openaccess-api.clevelandart.org/api/artworks/%s" % sid)
    if not r:
        return ""
    data = (r.json() or {}).get("data") or {}
    for k in ("wall_description", "description", "digital_description", "tombstone"):
        v = _tidy(data.get(k))
        if v:
            return v
    return ""


def aic_desc(sid):
    r = _get("https://api.artic.edu/api/v1/artworks/%s?fields=description,short_description" % sid)
    if not r:
        return ""
    data = (r.json() or {}).get("data") or {}
    for k in ("description", "short_description"):
        v = _tidy(data.get(k))
        if v:
            return v
    return ""


SOURCE_FETCHERS = {"cma": cma_desc, "aic": aic_desc}


# ---- Wikidata -> Wikipedia fallback -----------------------------------------

_ENTITY_CACHE = {}


def _entity(qid):
    if qid in _ENTITY_CACHE:
        return _ENTITY_CACHE[qid]
    r = _get("https://www.wikidata.org/wiki/Special:EntityData/%s.json" % qid)
    ent = None
    if r:
        try:
            ent = (r.json().get("entities") or {}).get(qid)
        except ValueError:
            ent = None
    _ENTITY_CACHE[qid] = ent
    return ent


def _claim_ids(ent, prop):
    out = []
    for c in (ent.get("claims") or {}).get(prop, []):
        try:
            out.append(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            pass
    return out


# painting (Q3305213) plus a few common artwork classes we'll accept
_ART_TYPES = {"Q3305213", "Q838948", "Q11060274", "Q93184", "Q179700"}


def wikipedia_desc(title, artist):
    """Find a Wikidata item matching the title whose creator matches the artist,
    then return its English Wikipedia intro extract (or the Wikidata description)."""
    if not title:
        return ""
    r = _get("https://www.wikidata.org/w/api.php", params={
        "action": "wbsearchentities", "search": title, "language": "en",
        "format": "json", "type": "item", "limit": 6})
    if not r:
        return ""
    artist_tokens = _name_tokens(artist)
    for hit in (r.json().get("search") or [])[:6]:
        ent = _entity(hit["id"])
        if not ent:
            continue
        if not (set(_claim_ids(ent, "P31")) & _ART_TYPES):
            continue
        # creator (P170) label must overlap the artist name
        creators = _claim_ids(ent, "P170")
        ok = not artist_tokens  # if we have no artist, don't gate on it
        for cq in creators:
            cent = _entity(cq)
            label = (((cent or {}).get("labels") or {}).get("en") or {}).get("value", "")
            if artist_tokens & _name_tokens(label):
                ok = True
                break
        if not ok:
            continue
        # prefer the Wikipedia intro; fall back to the Wikidata description
        site = (((ent.get("sitelinks") or {}).get("enwiki")) or {}).get("title")
        if site:
            wr = _get("https://en.wikipedia.org/api/rest_v1/page/summary/" +
                      requests.utils.quote(site.replace(" ", "_"), safe=""))
            if wr:
                extract = _tidy((wr.json() or {}).get("extract"))
                if extract:
                    return extract
        desc = _tidy((((ent.get("descriptions") or {}).get("en")) or {}).get("value"))
        if desc:
            return desc
    return ""


def fetch_description(w):
    src, sid = w.get("source"), w.get("source_id")
    fetcher = SOURCE_FETCHERS.get(src)
    if fetcher and sid:
        d = fetcher(sid)
        if d:
            return d, src
    d = wikipedia_desc(w.get("title"), w.get("artist"))
    if d:
        return d, "wikipedia"
    return "", None


def write_description(w, desc):
    sc = Path(config.LIBRARY_DIR / w["rel"]).with_name(Path(w["rel"]).name + ".json")
    data = {}
    if sc.exists():
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["description"] = desc
    sc.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Backfill piece descriptions into sidecars.")
    ap.add_argument("--force", action="store_true", help="overwrite existing descriptions")
    ap.add_argument("--limit", type=int, default=0, help="stop after N works (0 = all)")
    ap.add_argument("--source", help="only works from this source (cma, aic, import, gac, ...)")
    ap.add_argument("--sleep", type=float, default=0.7, help="seconds between works")
    ap.add_argument("--dry-run", action="store_true", help="fetch but don't write sidecars")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    works = library.scan(force=True)["works"]
    log("Library: %s  (%d works)" % (config.LIBRARY_DIR, len(works)))

    found = skipped = missed = 0
    tried = 0
    for w in works:
        if args.source and w.get("source") != args.source:
            continue
        if w.get("description") and not args.force:
            skipped += 1
            continue
        if args.limit and tried >= args.limit:
            break
        tried += 1
        label = "%s — %s" % (w.get("title"), w.get("artist"))
        try:
            desc, provider = fetch_description(w)
        except Exception as e:
            desc, provider = "", None
            log("  ERROR  %s (%s)" % (label, e))
        if desc:
            found += 1
            log("  [%s] %s" % (provider, label))
            log("         %s" % (desc[:160] + ("…" if len(desc) > 160 else "")))
            if not args.dry_run:
                write_description(w, desc)
        else:
            missed += 1
            log("  ----   no description found: %s" % label)
        time.sleep(args.sleep)

    if not args.dry_run and found:
        library.invalidate()
    log("\nDone. wrote/found %d · skipped %d (already had one) · no match %d%s"
        % (found, skipped, missed, "  [DRY RUN — nothing written]" if args.dry_run else ""))


if __name__ == "__main__":
    main()
