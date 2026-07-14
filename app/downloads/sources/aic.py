"""Art Institute of Chicago — CC0 public-domain images over IIIF, no key.
https://api.artic.edu/docs/"""
import time

from ... import library
from ...names import name_match
from ..util import session, fetch_json, download_to_tmp
from . import tuning

ID = "aic"
LABEL = "Art Institute of Chicago"
HINT = ("Searches the Art Institute of Chicago's CC0 collection and downloads "
        "the largest image their IIIF server will give for each painting.")
PLACEHOLDER = "Artist name, e.g. Gustave Caillebotte"

API = "https://api.artic.edu/api/v1/artworks/search"
IIIF = "https://www.artic.edu/iiif/2"
FIELDS = ("id,title,image_id,artist_title,date_display,date_start,"
          "medium_display,style_title,artwork_type_title,is_public_domain")
# Sizes to try, best first. Full-resolution works for public-domain images;
# the smaller ones are graceful fallbacks.
SIZES = ("full", "3000,", "1686,", "843,")

ENDPOINTS = (("Search", API), ("IIIF images", IIIF))
CONFIG = [
    {"key": "type_keywords", "label": "Accepted artwork types", "type": "text", "default": "painting",
     "help": "Comma-separated keywords, matched as substrings against each work's "
             "artwork_type_title (e.g. 'painting', 'drawing'). Blank accepts every type."},
    {"key": "public_domain_only", "label": "Public-domain (CC0) only", "type": "bool", "default": True,
     "help": "Skip works the Art Institute doesn't flag as public domain."},
    {"key": "max_pages", "label": "Max result pages (100 each)", "type": "int", "default": 10, "min": 1, "max": 50,
     "help": "How many pages of search results to walk."},
]


def run(job):
    sess = session()
    cfg = tuning.effective(ID, CONFIG)
    keywords = [k.strip().lower() for k in cfg["type_keywords"].split(",") if k.strip()]
    max_pages = cfg["max_pages"]
    max_items = job.opts.get("max_items")
    page, total_pages = 1, 1
    while page <= total_pages and page <= max_pages:
        if job.cancelled:
            return
        data = fetch_json(sess, API, {
            "q": job.query, "fields": FIELDS, "limit": 100, "page": page,
        })
        total_pages = min((data.get("pagination") or {}).get("total_pages") or 1, max_pages)
        rows = data.get("data") or []
        if page == 1:
            job.log("AIC search returned %d results; filtering to public-domain paintings…"
                    % ((data.get("pagination") or {}).get("total") or len(rows)))
        for row in rows:
            if job.cancelled:
                return
            if not row.get("image_id"):
                continue
            if cfg["public_domain_only"] and not row.get("is_public_domain"):
                continue
            atype = (row.get("artwork_type_title") or "").lower()
            if keywords and not any(k in atype for k in keywords):
                continue
            artist = row.get("artist_title") or ""
            if not name_match(job.query, artist):
                continue

            job.found += 1
            title = row.get("title") or "Untitled"
            if library.source_exists(ID, row["id"]):
                job.skipped += 1
                continue

            meta = {
                "title": title,
                "date": row.get("date_display") or None,
                "year": row.get("date_start") or None,
                "medium": row.get("medium_display") or None,
                "style": row.get("style_title") or None,
                "type": "painting",
                "source": ID,
                "source_id": row["id"],
                "source_url": "https://www.artic.edu/artworks/%s" % row["id"],
            }
            tmp = None
            for size in SIZES:
                url = "%s/%s/full/%s/0/default.jpg" % (IIIF, row["image_id"], size)
                try:
                    tmp = download_to_tmp(sess, url)
                    break
                except Exception:
                    continue
            if tmp is None:
                job.failed += 1
                job.log("FAILED \"%s\": no IIIF size worked" % title)
                continue
            path = library.save_work(artist, meta, tmp, job)
            job.saved += 1
            job.log("Saved: %s" % path.name)
            if max_items and job.saved >= max_items:
                job.log("Reached the requested maximum of %d works." % max_items)
                return
            time.sleep(0.6)
        page += 1
