"""A list of works, from a CSV — one row per painting, rather than one artist.

Every other source here answers "everything by X". A museum's own export asks a
different question: these thirty things, in this room. This source takes that list
and fetches exactly those, which is the difference between "all of Delacroix" and
"Salle Mollien".

Each row is resolved by the strongest identifier it carries: an explicit image URL,
a Wikidata QID, a Louvre ARK id (P9394), and only failing all of those, the artist
and title together.

Choosing WHICH file to take is the whole job. A Commons category is a grab-bag: it
holds the frame, the back, details, the room the painting hangs in, and sometimes a
different object of the same name. "Take the largest file" — the obvious rule, and
the one to resist — was measured against the Louvre's Salle Mollien list and
returned a picture frame for Liberty Leading the People, a photograph of an 1855
exhibition catalogue for Ingres' Jeanne d'Arc, and the interior of a church in
Finland for Prud'hon's Christ on the Cross. All three are the biggest file in the
right category.

So a file is taken only when something positively identifies it AS this work:
Wikidata names it (P18), or its filename carries this work's inventory number and
no one else's. Everything else is left alone, and a work with no good file is
reported rather than filled in with something that looked close.
"""
import csv
import hashlib
import io
import re
import time
from pathlib import Path

from PIL import Image

from ... import config, library
from ...names import normalize_comma_name, parse_year, unshout
from ..util import session, fetch_json, download_to_tmp
from . import tuning

ID = "worklist"
LABEL = "Work list (CSV)"
HINT = ("Imports a list of individual works rather than an artist's whole output. "
        "Choose a CSV and the browser hands it over — a museum's own room export works "
        "as-is. Rows are matched by an image_url, wikidata or ark column if present, "
        "else by artist + title. Anything smaller than the minimum size is reported, "
        "not saved.")
PLACEHOLDER = "…or a URL, or a path on the gallery server"
# The file is read in the browser and posted as text: the CSV is wherever the
# person is sitting, which is rarely where the gallery is running.
ACCEPTS_FILE = True
FILE_ACCEPT = ".csv,.tsv,text/csv"
QUERY_LABEL = "CSV file"

WDQS = "https://query.wikidata.org/sparql"
COMMONS = "https://commons.wikimedia.org/w/api.php"
ENDPOINTS = (("Wikidata SPARQL", WDQS), ("Commons API", COMMONS))

CONFIG = [
    {"key": "min_px", "label": "Minimum pixels on the long side", "type": "int",
     "default": config.VIEW_MAX, "min": 0, "max": 30000,
     "help": "A row whose best image is no bigger than this is reported and skipped "
             "rather than saved small. Defaults to the viewer's own size "
             "(GALLERY_VIEW_MAX), the point below which a painting is being blown up "
             "to hang."},
    {"key": "max_rows", "label": "Max rows per file", "type": "int", "default": 500,
     "min": 1, "max": 5000, "help": "Guard against pointing this at a 40,000-row export."},
]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Header spellings seen in the wild. Matched on letters and digits only, so
# "Object name/Title", "object_name_title" and "OBJECT NAME / TITLE" all land.
_COLS = {
    "image": ("imageurl", "image", "fileurl", "url", "media", "iiif"),
    "qid": ("wikidata", "qid", "wikidataid", "item", "wikidataitem"),
    "ark": ("ark", "arkid"),
    "artist": ("artist", "author", "creator", "painter", "attribution", "artistname"),
    "title": ("title", "objectnametitle", "objecttitle", "work", "workname", "objectname"),
    "date": ("date", "year", "dated", "displaydatecreated", "datecreated"),
    "inv": ("inventorynumber", "inv", "objectnumber", "accessionnumber", "invno"),
}

# A school packed in beside the painter: "Géricault, Théodore ; France".
_SCHOOLS = {"france", "italie", "espagne", "paysbas", "angleterre", "allemagne",
            "flandres", "hollande", "ecolefrancaise", "italy", "spain", "netherlands"}

# Not the painting: its frame, its back, a detail, a technical plate, the room.
_REJECT = re.compile(r"avec cadre|with frame|cadre seul|frame only|d[ée]tail|"
                     r"verso|recto[- ]verso|infrarouge|infrared|radiograph|ultraviolet|"
                     r"avant restaur|before restor|en cours de|\(cropped\)|montage|"
                     r"salle |gallery view|mus[ée]e vu|exposition|expo \d", re.I)

_INV = re.compile(r"\b(INV|RF|MI|MR|LP|MN|RFML|OA|MNR)[\s.]*(\d+(?:\.\d+)*)", re.I)
_QID = re.compile(r"\bQ\d+\b")


