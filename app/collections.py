"""Collections: a curator's hand-picked, ordered list of works. Stored one JSON
file per collection under data/collections/<id>.json. Membership is by work id
(the sha1 of the file's library-relative path); if a work is later deleted or its
artist renamed — either of which changes the id — it simply drops out, handled by
resolve_works. Every collection is visible to anyone who can browse; only its
creating curator (or any owner) may edit it."""
import json
import re
import secrets
import threading
import time

from . import config, library
from .names import artist_sort_key, fold

_lock = threading.RLock()
_ID_RE = re.compile(r"^[a-f0-9]{6,32}$")

_MAX_TITLE = 120
_MAX_DESC = 2000


# ---------------- store ----------------

def _path(cid):
    if not _ID_RE.match(cid or ""):
        return None
    return config.COLLECTIONS_DIR / (cid + ".json")


def _read(cid):
    p = _path(cid)
    if not p or not p.exists():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return rec if isinstance(rec, dict) else None


def _write(rec):
    rec["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _path(rec["id"]).write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return rec


def _all():
    out = []
    for p in config.COLLECTIONS_DIR.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and rec.get("id"):
                out.append(rec)
        except Exception:
            continue
    return out


# ---------------- validation ----------------

def _clean_title(title):
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        raise ValueError("A collection needs a title.")
    return t[:_MAX_TITLE]


def _clean_desc(desc):
    return (desc or "").strip()[:_MAX_DESC]


def _owner_role(rec):
    """The curator's role as it stands now, for the byline pill. Looked up rather
    than frozen into the record: promote someone to Owner and their collections
    should say so, and a deleted account shouldn't claim a role at all."""
    from . import auth
    u = auth.get_user(rec.get("owner") or "")
    return u.get("role") if u else None


# ---------------- work resolution ----------------

# How a collection hangs. The order is part of the curation, not a viewer's
# preference: it's the walk the viewer takes and the three works the card shows,
# so it belongs to the collection and everyone sees it the same way.
#
# "added" is the manual hang -- the stored list itself, in whatever order the
# curator put it in. It starts as the order the works were gathered in and is
# theirs to rearrange (see reorder). The rest are computed lenses over that list
# and never overwrite it, so returning to "added" always restores the hand-made
# order exactly.
SORTS = ("added", "artist", "year", "year_desc", "title")
DEFAULT_SORT = "added"


def clean_sort(s):
    s = (s or "").strip()
    return s if s in SORTS else DEFAULT_SORT


def _apply_sort(works, sort):
    """Undated works sort last whichever way the years run — flipping the order
    shouldn't march the ones we know nothing about to the front."""
    if sort == "artist":
        works.sort(key=lambda w: (artist_sort_key(w["artist"]), w["year"] or 9999,
                                  fold(w["title"])))
    elif sort == "year":
        works.sort(key=lambda w: (w["year"] is None, w["year"] or 0,
                                  artist_sort_key(w["artist"]), fold(w["title"])))
    elif sort == "year_desc":
        works.sort(key=lambda w: (w["year"] is None, -(w["year"] or 0),
                                  artist_sort_key(w["artist"]), fold(w["title"])))
    elif sort == "title":
        works.sort(key=lambda w: (fold(w["title"]), artist_sort_key(w["artist"])))
    return works                      # "added" is the stored order: leave it alone


def resolve_works(rec):
    """The collection's works as full dicts, in the order it hangs in, silently
    skipping ids whose file no longer exists.

    The stored list is the curator's own order — the one arrangement that can't be
    worked out from the paintings themselves. Every other sort is a lens over it and
    leaves it untouched, so choosing the manual hang again restores exactly what
    they built."""
    out = []
    for wid in rec.get("work_ids", []):
        w = library.get(wid)
        if w:
            out.append(w)
    return _apply_sort(out, rec.get("sort"))


def can_edit(rec, user):
    """Owners can edit any collection; a curator can edit only their own."""
    if not user or not rec:
        return False
    if user.get("role") == "owner":
        return True
    return rec.get("owner") == (user.get("username") or "").strip().casefold()


# ---------------- views ----------------

def summary(rec):
    """Index-card view: covers + count reflect works that still exist. `covers` is
    up to three ids — the card draws them as a mosaic; `cover` stays for callers
    that just want the lead image.

    Ordered the same way the collection hangs, so the card leads with the works
    you actually meet first rather than whatever was gathered first."""
    works = resolve_works(rec)
    covers = [w["id"] for w in works[:3]]
    return {
        "id": rec.get("id"),
        "title": rec.get("title"),
        "description": rec.get("description") or "",
        "owner_display": rec.get("owner_display"),
        "owner_role": _owner_role(rec),
        "count": len(works),
        "cover": covers[0] if covers else None,
        "covers": covers,
        "sort": clean_sort(rec.get("sort")),
        "updated": rec.get("updated"),
    }


def detail(rec, user):
    works = resolve_works(rec)
    return {
        "id": rec.get("id"),
        "title": rec.get("title"),
        "description": rec.get("description") or "",
        "owner_display": rec.get("owner_display"),
        "created": rec.get("created"),
        "updated": rec.get("updated"),
        "works": works,
        "count": len(works),
        "sort": clean_sort(rec.get("sort")),
        "can_edit": can_edit(rec, user),
    }


def count_containing_artist(name):
    """How many collections hold at least one work by this artist — the artist
    page's 'IN COLLECTIONS' figure."""
    key = (name or "").strip().casefold()
    n = 0
    for rec in _all():
        if any((library.get(wid) or {}).get("artist", "").strip().casefold() == key
               for wid in rec.get("work_ids", [])):
            n += 1
    return n


def list_summaries(user=None):
    recs = _all()
    recs.sort(key=lambda r: (r.get("title") or "").casefold())
    out = []
    for r in recs:
        s = summary(r)
        s["can_edit"] = can_edit(r, user)
        out.append(s)
    return out


# ---------------- mutations ----------------

def get_collection(cid):
    return _read(cid)


def all_records():
    """Every collection as stored, with membership unresolved. For the publisher,
    which wants the raw work ids rather than a resolved, sorted view."""
    return _all()


def import_published(rec):
    """Write a collection pulled from the content repo, keyed by its own id.

    A collection id is minted once and travels, so a re-pull corrects a retitled or
    re-hung collection in place instead of hanging a second copy. `work_ids` are
    expected to have been mapped back to this box's ids already — the publisher
    sends membership as pids, which are the only ids both boxes agree on.

    Returns "added", "updated" or "unchanged"."""
    cid = (rec.get("id") or "").strip()
    if not _ID_RE.match(cid):
        raise ValueError("Bad collection id: %r" % cid)
    with _lock:
        cur = _read(cid)
        new = {
            "id": cid,
            "title": _clean_title(rec.get("title")),
            "description": _clean_desc(rec.get("description")),
            "owner": (rec.get("owner") or "").strip().casefold(),
            "owner_display": rec.get("owner_display"),
            "work_ids": [w for w in rec.get("work_ids") or [] if w],
            "sort": clean_sort(rec.get("sort")),
            "source": "published",
            "created": (cur or {}).get("created") or time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if cur and all(cur.get(k) == new[k] for k in
                       ("title", "description", "owner", "owner_display", "work_ids")) \
                and clean_sort(cur.get("sort")) == new["sort"]:
            return "unchanged"
        _write(new)
        return "updated" if cur else "added"


def prune_published(keep_ids):
    """Delete imported collections the content repo no longer carries — retired
    at the source. Only ever touches records marked source == "published"; a
    curator's own collections on this box are theirs, whatever the repo does.
    Returns how many were removed."""
    keep = set(keep_ids or ())
    n = 0
    with _lock:
        for rec in _all():
            if rec.get("source") != "published" or rec.get("id") in keep:
                continue
            p = _path(rec["id"])
            if p and p.exists():
                p.unlink()
                n += 1
    return n


def create_collection(title, description, user):
    title = _clean_title(title)
    description = _clean_desc(description)
    with _lock:
        cid = secrets.token_hex(6)
        while _path(cid).exists():
            cid = secrets.token_hex(6)
        rec = {
            "id": cid,
            "title": title,
            "description": description,
            "owner": (user.get("username") or "").strip().casefold(),
            "owner_display": user.get("username"),
            "work_ids": [],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return _write(rec)


def update_collection(cid, title=None, description=None, sort=None):
    with _lock:
        rec = _read(cid)
        if not rec:
            raise ValueError("No such collection.")
        if title is not None:
            rec["title"] = _clean_title(title)
        if description is not None:
            rec["description"] = _clean_desc(description)
        if sort is not None:
            rec["sort"] = clean_sort(sort)
        return _write(rec)


def add_works(cid, ids):
    with _lock:
        rec = _read(cid)
        if not rec:
            raise ValueError("No such collection.")
        have = set(rec.get("work_ids", []))
        for wid in ids:
            if wid and wid not in have:
                rec.setdefault("work_ids", []).append(wid)
                have.add(wid)
        return _write(rec)


def reorder(cid, ids):
    """Hang the collection in exactly this order.

    Only ever a rearrangement of what's already there: ids the collection doesn't
    hold are ignored, and anything the curator's screen didn't name keeps its place
    at the end rather than vanishing — someone adding a work from another tab while
    a drag is in progress shouldn't cost anyone a painting.

    Arranging by hand only means something in manual mode, so this drops the
    collection into it. Dragging a painting while the collection hangs by year is a
    request to take the wall back from the year, starting from what's on screen."""
    with _lock:
        rec = _read(cid)
        if not rec:
            raise ValueError("No such collection.")
        have = rec.get("work_ids", [])
        held = set(have)
        seen, order = set(), []
        for wid in ids or []:
            if wid in held and wid not in seen:
                seen.add(wid)
                order.append(wid)
        order += [wid for wid in have if wid not in seen]
        rec["work_ids"] = order
        rec["sort"] = DEFAULT_SORT
        return _write(rec)


def remove_works(cid, ids):
    drop = set(ids or [])
    with _lock:
        rec = _read(cid)
        if not rec:
            raise ValueError("No such collection.")
        rec["work_ids"] = [w for w in rec.get("work_ids", []) if w not in drop]
        return _write(rec)


def remap_works(id_map):
    """Follow works whose id changed because their file moved — an artist edit or a
    repoint.

    A collection stores work ids, and an id is the sha1 of the file's path, so
    correcting a painter's spelling re-identifies every painting of theirs. Without
    this they fall out of every collection holding them: still in the library, just
    silently off the wall, which reads as the collection having lost them. A work
    that was genuinely deleted is a different matter and is left to drop out.

    Returns the number of collections rewritten."""
    id_map = {k: v for k, v in (id_map or {}).items() if k and v and v != k}
    if not id_map:
        return 0
    n = 0
    with _lock:
        for rec in _all():
            ids = rec.get("work_ids") or []
            moved = [id_map.get(w, w) for w in ids]
            if moved != ids:
                rec["work_ids"] = moved
                _write(rec)
                n += 1
    return n


def delete_collection(cid):
    with _lock:
        p = _path(cid)
        if not p or not p.exists():
            raise ValueError("No such collection.")
        p.unlink()
        return True
