"""Rijksmuseum open data — keyless, full-resolution, CC0/public-domain.

In 2026 the Rijksmuseum retired its old keyed collection API (HTTP 410) in favour
of a keyless Linked-Art Search API. Search returns only LOD identifiers, so each
work takes a short resolve chain:

    search/collection            -> object ids
    <object>                     -> title, date, maker id, VisualItem id
    <VisualItem>                 -> DigitalObject id
    <DigitalObject>.access_point -> https://iiif.micr.io/<id>/full/max/0/default.jpg

No API key is required. Docs: https://data.rijksmuseum.nl/docs/
"""
import time
from urllib.parse import quote

import requests

from ... import library
from ...names import name_match, normalize_comma_name, parse_year, unshout
from ..util import session, download_to_tmp, request_with_retries
from . import tuning


def _clean_name(s):
    """'Vermeer, Johannes' -> 'Johannes Vermeer'; 'REMBRANDT' -> 'Rembrandt'.
    Keeps Rijksmuseum makers from splitting off from the same artist elsewhere."""
    return unshout(normalize_comma_name((s or "").strip()))

ID = "rijks"
LABEL = "Rijksmuseum (open data)"
HINT = ("Searches the Rijksmuseum's keyless open-data API for paintings by the maker "
        "and downloads the full-resolution IIIF image — no API key needed. Their new "
        "API resolves each work through several linked records, so it is a little "
        "slower than the museum APIs. Use the maker's name, e.g. 'Rembrandt van Rijn'.")
PLACEHOLDER = "Maker name, e.g. Johannes Vermeer"

SEARCH = "https://data.rijksmuseum.nl/search/collection"
LD_JSON = "application/ld+json"
AAT_ENGLISH = "300388277"
_MAX_PAGES = 60  # safety net: 60 * 100 = 6000 objects

ENDPOINTS = (("Search", SEARCH),)
CONFIG = [
    {"key": "search_type", "label": "Search type filter", "type": "text", "default": "painting",
     "help": "Sent to the Rijksmuseum `type` search filter, e.g. painting or drawing. "
             "Blank searches all object types."},
    {"key": "max_pages", "label": "Max result pages (100 each)", "type": "int", "default": 60, "min": 1, "max": 100,
     "help": "Safety cap on how many pages of linked-data results to resolve."},
]


def _getj(sess, url):
    r = request_with_retries(sess, url, headers={"Accept": LD_JSON}, timeout=45)
    r.raise_for_status()
    return r.json()


def _names(entity):
    return [n for n in (entity.get("identified_by") or [])
            if n.get("type") == "Name" and n.get("content")]


def _is_english(name):
    return any(AAT_ENGLISH in (l.get("id") or "") for l in (name.get("language") or []))


def _best_title(obj):
    names = _names(obj)
    for n in names:                       # prefer an English title
        if _is_english(n):
            return n["content"].strip()
    return names[0]["content"].strip() if names else "Untitled"


def _date_text(produced_by):
    ts = produced_by.get("timespan") or {}
    if ts.get("_label"):
        return ts["_label"]
    for i in (ts.get("identified_by") or []):
        if i.get("content"):
            return i["content"]
    return None


def _maker(sess, produced_by, cache):
    ids = []
    for part in [produced_by] + (produced_by.get("part") or []):
        for a in (part.get("carried_out_by") or []):
            if a.get("_label"):
                return _clean_name(a["_label"])
            if a.get("id"):
                ids.append(a["id"])
    if not ids:
        return None
    aid = ids[0]
    if aid in cache:
        return cache[aid]
    name = None
    try:
        names = _names(_getj(sess, aid))
        if names:
            name = _clean_name(names[0]["content"])
    except Exception:
        pass
    cache[aid] = name
    return name


def _materials(obj):
    mats = [m.get("_label") for m in (obj.get("made_of") or []) if m.get("_label")]
    return ", ".join(mats) or None


def _find_digital_object_id(entity):
    """Depth-first search for a nested DigitalObject's id."""
    found = [None]

    def walk(o):
        if found[0]:
            return
        if isinstance(o, dict):
            if o.get("type") == "DigitalObject" and o.get("id"):
                found[0] = o["id"]
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(entity)
    return found[0]


def _image_url(sess, obj):
    shows = obj.get("shows") or []
    if not shows:
        return None
    visual = _getj(sess, shows[0]["id"])
    dobj_id = _find_digital_object_id(visual)
    if not dobj_id:
        return None
    dobj = _getj(sess, dobj_id)
    for ap in (dobj.get("access_point") or []):
        if ap.get("id"):
            return ap["id"]
    return None


def run(job):
    sess = session()
    cfg = tuning.effective(ID, CONFIG)
    q = job.query.strip()
    max_items = job.opts.get("max_items")
    actor_cache = {}

    type_q = ("&type=%s" % quote(cfg["search_type"])) if cfg["search_type"] else ""
    url = "%s?creator=%s%s&imageAvailable=true" % (SEARCH, quote(q), type_q)
    pages = 0
    while url and pages < cfg["max_pages"]:
        if job.cancelled:
            return
        try:
            data = _getj(sess, url)
        except requests.ConnectionError:
            raise RuntimeError("Couldn't reach the Rijksmuseum (data.rijksmuseum.nl) after "
                               "several tries — check the server's internet/DNS connection "
                               "and try again.")
        items = data.get("orderedItems") or []
        if pages == 0:
            total = (data.get("partOf") or {}).get("totalItems")
            job.log("Rijksmuseum: %s matching paintings; resolving each work…"
                    % (total if total is not None else len(items)))
        for it in items:
            if job.cancelled:
                return
            oid = it.get("id")
            if not oid:
                continue
            source_id = oid.rstrip("/").rsplit("/", 1)[-1]
            try:
                obj = _getj(sess, oid)
                artist = _maker(sess, obj.get("produced_by") or {}, actor_cache) or q
                # the search matches makers loosely; keep only real matches
                if not name_match(q, artist):
                    continue
                img = _image_url(sess, obj)
            except Exception as e:
                job.failed += 1
                job.log("FAILED %s: %s" % (source_id, e))
                continue
            if not img:
                continue

            job.found += 1
            title = _best_title(obj)
            if library.source_exists(ID, source_id):
                job.skipped += 1
                continue
            date_text = _date_text(obj.get("produced_by") or {})
            meta = {
                "title": title,
                "date": date_text,
                "year": parse_year(date_text),
                "medium": _materials(obj),
                "style": None,
                "type": "painting",
                "source": ID,
                "source_id": source_id,
                "source_url": oid,  # id.rijksmuseum.nl/<id> resolves to the web page
            }
            try:
                tmp = download_to_tmp(sess, img, referer="https://www.rijksmuseum.nl/")
            except Exception as e:
                job.failed += 1
                job.log("FAILED \"%s\": %s" % (title, e))
                continue
            path = library.save_work(artist, meta, tmp, job)
            job.saved += 1
            job.log("Saved: %s" % path.name)
            if max_items and job.saved >= max_items:
                job.log("Reached the requested maximum of %d works." % max_items)
                return
            time.sleep(0.4)

        url = (data.get("next") or {}).get("id")
        pages += 1
        time.sleep(0.3)