def _inv_tokens(s):
    """{'inv4884', 'c51'} — inventory numbers with case and punctuation flattened."""
    return {(m.group(1) + m.group(2)).replace(".", "").lower() for m in _INV.finditer(s or "")}


def _artist_of(raw):
    """'Géricault, Théodore ; France' -> 'Théodore Géricault'. Exports pack the
    school in beside the painter, in either order, and sometimes a second hand."""
    for part in (raw or "").split(";"):
        p = " ".join(part.split())
        if not p or _norm(p) in _SCHOOLS:
            continue
        return unshout(normalize_comma_name(p))
    return ""


def _read_rows(spec, limit, text=None):
    """The CSV: handed over by the browser, or fetched from a URL, or read from a
    path on the server. The delimiter is sniffed — museum exports are as often
    semicolon-separated as comma."""
    if text is None:
        spec = (spec or "").strip().strip('"')
        if not spec:
            raise RuntimeError("Choose a CSV file, or give a URL or server path.")
        if re.match(r"^https?://", spec, re.I):
            r = session().get(spec, timeout=60)
            r.raise_for_status()
            raw = r.content
        else:
            p = Path(spec)
            if not p.is_file():
                raise RuntimeError(
                    "No CSV file at %s. That path has to exist on the machine running "
                    "the gallery — if the file is on your own computer, use the file "
                    "picker instead." % p)
            raw = p.read_bytes()
        text = raw.decode("utf-8-sig", "replace")
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rdr = csv.DictReader(io.StringIO(text), dialect=dialect)
    cols = {}
    for raw_h in (rdr.fieldnames or []):
        n = _norm(raw_h)
        for key, aliases in _COLS.items():
            if n in aliases and key not in cols:
                cols[key] = raw_h
    if not cols:
        raise RuntimeError("None of that CSV's columns look like a work list. Wanted one "
                           "of: image_url, wikidata, ark, or artist + title.")
    out = []
    for row in rdr:
        item = {k: " ".join((row.get(col) or "").split()) for k, col in cols.items()}
        if not any(item.values()):
            continue
        item["artist"] = _artist_of(item.get("artist"))
        if item.get("qid"):
            m = _QID.search(item["qid"])          # accepts a bare Q123 or a wikidata URL
            item["qid"] = m.group(0) if m else ""
        out.append(item)
        if len(out) >= limit:
            break
    return out, cols


def _sparql(sess, query):
    return fetch_json(sess, WDQS, {"query": query, "format": "json"}, timeout=90)


def _bind(rows, key):
    return [(b.get(key) or {}).get("value") for b in rows]


def _arks_to_qids(sess, arks):
    """Louvre ARK -> Wikidata item. The Louvre writes 'cl010059198'; Wikidata stores
    the same id bare, as '010059198'. Ask for both rather than bet on one."""
    vals = " ".join('"%s" "%s"' % (a, a[2:] if a.lower().startswith("cl") else a) for a in arks)
    q = "SELECT ?ark ?item WHERE { VALUES ?ark { %s } ?item wdt:P9394 ?ark }" % vals
    out = {}
    for b in _sparql(sess, q)["results"]["bindings"]:
        ark, item = b["ark"]["value"], b["item"]["value"].rsplit("/", 1)[-1]
        out[ark] = item
        out["cl" + ark] = item
    return out


def _items(sess, qids):
    """P18, inventory number, Commons category and label for each item, in one query."""
    q = """SELECT ?item ?itemLabel ?image ?inv ?cat ?date ?creatorLabel WHERE {
      VALUES ?item { %s }
      OPTIONAL { ?item wdt:P18 ?image }
      OPTIONAL { ?item wdt:P217 ?inv }
      OPTIONAL { ?item wdt:P373 ?cat }
      OPTIONAL { ?item wdt:P571 ?date }
      OPTIONAL { ?item wdt:P170 ?creator }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en,fr". }
    }""" % " ".join("wd:" + q for q in qids)
    out = {}
    for b in _sparql(sess, q)["results"]["bindings"]:
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        rec = out.setdefault(qid, {"p18": None, "inv": set(), "cat": None,
                                   "label": None, "date": None, "creator": None})
        if b.get("image"):
            rec["p18"] = _file_of(b["image"]["value"])
        if b.get("inv"):
            rec["inv"].add(b["inv"]["value"])
        for k, f in (("cat", "cat"), ("date", "date")):
            if b.get(f) and not rec[k]:
                rec[k] = b[f]["value"]
        for k, f in (("label", "itemLabel"), ("creator", "creatorLabel")):
            if b.get(f) and not rec[k]:
                rec[k] = b[f]["value"]
    return out


