"""The Metropolitan Museum of Art Open Access — CC0, full-resolution originals, no key.
https://metmuseum.github.io/"""
import time

from ... import library
from ...names import name_match, parse_year
from ..util import session, fetch_json, download_to_tmp

ID = "met"
LABEL = "The Met (Open Access)"
HINT = ("Searches the Metropolitan Museum of Art's CC0 open-access collection for "
        "public-domain paintings by the artist and downloads the original files.")
PLACEHOLDER = "Artist name, e.g. Johannes Vermeer"

BASE = "https://collectionapi.metmuseum.org/public/collection/v1"


def run(job):
    sess = session()
    data = fetch_json(sess, BASE + "/search", {
        "artistOrCulture": "true",
        "hasImages": "true",
        "q": job.query,
    })
    ids = data.get("objectIDs") or []
    job.log("Met search returned %d objects; checking each for public-domain paintings…" % len(ids))
    if len(ids) > 4000:
        job.log("Capping scan at the first 4000 objects.")
        ids = ids[:4000]

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

        if not obj.get("isPublicDomain"):
            continue
        # The Met leaves `classification` blank on many paintings but sets
        # objectName to "Painting" — so check both, or we reject everything.
        kind = ((obj.get("classification") or "") + " " + (obj.get("objectName") or "")).lower()
        if "painting" not in kind:
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
