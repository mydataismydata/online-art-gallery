"""Victoria and Albert Museum (London) — keyless open API, IIIF full images.

The V&A's public API (https://api.vam.ac.uk/) needs no key. We page the search,
keep fine-art object types (paintings, watercolours, drawings…) whose maker matches
the query, and pull the largest IIIF rendering of each. The world's largest applied-
arts museum, strong on British painting and works on paper (Constable, Turner…)."""
import re
import time

from ... import library
from ...names import name_match, normalize_comma_name, parse_year, unshout
from ..util import session, fetch_json, download_to_tmp
from . import tuning

ID = "vam"
LABEL = "Victoria & Albert Museum"
HINT = ("Searches the V&A's keyless open collection for paintings, watercolours and "
        "drawings by the artist and downloads the largest IIIF image of each. Especially "
        "strong on British art. Use the artist's name, e.g. 'John Constable'.")
PLACEHOLDER = "Artist name, e.g. J. M. W. Turner"

SEARCH = "https://api.vam.ac.uk/v2/objects/search"
_MAX_PAGES = 40                      # 40 * 100 = up to 4000 candidates
# IIIF sizes to try, largest first; the base url already ends in '/'.
SIZES = ("full", "!3000,3000", "!2048,2048", "!1024,1024")

ENDPOINTS = (("Search", SEARCH),)
CONFIG = [
    {"key": "fine_art_types", "label": "Accepted object types", "type": "text",
     "default": "painting, watercolour, watercolor, drawing, gouache, pastel, miniature, tempera",
     "help": "Comma-separated keywords, matched as substrings against each record's "
             "objectType. Blank accepts every object type."},
    {"key": "max_pages", "label": "Max result pages (100 each)", "type": "int", "default": 40, "min": 1, "max": 100,
     "help": "How many pages of search results to walk."},
]


def _clean_maker(name):
    """'Constable, John (RA)' -> 'John Constable'."""
    name = re.sub(r"\s*\([^)]*\)", "", name or "")     # drop '(RA)', '(engraver)', …
    return unshout(normalize_comma_name(name.strip()))


def run(job):
    sess = session()
    cfg = tuning.effective(ID, CONFIG)
    fine_art = [k.strip().lower() for k in cfg["fine_art_types"].split(",") if k.strip()]
    max_pages = cfg["max_pages"]
    max_items = job.opts.get("max_items")
    page, pages = 1, 1
    announced = False
    while page <= pages and page <= max_pages:
        if job.cancelled:
            return
        data = fetch_json(sess, SEARCH, {
            "q": job.query, "page_size": 100, "page": page,
            "images_exist": 1,  # relevance is the default order; passing order_by=relevance 422s
        })
        info = data.get("info") or {}
        pages = min(info.get("pages") or 1, max_pages)
        records = data.get("records") or []
        if not announced:
            job.log("V&A returned %d records with images; keeping fine art by the maker…"
                    % (info.get("record_count") or len(records)))
            announced = True
        if not records:
            break

        for rec in records:
            if job.cancelled:
                return
            otype = (rec.get("objectType") or "").lower()
            if fine_art and not any(k in otype for k in fine_art):
                continue
            maker = _clean_maker((rec.get("_primaryMaker") or {}).get("name"))
            if not maker or not name_match(job.query, maker):
                continue
            base = (rec.get("_images") or {}).get("_iiif_image_base_url")
            if not base:
                continue

            job.found += 1
            sysno = rec.get("systemNumber")
            title = rec.get("_primaryTitle") or "Untitled"
            if library.source_exists(ID, sysno):
                job.skipped += 1
                continue

            date_text = rec.get("_primaryDate") or None
            meta = {
                "title": title,
                "date": date_text,
                "year": parse_year(date_text),
                "medium": rec.get("objectType") or None,
                "style": None,
                "type": (rec.get("objectType") or "painting").strip().lower(),
                "source": ID,
                "source_id": sysno,
                "source_url": "https://collections.vam.ac.uk/item/%s/" % sysno,
            }
            tmp = None
            for size in SIZES:
                url = "%sfull/%s/0/default.jpg" % (base, size)
                try:
                    tmp = download_to_tmp(sess, url)
                    break
                except Exception:
                    continue
            if tmp is None:
                job.failed += 1
                job.log("FAILED \"%s\": no IIIF size worked" % title)
                continue
            path = library.save_work(maker, meta, tmp, job)
            job.saved += 1
            job.log("Saved: %s" % path.name)
            if max_items and job.saved >= max_items:
                job.log("Reached the requested maximum of %d works." % max_items)
                return
            time.sleep(0.5)
        page += 1
        time.sleep(0.3)