def _file_of(url):
    """A Commons Special:FilePath URL -> the file's page title."""
    import urllib.parse
    return urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace("_", " ")


def _commons(sess, params):
    return fetch_json(sess, COMMONS, dict(params, format="json", formatversion="2"), timeout=60)


def _sizes(sess, files):
    """{filename: (w, h, url)} from the Commons imageinfo API — so a too-small file is
    ruled out before a byte of it is downloaded."""
    out = {}
    files = sorted(set(files))
    for i in range(0, len(files), 40):
        chunk = files[i:i + 40]
        try:
            r = _commons(sess, {"action": "query", "prop": "imageinfo", "iiprop": "url|size|mime",
                                "titles": "|".join("File:" + f for f in chunk)})
        except Exception:
            continue
        for p in (r.get("query") or {}).get("pages") or []:
            ii = (p.get("imageinfo") or [{}])[0]
            if ii.get("width") and (ii.get("mime") or "").startswith("image/"):
                out[p["title"].split(":", 1)[1]] = (ii["width"], ii["height"], ii["url"])
    return out


def _category_files(sess, cat):
    try:
        r = _commons(sess, {"action": "query", "list": "categorymembers", "cmtype": "file",
                            "cmtitle": "Category:" + cat, "cmlimit": "60"})
        return [m["title"].split(":", 1)[1]
                for m in (r.get("query") or {}).get("categorymembers") or []]
    except Exception:
        return []


def _pick(p18, cat_files, want_inv, sizes):
    """The largest file that is positively identified as this work. See the module
    docstring for why 'largest in the category' is not that."""
    cands = []
    if p18 and p18 in sizes:
        cands.append(p18)
    for f in cat_files:
        if f == p18 or f not in sizes:
            continue
        got = _inv_tokens(f)
        if not want_inv or not (got & want_inv):
            continue        # nothing ties this file to this work
        if got - want_inv:
            continue        # it names another work too — two paintings in one shot
        cands.append(f)
    cands = [f for f in cands if not _REJECT.search(f)]
    if not cands:
        return None
    return max(cands, key=lambda f: sizes[f][0] * sizes[f][1])


def _search_qid(sess, title, artist):
    """Last resort for a row with no identifier: a work of this title whose creator
    matches. Bounded search, and a unique hit or nothing — a near-miss here would
    hang the wrong painting."""
    if not title or not artist:
        return None
    r = fetch_json(sess, "https://www.wikidata.org/w/api.php",
                   {"action": "wbsearchentities", "search": title[:120], "language": "en",
                    "uselang": "en", "type": "item", "limit": 10, "format": "json"})
    ids = [h["id"] for h in (r.get("search") or [])]
    if not ids:
        return None
    q = """SELECT ?item ?creatorLabel WHERE {
      VALUES ?item { %s }
      ?item wdt:P31/wdt:P279* wd:Q3305213 ; wdt:P170 ?creator .
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en,fr". }
    }""" % " ".join("wd:" + i for i in ids)
    want = {w for w in re.split(r"[^\w]+", unshout(artist).casefold()) if len(w) > 2}
    hits = []
    for b in _sparql(sess, q)["results"]["bindings"]:
        cl = (b.get("creatorLabel") or {}).get("value", "").casefold()
        if want and all(w in cl for w in want):
            hits.append(b["item"]["value"].rsplit("/", 1)[-1])
    hits = list(dict.fromkeys(hits))
    return hits[0] if len(hits) == 1 else None


def _long_side(path):
    try:
        with Image.open(str(path)) as im:
            return max(im.size)
    except Exception:
        return 0


