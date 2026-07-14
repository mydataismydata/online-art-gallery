"""National Gallery of Art, Washington — full-resolution CC0 open-access works,
built from the NGA's official Open Data program (https://github.com/NationalGalleryOfArt/opendata).

NGA has no live search API that yields full-resolution open-access images — its
website search only serves ~900px derivatives. So this source downloads the open
data CSVs once, distils them into a small local index of open-access works keyed
for artist lookup, and caches it. Later runs reuse the cache and pull the full
image straight from NGA's IIIF endpoint.
"""
import csv
import json
import os
import time

from ... import config, library
from ...names import name_match
from ..util import session, request_with_retries, download_to_tmp
from . import tuning

ID = "nga"
LABEL = "National Gallery of Art (Washington)"
HINT = ("Full-resolution open-access (CC0) works by an artist, from the NGA's open "
        "data. The first run downloads NGA's ~170 MB catalogue once (progress is "
        "shown below); after that, runs are quick. Use the artist's full name.")
PLACEHOLDER = "Artist name, e.g. Vincent van Gogh"
SUPPORTS_MAX_PX = True
MAX_PX_DEFAULT = None  # native/full resolution unless a Max size is given

IIIF = "https://api.nga.gov/iiif"
DATA = "https://raw.githubusercontent.com/NationalGalleryOfArt/opendata/main/data"
OBJECTS_CSV = DATA + "/objects.csv"
IMAGES_CSV = DATA + "/published_images.csv"

ENDPOINTS = (("Open data", "https://github.com/NationalGalleryOfArt/opendata"),
             ("IIIF images", IIIF))
CONFIG = [
    {"key": "type_keywords", "label": "Accepted classifications", "type": "text", "default": "painting",
     "help": "Comma-separated keywords matched against each work's NGA classification "
             "(e.g. 'painting', 'drawing'). Blank accepts every open-access type."},
    {"key": "refresh_days", "label": "Catalogue refresh (days)", "type": "int", "default": 30, "min": 1, "max": 365,
     "help": "How stale the cached catalogue may get before it's re-downloaded. NGA "
             "updates the open data daily; 30 days keeps it current without refetching often."},
]

_CACHE_DIR = config.CACHE_DIR / "nga"
_INDEX = _CACHE_DIR / "catalog.json"


def availability():
    return True, ""


# ---------------- catalogue download + index build ----------------

