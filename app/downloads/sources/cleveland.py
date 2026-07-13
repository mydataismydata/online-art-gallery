"""Cleveland Museum of Art Open Access — CC0, no key.
https://openaccess-api.clevelandart.org/"""
import time

from ... import library
from ...names import name_match, parse_year
from ..util import session, fetch_json, download_to_tmp
from . import tuning

ID = "cma"
LABEL = "Cleveland Museum of Art"
HINT = ("Searches the Cleveland Museum of Art's CC0 open-access collection "
        "and downloads the largest available image of each painting.")
PLACEHOLDER = "Artist name, e.g. J. M. W. Turner"

API = "https://openaccess-api.clevelandart.org/api/artworks/"

ENDPOINTS = (("API", API),)
CONFIG = [
    {"key": "object_type", "label": "Object type", "type": "text", "default": "Painting",
     "help": "Sent to Cleveland's `type` filter, e.g. Painting or Drawing. Blank searches all types."},
    {"key": "cc0_only", "label": "CC0 only", "type": "bool", "default": True,
     "help": "Restrict to CC0 open-access works. Off also pulls copyright-restricted records."},
    {"key": "max_scan", "label": "Max candidates to scan", "type": "int", "default": 2000, "min": 100, "max": 10000,
     "help": "Upper bound on how many candidate records to page through."},
]


def _artist_of(row):
    creators = row.get("creators") or []
    if not creators:
        return ""
    desc = creators[0].get("description") or ""
    return desc.split("(")[0].strip()


def run(job):
    sess = session()
    cfg = tuning.effective(ID, CONFIG)
    max_items = job.opts.get("max_items")
    skip, total = 0, None
    while total is None or skip < min(total, cfg["max_scan"]):
        if job.cancelled:
            return
        params = {"artists": job.query, "has_image": 1, "limit": 100, "skip": skip}
        if cfg["object_type"]:
            params["type"] = cfg["object_type"]
        if cfg["cc0_only"]:
            params["cc0"] = 1
        data = fetch_json(sess, API, params)
        if total is None:
            total = (data.get("info") or {}).get("total") or 0
            job.log("Cleveland returned %d candidate paintings…" % total)
        rows = data.get("data") or []
        if not rows:
            break
        for row in rows:
            if job.cancelled:
                return
            if cfg["cc0_only"] and (row.get("share_license_status") or "").upper() not in ("CC0", ""):
                continue
            artist = _artist_of(row)
            if not name_match(job.query, artist):
                continue
            images = row.get("images") or {}
            best = images.get("full") or images.get("print") or images.get("web") or {}
            url = best.get("url")
            if not url:
                continue

            job.found += 1
            title = row.get("title") or "Untitled"
            if library.source_exists(ID, row.get("id")):
                job.skipped += 1
                continue

            meta = {
                "title": title,
                "date": row.get("creation_date") or None,
                "year": row.get("creation_date_earliest") or parse_year(row.get("creation_date")),
                "medium": row.get("technique") or None,
                "style": None,
                "type": "painting",
                "source": ID,
                "source_id": row.get("id"),
                "source_url": row.get("url"),
            }
            try:
                tmp = download_to_tmp(sess, url)
            except Exception as e:
                job.failed += 1
                job.log("FAILED \"%s\": %s" % (title, e))
                continue
            path = library.save_work(artist, meta, tmp)
            job.saved += 1
            job.log("Saved: %s" % path.name)
            if max_items and job.saved >= max_items:
                job.log("Reached the requested maximum of %d works." % max_items)
                return
            time.sleep(0.6)
        skip += len(rows)
