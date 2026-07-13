"""Wikidata + Wikimedia Commons — public-domain artworks by an artist, keyless.

Resolves the typed name to a Wikidata entity, then asks the Wikidata Query Service
(SPARQL) for that creator's paintings that carry an image (P18), and downloads the
full-resolution original from Wikimedia Commons. No API key, and coverage is enormous
(virtually every notable painter) — the trade-off is crowd-sourced metadata, so titles
and dates vary in polish. Docs: https://query.wikidata.org/"""
import re
import time

from ... import library, artistinfo
from ...names import parse_year
from ..util import session, fetch_json, download_to_tmp
from . import tuning

ID = "wikidata"
LABEL = "Wikidata / Wikimedia Commons"
HINT = ("Looks the artist up on Wikidata, then downloads the full-resolution public-"
        "domain images of their paintings from Wikimedia Commons. Vast coverage and no "
        "API key — metadata is crowd-sourced, so quality varies. Use the artist's full "
        "name, e.g. 'Rembrandt van Rijn'.")
PLACEHOLDER = "Artist name, e.g. Claude Monet"

WDQS = "https://query.wikidata.org/sparql"
_LIMIT = 500  # per-artist cap on works pulled from SPARQL

ENDPOINTS = (("SPARQL endpoint", WDQS),)
CONFIG = [
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


def run(job):
    sess = session()
    cfg = tuning.effective(ID, CONFIG)
    limit = cfg["max_works"]
    job.log("Identifying \"%s\" on Wikidata…" % job.query)
    qid, label = artistinfo.resolve_qid(job.query)
    if not qid:
        raise RuntimeError("Couldn't confidently match that name to an artist on Wikidata. "
                           "Try the artist's full name, e.g. 'Rembrandt van Rijn'.")
    artist = label or job.query
    job.log("Matched %s (%s); querying their paintings…" % (artist, qid))

    query = _SPARQL % {"qid": qid, "limit": limit}
    data = fetch_json(sess, WDQS, {"query": query, "format": "json"}, timeout=90)
    rows = (data.get("results") or {}).get("bindings") or []
    job.log("Wikidata lists %d painting%s with an image%s."
            % (len(rows), "" if len(rows) == 1 else "s",
               " (capped)" if len(rows) >= limit else ""))

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
            tmp = download_to_tmp(sess, image, referer="https://commons.wikimedia.org/")
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
        time.sleep(0.4)