def _download_csv(job, sess, url, dest, label):
    job.log("Downloading NGA %s…" % label)
    r = request_with_retries(sess, url, stream=True, timeout=120)
    r.raise_for_status()
    # NGA's raw.githubusercontent host serves gzip, so Content-Length is the
    # compressed size — not comparable to the decompressed bytes we count here.
    got, next_mark = 0, 25 * 1024 * 1024
    try:
        with open(str(dest), "wb") as f:
            for chunk in r.iter_content(1 << 16):
                if job.cancelled:
                    raise RuntimeError("Cancelled during catalogue download.")
                if chunk:
                    f.write(chunk)
                    got += len(chunk)
                    if got >= next_mark:
                        job.log("  %s: %d MB…" % (label, got // (1 << 20)))
                        next_mark += 25 * 1024 * 1024
    finally:
        r.close()


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _build_catalog(job, sess):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_img = _CACHE_DIR / "published_images.csv.tmp"
    tmp_obj = _CACHE_DIR / "objects.csv.tmp"
    try:
        _download_csv(job, sess, IMAGES_CSV, tmp_img, "image catalogue (published_images.csv)")
        _download_csv(job, sess, OBJECTS_CSV, tmp_obj, "object catalogue (objects.csv)")

        job.log("Building the open-access index…")
        # objectid -> (iiifurl, width, height) for its primary open-access image
        images = {}
        with open(str(tmp_img), newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("openaccess") != "1" or (row.get("viewtype") or "") != "primary":
                    continue
                oid = (row.get("depictstmsobjectid") or "").strip()
                iiifurl = (row.get("iiifurl") or "").strip()
                if not oid or not iiifurl or oid in images:
                    continue
                images[oid] = (iiifurl, _int_or_none(row.get("width")) or 0,
                               _int_or_none(row.get("height")) or 0)

        works = []
        with open(str(tmp_obj), newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                img = images.get((row.get("objectid") or "").strip())
                attribution = (row.get("attribution") or "").strip()
                if not img or not attribution:
                    continue
                works.append({
                    "objectid": (row.get("objectid") or "").strip(),
                    "title": (row.get("title") or "").strip() or "Untitled",
                    "date": (row.get("displaydate") or "").strip() or None,
                    "year": _int_or_none(row.get("beginyear")),
                    "medium": (row.get("medium") or "").strip() or None,
                    "classification": (row.get("classification") or "").strip(),
                    "attribution": attribution,
                    "iiif": img[0], "w": img[1], "h": img[2],
                })
        if not works:
            raise RuntimeError("NGA open data parsed to zero works — the CSV format may have changed.")

        payload = {"built": time.time(), "count": len(works), "works": works}
        tmp_index = _CACHE_DIR / "catalog.json.tmp"
        tmp_index.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp_index), str(_INDEX))
        job.log("Catalogue ready: %d open-access works with images." % len(works))
        return works
    finally:
        for t in (tmp_img, tmp_obj):
            try:
                if t.exists():
                    t.unlink()
            except OSError:
                pass


def _load_catalog(job, sess, refresh_days):
    if _INDEX.exists():
        data = None
        try:
            data = json.loads(_INDEX.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if data and data.get("works"):
            age = time.time() - (data.get("built") or 0)
            if age <= refresh_days * 86400:
                job.log("Using cached NGA catalogue (%d works, %d days old)."
                        % (data.get("count") or len(data["works"]), int(age // 86400)))
                return data["works"]
            job.log("Cached NGA catalogue is %d days old; refreshing…" % int(age // 86400))
    return _build_catalog(job, sess)


# ---------------- run ----------------

def _image_urls(w, max_px):
    """Best image URL first, with smaller fallbacks if the server rejects it."""
    base = w["iiif"].rstrip("/")
    urls = []
    if max_px:
        # cap the longer side so the result fits in a max_px box (IIIF level 1)
        dim = ("%d," % max_px) if (w["w"] or 0) >= (w["h"] or 0) else (",%d" % max_px)
        urls.append("%s/full/%s/0/default.jpg" % (base, dim))
    urls.append("%s/full/full/0/default.jpg" % base)
    urls.append("%s/full/2000,/0/default.jpg" % base)
    urls.append("%s/full/1200,/0/default.jpg" % base)
    return urls


def run(job):
    sess = session()
    cfg = tuning.effective(ID, CONFIG)
    keywords = [k.strip().lower() for k in cfg["type_keywords"].split(",") if k.strip()]
    works = _load_catalog(job, sess, cfg["refresh_days"])
    if job.cancelled:
        return
    max_items = job.opts.get("max_items")
    max_px = job.opts.get("max_px")

    def typed(w):
        if not keywords:
            return True
        cl = (w.get("classification") or "").lower()
        return any(k in cl for k in keywords)

    matched = [w for w in works if typed(w) and name_match(job.query, w["attribution"])]
    job.log("Catalogue holds %d open-access works; %d match \"%s\"."
            % (len(works), len(matched), job.query))
    for w in matched:
        if job.cancelled:
            return
        job.found += 1
        if library.source_exists(ID, w["objectid"]):
            job.skipped += 1
            continue
        meta = {
            "title": w["title"],
            "date": w["date"],
            "year": w["year"],
            "medium": w["medium"],
            "type": "painting",
            "source": ID,
            "source_id": w["objectid"],
            "source_url": None,
        }
        tmp = None
        for url in _image_urls(w, max_px):
            try:
                tmp = download_to_tmp(sess, url)
                break
            except Exception:
                continue
        if tmp is None:
            job.failed += 1
            job.log("FAILED \"%s\": no NGA image size worked" % w["title"])
            continue
        path = library.save_work(w["attribution"], meta, tmp, job)
        job.saved += 1
        job.log("Saved: %s" % path.name)
        if max_items and job.saved >= max_items:
            job.log("Reached the requested maximum of %d works." % max_items)
            return
        time.sleep(0.4)