def run(job):
    sess = session()
    cfg = tuning.effective(ID, CONFIG)
    min_px = job.opts.get("min_px") or cfg["min_px"]
    rows, cols = _read_rows(job.query, cfg["max_rows"], job.opts.get("csv_text"))
    job.log("Read %d row%s; using columns: %s."
            % (len(rows), "" if len(rows) == 1 else "s",
               ", ".join("%s=%s" % (k, v) for k, v in sorted(cols.items()))))
    if min_px:
        job.log("Skipping anything %d px or smaller on the long side." % min_px)

    # Resolve identifiers in bulk: two queries, not two per row.
    arks = [r["ark"] for r in rows if r.get("ark") and not r.get("qid") and not r.get("image")]
    if arks:
        try:
            found = _arks_to_qids(sess, arks)
            for r in rows:
                if r.get("ark") and not r.get("qid"):
                    r["qid"] = found.get(r["ark"]) or ""
            job.log("Matched %d of %d ARK ids on Wikidata." %
                    (sum(1 for r in rows if r.get("ark") and r.get("qid")), len(arks)))
        except Exception as e:
            job.log("ARK lookup failed (%s); falling back to title matching." % e)

    for r in rows:
        if job.cancelled:
            return
        if not r.get("qid") and not r.get("image") and r.get("title"):
            r["qid"] = _search_qid(sess, r["title"], r.get("artist")) or ""
            if r["qid"]:
                job.log("Matched by title: \"%s\" -> %s" % (r["title"][:60], r["qid"]))

    qids = [r["qid"] for r in rows if r.get("qid")]
    meta = _items(sess, qids) if qids else {}

    # Every Commons candidate, measured in batches before anything is fetched.
    cands = {}
    for r in rows:
        m = meta.get(r.get("qid") or "")
        if not m:
            continue
        want = _inv_tokens(" ".join(m["inv"]) + " " + (r.get("inv") or ""))
        cat = _category_files(sess, m["cat"]) if (m["cat"] and want) else []
        cands[id(r)] = (m, want, cat)
    sizes = _sizes(sess, [f for m, w, cat in cands.values() for f in ([m["p18"]] if m["p18"] else []) + cat])

    # A row that resolves to nothing, or to nothing big enough, is not a crash but
    # it isn't a work either. Both land in the job's `failed` tally, so account for
    # them by name at the end rather than leaving "11 failed" to look like breakage.
    small, missing = [], []
    max_items = job.opts.get("max_items")
    for r in rows:
        if job.cancelled:
            return
        job.found += 1
        title = r.get("title") or (meta.get(r.get("qid") or "") or {}).get("label") or "Untitled"
        artist = r.get("artist") or (meta.get(r.get("qid") or "") or {}).get("creator") or "Unknown Artist"
        qid = r.get("qid") or ""
        # This lands in the saved filename, so a row identified only by its URL gets
        # a short digest of it rather than 60 characters of percent-encoded path.
        source_id = qid or r.get("ark") or (
            "url-" + hashlib.sha1(r["image"].encode("utf-8")).hexdigest()[:12]
            if r.get("image") else "")

        # The by-artist Wikidata source files works under the same QID, so a work
        # pulled that way is already here even though the source name differs.
        if library.source_exists(ID, source_id) or (qid and library.source_exists("wikidata", qid)):
            job.skipped += 1
            continue

        # An explicit URL is the curator's own decision and outranks anything we
        # could work out; otherwise take the best file this work can be proved to own.
        url, note = None, ""
        if r.get("image"):
            url, note = r["image"], "listed URL"
        elif id(r) in cands:
            m, want, cat = cands[id(r)]
            pick = _pick(m["p18"], cat, want, sizes)
            if pick:
                w, h, url = sizes[pick]
                if max(w, h) <= min_px:
                    job.log("TOO SMALL \"%s\": best is %dx%d." % (title[:52], w, h))
                    small.append(title)
                    job.failed += 1
                    continue
                note = "%dx%d" % (w, h)
        if not url:
            job.log("NO IMAGE \"%s\": nothing on Wikidata or Commons identifies this work."
                    % title[:52])
            missing.append(title)
            job.failed += 1
            continue

        try:
            tmp = download_to_tmp(sess, url, referer="https://commons.wikimedia.org/")
        except Exception as e:
            job.failed += 1
            job.log("FAILED \"%s\": %s" % (title[:52], e))
            continue
        # A listed URL's size isn't known until it's here.
        got = _long_side(tmp)
        if min_px and got <= min_px:
            try:
                tmp.unlink()
            except OSError:
                pass
            small.append(title)
            job.failed += 1
            job.log("TOO SMALL \"%s\": %d px on the long side." % (title[:52], got))
            continue

        m = meta.get(qid) or {}
        date_text = r.get("date") or (m.get("date") or "")[:10] or None
        work = {
            "title": title,
            "date": date_text,
            "year": parse_year(date_text),
            "type": "painting",
            "source": ID,
            "source_id": source_id,
            "source_url": ("https://www.wikidata.org/wiki/%s" % qid) if qid else (r.get("image") or None),
        }
        path = library.save_work(artist, work, tmp, job)
        job.saved += 1
        job.log("Saved: %s%s" % (path.name, (" (%s)" % note) if note else ""))
        if max_items and job.saved >= max_items:
            job.log("Reached the requested maximum of %d works." % max_items)
            return
        time.sleep(0.3)

    if small:
        job.log("Not available above %d px (%d): %s" % (min_px, len(small), "; ".join(small)[:400]))
    if missing:
        job.log("No image anywhere (%d): %s" % (len(missing), "; ".join(missing)[:400]))
