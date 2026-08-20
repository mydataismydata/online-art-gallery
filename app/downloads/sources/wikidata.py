"""Wikidata + Wikimedia Commons — public-domain artworks by an artist, keyless.

Resolves the typed name to a Wikidata entity, then asks the Wikidata Query Service
(SPARQL) for that creator's paintings that carry an image (P18), and downloads the
full-resolution original from Wikimedia Commons. No API key, and coverage is enormous
(virtually every notable painter) — the trade-off is crowd-sourced metadata, so titles
and dates vary in polish. Docs: https://query.wikidata.org/"""
import re
import time
import urllib.parse

from PIL import Image

from ... import config, library, artistinfo
from ...names import parse_year
from ..util import session, fetch_json, download_to_tmp, job_hooks
from . import tuning

ID = "wikidata"
LABEL = "Wikidata / Wikimedia Commons"
HINT = ("Looks the artist up on Wikidata, then downloads the full-resolution public-"
        "domain images of their paintings from Wikimedia Commons. Vast coverage and no "
        "API key — metadata is crowd-sourced, so quality varies. Use the artist's full "
        "name, e.g. 'Rembrandt van Rijn'.")
PLACEHOLDER = "Artist name, e.g. Claude Monet"

WDQS = "https://query.wikidata.org/sparql"
COMMONS = "https://commons.wikimedia.org/w/api.php"
_LIMIT = 500  # per-artist cap on works pulled from SPARQL

ENDPOINTS = (("SPARQL endpoint", WDQS), ("Commons API", COMMONS))
CONFIG = [
    {"key": "min_px", "label": "Minimum pixels on the long side", "type": "int",
     "default": config.VIEW_MAX, "min": 0, "max": 30000,
     "help": "A painting whose Commons image is no bigger than this on its long side is "
             "reported and skipped rather than saved small. Defaults to the viewer's own "
             "size (GALLERY_VIEW_MAX), the point below which a painting is being blown up "
             "to hang. Set 0 to keep every image regardless of size."},
    {"key": "max_works", "label": "Max works per artist", "type": "int", "default": 500, "min": 10, "max": 2000,
     "help": "Upper bound on how many of an artist's paintings the SPARQL query returns."},
]

# Paintings (Q3305213 or a subclass) by the given creator that have an image.
# GROUP BY collapses the extra rows that the optional material values would create.
_SPARQL = """SELECT ?item ?itemLabel ?image (SAMPLE(?inception) AS ?date)
       (GROUP_CONCAT(DISTINCT ?matLabel; SEPARATOR=", ") AS ?materials) WHERE {
  ?item wdt:P170 wd:%(qid)s ;
        wdt:P18 ?image ;
        wdt:P31/wdt:P279* wd:Q3305213 .
  OPTIONAL { ?item wdt:P571 ?inception. }
  OPTIONAL { ?item wdt:P186 ?mat. ?mat rdfs:label ?matLabel. FILTER(LANG(?matLabel)="en") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
GROUP BY ?item ?itemLabel ?image
LIMIT %(limit)d"""

_QID_RE = re.compile(r"^Q\d+$")


def _val(row, key):
    return (row.get(key) or {}).get("value")


def _file_of(url):
    """A P18 Commons Special:FilePath URL -> the file's page title, as imageinfo
    returns it (spaces, not underscores) so the two line up."""
    return urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace("_", " ")


def _sizes(sess, files, **hooks):
    """{file title: (w, h)} from the Commons imageinfo API, so a painting that only
    exists small is ruled out before a byte of it is fetched. 40 titles a call keeps
    the rate limiter quiet; a file it can't measure simply doesn't appear here and is
    judged after download instead."""
    out = {}
    files = sorted(set(files))
    for i in range(0, len(files), 40):
        chunk = files[i:i + 40]
        try:
            r = fetch_json(sess, COMMONS,
                           {"action": "query", "prop": "imageinfo", "iiprop": "size|mime",
                            "titles": "|".join("File:" + f for f in chunk),
                            "format": "json", "formatversion": "2"},
                           timeout=60, max_wait=60, **hooks)
        except Exception:
            continue
        for p in (r.get("query") or {}).get("pages") or []:
            ii = (p.get("imageinfo") or [{}])[0]
            if ii.get("width"):
                out[p["title"].split(":", 1)[1]] = (ii["width"], ii["height"])
    return out


