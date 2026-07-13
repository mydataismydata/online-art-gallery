"""The Metropolitan Museum of Art Open Access — CC0, full-resolution originals, no key.
https://metmuseum.github.io/"""
import time

from ... import library
from ...names import name_match, parse_year
from ..util import session, fetch_json, download_to_tmp
from . import tuning

ID = "met"
LABEL = "The Met (Open Access)"
HINT = ("Searches the Metropolitan Museum of Art's CC0 open-access collection for "
        "public-domain paintings by the artist and downloads the original files.")
PLACEHOLDER = "Artist name, e.g. Johannes Vermeer"

BASE = "https://collectionapi.metmuseum.org/public/collection/v1"

ENDPOINTS = (("Search", BASE + "/search"), ("Object", BASE + "/objects/{id}"))
CONFIG = [
    {"key": "type_keywords", "label": "Accepted object types", "type": "text", "default": "painting",
     "help": "Comma-separated keywords, matched as substrings against each object's "
             "classification and objectName. Blank accepts every type."},
    {"key": "public_domain_only", "label": "Public-domain (CC0) only", "type": "bool", "default": True,
     "help": "Skip anything the Met doesn't flag as public domain."},
    {"key": "max_scan", "label": "Max objects to scan", "type": "int", "default": 4000, "min": 100, "max": 20000,
     "help": "Search can return thousands of ids; each is fetched one at a time, so this caps the work."},
]


def run(job):
    sess = session()
    cfg = tuning.effective(ID, CONFIG)
    keywords = [k.strip().lower() for k in cfg["type_keywords"].split(",") if k.strip()]
    data = fetch_json(sess, BASE + "/search", {
        "artistOrCulture": "true",
        "hasImages": "true",
        "q": job.query,
    })
    ids = data.get("objectIDs") or []
    job.log("Met search returned %d objects; checking each for public-domain paintings…" % len(ids))
    if len(ids) > cfg["max_scan"]:
        job.log("Capping scan at the first %d objects." % cfg["max_scan"])
        ids = ids[:cfg["max_scan"]]

    max_items = job.opts.get("max_items")
    for i, oid in enumerate(ids):
        if job.cancelled:
            return
        if i and i % 100 == 0:
            job.log("…scanned %d/%d objects so far (%d matched)" % (i, len(ids), job.found))
        try:
            obj = fetch_json(sess, "%s/objects/%d" % (BASE, oid))
        except Exception:
            continue
        time.sleep(0.1)

        if cfg["public_domain_only"] and not obj.get("isPublicDomain"):
            continue
        # The Met leaves `classification` blank on many paintings but sets
        # objectName to "Painting" — so check both, or we reject everything.
        kind = ((obj.get("classification") or "") + " " + (obj.get("objectName") or "")).lower()
        if keywords and not any(k in kind for k in keywords):
            continue
        artist = obj.get("artistDisplayName") or ""
        if not name_match(job.query, artist):
            continue
        url = obj.get("primaryImage") or ""
        if not url:
            continue

        job.found += 1
        title = obj.get("title") or "Untitled"
        if library.source_exists(ID, oid):
            job.skipped += 1
            continue

        meta = {
            "title": title,
            "date": obj.get("objectDate") or None,
            "year": obj.get("objectBeginDate") or parse_year(obj.get("objectDate")),
            "medium": obj.get("medium") or None,
            "style": None,
            "type": "painting",
            "source": ID,
            "source_id": oid,
            "source_url": obj.get("objectURL"),
        }
        try:
            tmp = download_to_tmp(sess, url)
        except Exception as e:
            small = obj.get("primaryImageSmall")
            try:
                if not small:
                    raise
                tmp = download_to_tmp(sess, small)
                job.log("Original failed for \"%s\" (%s); saved web-size instead." % (title, e))
            except Exception as e2:
                job.failed += 1
                job.log("FAILED \"%s\": %s" % (title, e2))
                continue
        path = library.save_work(artist, meta, tmp)
        job.saved += 1
        job.log("Saved: %s" % path.name)
        if max_items and job.saved >= max_items:
            job.log("Reached the requested maximum of %d works." % max_items)
            return
        time.sleep(0.6)
