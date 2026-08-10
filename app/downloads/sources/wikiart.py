"""WikiArt — an artist's works from wikiart.org, keyless.

WikiArt is its own curated art encyclopedia, separate from Wikidata/Wikimedia:
where that source pulls public-domain paintings a museum has photographed, WikiArt
carries a broader, more modern span with its own imagery and metadata. The artist's
own page URL already holds the key — the slug in
`wikiart.org/en/<slug>/all-works` — so a full name (slugified the same way, e.g.
'Claude Monet' -> 'claude-monet') or a pasted page URL both resolve. Works arrive a
page at a time (WikiArt serves 20 per page) and each carries its own id, which keys
deduplication so an artist's whole run comes through, not just the first piece.

Note: WikiArt hosts in-copyright as well as public-domain works — mind the rights
before republishing what you pull."""
import re
import time

import requests

from ... import library
from ...names import parse_year, slugify
from ..util import session, fetch_json, download_to_tmp, job_hooks
from . import tuning

ID = "wikiart"
LABEL = "WikiArt"
HINT = ("Pulls an artist's works from wikiart.org. Type the artist's full name "
        "(e.g. 'Fortunino Matania') or paste their WikiArt page URL. Broad, modern "
        "coverage with WikiArt's own images and metadata — mind that it includes "
        "in-copyright works, not only public-domain ones.")
PLACEHOLDER = "Artist name or WikiArt URL"

BASE = "https://www.wikiart.org"
_PAGE_SIZE = 20                      # WikiArt serves 20 works per all-works page

ENDPOINTS = (("all-works JSON", BASE + "/en/{artist}/all-works?json=2&page=1"),)
CONFIG = [
    {"key": "max_works", "label": "Max works per artist", "type": "int",
     "default": 500, "min": 10, "max": 2000,
     "help": "Upper bound on how many of an artist's works to pull."},
]

_URL_SLUG_RE = re.compile(r"wikiart\.org/[a-z]{2}/([^/?#]+)", re.I)


def _slug(query):
    """The WikiArt artist slug from what the owner typed: a pasted page URL
    carries it outright, otherwise the name slugifies the way WikiArt's own
    URLs do ('Albrecht Dürer' -> 'albrecht-durer')."""
    m = _URL_SLUG_RE.search(query or "")
    return m.group(1).lower() if m else slugify(query)


def run(job):
    sess = session()
    hooks = job_hooks(job, "WikiArt")
    cfg = tuning.effective(ID, CONFIG)
    limit = cfg["max_works"]
    slug = _slug(job.query)
    if not slug or slug == "unknown":
        raise RuntimeError("Type the artist's name or paste their WikiArt page URL.")
    job.log("Looking up \"%s\" on WikiArt (%s)…" % (job.query, slug))

    max_items = job.opts.get("max_items")
    artist = job.query
    # A generous page ceiling from the work cap, so a runaway never loops.
    max_pages = min(200, limit // _PAGE_SIZE + 2)
    seen = 0

    for page in range(1, max_pages + 1):
        if job.cancelled:
            return
        url = "%s/en/%s/all-works?json=2&page=%d" % (BASE, slug, page)
        try:
            data = fetch_json(sess, url, timeout=45, **hooks)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if page == 1 and code == 404:
                raise RuntimeError(
                    "No WikiArt artist at '%s'. Check the spelling, or paste the "
                    "artist's WikiArt page URL." % slug)
            job.log("Page %d request failed (HTTP %s); stopping." % (page, code))
            break

        paintings = (data.get("Paintings") if isinstance(data, dict) else None) or []
        if page == 1:
            total = data.get("AllPaintingsCount")
            job.log("WikiArt lists %s work%s for %s."
                    % (total if total is not None else len(paintings),
                       "" if total == 1 else "s",
                       (paintings[0].get("artistName") if paintings else slug)))
        if not paintings:
            break
        if paintings:
            artist = paintings[0].get("artistName") or artist

        for p in paintings:
            if job.cancelled:
                return
            sid = str(p.get("id") or "").strip()
            image = p.get("image")
            if not sid or not image:
                continue
            job.found += 1
            if library.source_exists(ID, sid):
                job.skipped += 1
                continue

            title = (p.get("title") or "").strip() or "Untitled"
            yr = p.get("year")
            year = int(yr) if str(yr).isdigit() else parse_year(str(yr or ""))
            purl = p.get("paintingUrl") or ""
            meta = {
                "title": title,
                "date": str(year) if year else None,
                "year": year,
                "medium": None,
                "style": None,
                "type": "painting",
                "source": ID,
                "source_id": sid,
                "source_url": (BASE + purl) if purl.startswith("/") else (purl or None),
            }
            try:
                tmp = download_to_tmp(sess, str(image), referer=BASE + "/", **hooks)
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

        seen += len(paintings)
        if seen >= limit:
            job.log("Reached the %d-work cap for this source." % limit)
            break
        if len(paintings) < _PAGE_SIZE:
            break                    # a short page is the last one
        time.sleep(0.3)