def _long_side(path):
    """The downloaded file's longer edge — the backstop for a P18 whose size Commons
    didn't hand back in the batch above."""
    try:
        with Image.open(str(path)) as im:
            return max(im.size)
    except Exception:
        return 0


def run(job):
    sess = session()
    hooks = job_hooks(job, "Wikimedia")
    cfg = tuning.effective(ID, CONFIG)
    limit = cfg["max_works"]
    min_px = job.opts.get("min_px") or cfg["min_px"]
    job.log("Identifying \"%s\" on Wikidata…" % job.query)
    qid, label = artistinfo.resolve_qid(job.query)
    if not qid:
        raise RuntimeError("Couldn't confidently match that name to an artist on Wikidata. "
                           "Try the artist's full name, e.g. 'Rembrandt van Rijn'.")
    artist = label or job.query
    job.log("Matched %s (%s); querying their paintings…" % (artist, qid))

    query = _SPARQL % {"qid": qid, "limit": limit}
    data = fetch_json(sess, WDQS, {"query": query, "format": "json"}, timeout=90, **hooks)
    rows = (data.get("results") or {}).get("bindings") or []
    job.log("Wikidata lists %d painting%s with an image%s."
            % (len(rows), "" if len(rows) == 1 else "s",
               " (capped)" if len(rows) >= limit else ""))

    # Measure every candidate in a couple of batched calls, so a painting that only
    # exists small is dropped before it's downloaded rather than after.
    sizes = {}
    if min_px:
        job.log("Skipping anything %d px or smaller on the long side." % min_px)
        files = [_file_of(img) for img in (_val(r, "image") for r in rows) if img]
        sizes = _sizes(sess, files, **hooks)

    small = []
    max_items = job.opts.get("max_items")
    for row in rows:
        if job.cancelled:
            return
        item = _val(row, "item") or ""
        source_id = item.rsplit("/", 1)[-1]
        image = _val(row, "image")
        if not source_id or not image:
            continue

        job.found += 1
        raw_title = _val(row, "itemLabel") or ""
        # a bare QID label means the work is untitled on Wikidata
        title = raw_title if raw_title and not _QID_RE.match(raw_title) else "Untitled"
        if library.source_exists(ID, source_id):
            job.skipped += 1
            continue

        # Judge the size Commons reports before spending the download on it.
        if min_px:
            wh = sizes.get(_file_of(image))
            if wh and max(wh) <= min_px:
                job.log("TOO SMALL \"%s\": best is %dx%d." % (title, wh[0], wh[1]))
                small.append(title)
                job.failed += 1
                continue

        date_text = (_val(row, "date") or "")[:10] or None  # trim SPARQL datetime
        meta = {
            "title": title,
            "date": date_text,
            "year": parse_year(date_text),
            "medium": _val(row, "materials") or None,
            "style": None,
            "type": "painting",
            "source": ID,
            "source_id": source_id,
            "source_url": "https://www.wikidata.org/wiki/%s" % source_id,
        }
        try:
            tmp = download_to_tmp(sess, image, referer="https://commons.wikimedia.org/",
                                  **hooks)
        except Exception as e:
            job.failed += 1
            job.log("FAILED \"%s\": %s" % (title, e))
            continue

        # Backstop for a file Commons didn't measure in the batch above: check the
        # bytes we actually got, and drop a small one rather than hang it blown up.
        if min_px:
            got = _long_side(tmp)
            if got and got <= min_px:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                job.log("TOO SMALL \"%s\": %d px on the long side." % (title, got))
                small.append(title)
                job.failed += 1
                continue

        path = library.save_work(artist, meta, tmp, job)
        job.saved += 1
        job.log("Saved: %s" % path.name)
        if max_items and job.saved >= max_items:
            job.log("Reached the requested maximum of %d works." % max_items)
            return
        time.sleep(0.4)

    if small:
        job.log("Not available above %d px (%d): %s"
                % (min_px, len(small), "; ".join(small)[:400]))
