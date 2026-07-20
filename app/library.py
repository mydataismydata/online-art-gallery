"""The library: a folder tree of images (library/<Artist>/<work>.jpg) plus optional
JSON sidecars (<work>.jpg.json) carrying metadata. Everything is rescanned from disk
with a short TTL, so files copied in by hand show up automatically."""
import hashlib
import json
import re
import shutil
import threading
import time
from collections import Counter, OrderedDict
from html import unescape
from pathlib import Path

from PIL import Image

from . import config
from .names import (safe_name, era_from, parse_year, clean_title_text,
                    artist_sort_key, strip_diacritics, particle_case_score,
                    fold, diacritic_count)

# The gallery is built around enormous images; don't let Pillow refuse to read them.
Image.MAX_IMAGE_PIXELS = None

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_lock = threading.RLock()
_state = {"scanned_at": 0.0, "works": [], "by_id": {}, "src_ids": set()}
_TTL = 2.0

# Cache each file's (width, height) so repeated scans don't re-open images.
# Keyed by rel path -> (mtime_int, (w, h) | None).
_dim_cache = {}


def _image_dims(path, rel, mtime):
    m = int(mtime)
    cached = _dim_cache.get(rel)
    if cached and cached[0] == m:
        return cached[1]
    dims = None
    try:
        with Image.open(str(path)) as im:
            dims = im.size  # read from the header; no full decode
    except Exception:
        dims = None
    _dim_cache[rel] = (m, dims)
    return dims

_MARKER_RE = re.compile(r"\s*\[([a-z]+)-([^\]\s]+)\]\s*$")


def invalidate():
    with _lock:
        _state["scanned_at"] = 0.0


def _parse_stem(stem):
    """Parse 'Title (1875) [met-123]' or 'Title; 1875' -> (title, date, source, source_id)."""
    source = source_id = None
    m = _MARKER_RE.search(stem)
    if m:
        source, source_id = m.group(1), m.group(2)
        stem = stem[: m.start()].rstrip()
    title, date_text = stem, None
    if ";" in stem:
        parts = [p.strip() for p in stem.split(";")]
        title = parts[0]
        rest = "; ".join(p for p in parts[1:] if p)
        date_text = rest or None
    else:
        m2 = re.search(r"\((\d{4}[^)]*)\)\s*$", stem)
        if m2:
            title = stem[: m2.start()].strip()
            date_text = m2.group(1)
    return clean_title_text(title), date_text, source, source_id


def work_id_for(rel):
    """The id the scanner gives a work at this library-relative path. A work's id
    is a hash of where it lives, so moving it renames it — anything holding an id
    across a move has to be told the new one."""
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]


def _work_from_file(path, artist_dir_name):
    rel = path.relative_to(config.LIBRARY_DIR).as_posix()
    wid = work_id_for(rel)
    meta = {}
    sidecar = Path(str(path) + ".json")
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    f_title, f_date, f_source, f_sid = _parse_stem(path.stem)
    title = meta.get("title") or f_title or path.stem
    date_text = meta.get("date") or f_date
    year = meta.get("year") or parse_year(date_text)
    st = path.stat()
    dims = _image_dims(path, rel, st.st_mtime)
    return {
        "id": wid,
        "rel": rel,
        # Persistent publish id: stamped on first "push to public", it survives
        # artist/title edits (which change `id`) so re-pushes update the same public
        # work instead of duplicating it. Absent until a work has been published.
        "pid": meta.get("pid"),
        "width": dims[0] if dims else None,
        "height": dims[1] if dims else None,
        "artist": meta.get("artist") or artist_dir_name,
        "title": title,
        "date": date_text,
        "year": year,
        "era": era_from(year, date_text),
        "medium": meta.get("medium"),
        # Three separate axes so each is browsable on its own: style = the movement
        # or manner (Baroque), genre = the subject category (Marine painting),
        # school = the national/regional school (Dutch, Heidelberg School).
        "style": meta.get("style"),
        "genre": meta.get("genre"),
        "school": meta.get("school"),
        "description": meta.get("description"),
        "type": meta.get("type") or "painting",
        "source": meta.get("source") or f_source,
        "source_id": str(meta.get("source_id")) if meta.get("source_id") is not None else f_sid,
        # Where the file came from is workshop paperwork: it belongs to the box
        # that does the collecting. The public snapshot is a gallery, and a wall
        # label there doesn't cite the warehouse — so the link is dropped from the
        # payload rather than hidden in the page, and nothing renders it because
        # nothing receives it. The sidecar still holds it (a pull writes it), so
        # this is a matter of what's served, not what's kept.
        "source_url": None if config.PUBLIC else meta.get("source_url"),
        "mtime": st.st_mtime,
        "size": st.st_size,
    }


def _pick_artist_name(counter):
    """Choose the display spelling among variants of one artist: the most accented
    wins, since an ASCII source drops accents but no source invents them; then the
    most common; then correctly-lowercased particles ('van Dyck' over 'Van Dyck');
    then alphabetical so the pick is stable across scans."""
    return sorted(counter.items(),
                  key=lambda kv: (-diacritic_count(kv[0]), -kv[1],
                                  -particle_case_score(kv[0]), kv[0]))[0][0]


def _canonicalize_artists(works):
    """Collapse artist spellings that differ only in case or accents onto one name.
    Museums credit the same painter inconsistently ('Anthony van Dyck' vs 'Anthony
    Van Dyck', 'Théodore Géricault' vs 'Theodore Gericault' from a source that
    couldn't carry the accents), which would otherwise split them into two artists.
    Done here so every consumer — cards, grids, AI batching, placards — sees one
    identity."""
    variants = {}
    for w in works:
        name = (w["artist"] or "").strip()
        variants.setdefault(fold(name), Counter())[name] += 1
    canon = {k: _pick_artist_name(c) for k, c in variants.items()}
    for w in works:
        w["artist"] = canon[fold((w["artist"] or "").strip())]


# The titles a placard italicises. House style marks up every artwork title — the
# AI that writes these is told to — so the marks are already in the prose, waiting
# to be read.
_EM_RE = re.compile(r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def text_of(html):
    """What a curator actually wrote, with the markup taken off.

    Prose is held as markup, so measuring it means measuring the words: counting
    tags would let bold eat someone's allowance. It's also how to tell an empty
    editor from a full one — left alone, one still hands back a stray <br>."""
    return unescape(_TAG_RE.sub(" ", html or "")).strip()


def title_index(works=None):
    """{folded title: work id} for every painting its title names on its own.

    A title two paintings share ("Portrait of a Young Girl" belongs to both Corot
    and Courbet in one real catalogue) is left out: sending a reader to a coin-flip
    is worse than leaving the words as words. Short or generic titles are out for
    the same reason — they'd match ordinary prose rather than a painting.

    Pass `works` from inside a scan; it defaults to asking for one."""
    works = all_works() if works is None else works
    keys = [fold(w["title"]) for w in works]
    seen = Counter(keys)
    out = {}
    for w, k in zip(works, keys):
        if seen[k] == 1 and len(k) >= 4 and k != "untitled":
            out[k] = w["id"]
    return out


def xref_in(html, index, exclude=None):
    """{title as written: work id} for the italicised titles in `html` that name a
    painting we hold. `exclude` drops a self-reference — a placard needn't point at
    its own painting.

    Always derived on read, never stored: it follows the library, so retitling a
    painting silently fixes every text that mentions it, and a work that leaves
    takes its links with it. The ids are local, which is also why this must never
    be published — the public box works its own out from its own library."""
    if not html or "<" not in html:
        return {}
    out = {}
    for m in _EM_RE.finditer(html):
        text = _TAG_RE.sub("", m.group(1)).strip()
        wid = index.get(fold(text))
        if wid and wid != exclude:
            out[text] = wid
    return out


def _link_descriptions(works):
    """Resolve each placard's italicised artwork titles to the works they name."""
    index = title_index(works)
    for w in works:
        out = xref_in(w.get("description") or "", index, exclude=w["id"])
        if out:
            w["xref"] = out


def scan(force=False):
    with _lock:
        if not force and time.time() - _state["scanned_at"] < _TTL:
            return _state
        works = []
        root = config.LIBRARY_DIR
        if root.exists():
            for artist_dir in sorted(root.iterdir()):
                if not artist_dir.is_dir() or artist_dir.name.startswith("."):
                    continue
                for f in sorted(artist_dir.rglob("*")):
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                        try:
                            works.append(_work_from_file(f, artist_dir.name))
                        except Exception as e:
                            print("scan: skipping %s (%s)" % (f, e), flush=True)
        _canonicalize_artists(works)
        _link_descriptions(works)
        works.sort(key=lambda w: (artist_sort_key(w["artist"]), w["year"] or 9999, w["title"].casefold()))
        _state["works"] = works
        _state["by_id"] = {w["id"]: w for w in works}
        _state["src_ids"] = {
            "%s-%s" % (w["source"], w["source_id"])
            for w in works
            if w["source"] and w["source_id"]
        }
        _state["scanned_at"] = time.time()
        return _state


def get(wid):
    return scan()["by_id"].get(wid)


def all_works():
    return scan()["works"]


def _matches(value, wanted):
    if wanted is None:
        return True
    have = (value or "Unknown").strip().casefold()
    return have == wanted.strip().casefold()


def query_works(artist=None, era=None, medium=None, style=None, genre=None,
                school=None, q=None):
    out = []
    for w in all_works():
        if artist is not None and fold(w["artist"]) != fold(artist):
            continue
        if not _matches(w["era"], era):
            continue
        if not _matches(w["medium"], medium):
            continue
        if not _matches(w["style"], style):
            continue
        if not _matches(w["genre"], genre):
            continue
        if not _matches(w["school"], school):
            continue
        # Fold both sides: someone typing "Theo" or "Gericault" without reaching for
        # the accents is still looking for Géricault.
        if q and fold(q) not in fold(w["title"] + " " + w["artist"]):
            continue
        out.append(w)
    return out


def cover_id(name, ws):
    """The representative thumbnail for an artist's works: the owner-chosen cover
    (set from the artist page) when it still exists, otherwise the first work."""
    from . import artistinfo  # local import avoids an import cycle at module load
    meta = artistinfo.load(name)
    chosen = meta.get("cover") if meta else None
    if chosen and any(w["id"] == chosen for w in ws):
        return chosen
    return ws[0]["id"]


def artists():
    groups = OrderedDict()
    for w in all_works():
        groups.setdefault(w["artist"], []).append(w)
    out = []
    for name, ws in groups.items():
        years = [w["year"] for w in ws if w["year"]]
        out.append({
            "name": name,
            "count": len(ws),
            "cover": cover_id(name, ws),
            "year_min": min(years) if years else None,
            "year_max": max(years) if years else None,
        })
    # plain A-Z on the displayed name ("Arthur Streeton" under A, not S)
    out.sort(key=lambda a: strip_diacritics(a["name"]).casefold())
    return out


def stats():
    """Totals for the Settings screen: artist/image counts, the total bytes of the
    stored images, and free/total space on the library's filesystem."""
    ws = all_works()
    free = disk_total = None
    try:
        du = shutil.disk_usage(str(config.LIBRARY_DIR))
        free, disk_total = du.free, du.total
    except OSError:
        pass
    return {
        "artists": len({w["artist"] for w in ws}),
        "images": len(ws),
        "images_bytes": sum(w.get("size") or 0 for w in ws),
        "disk_free": free,
        "disk_total": disk_total,
    }


def _era_sort_key(pair):
    m = re.match(r"(\d+)", pair["value"])
    return (int(m.group(1)) if m else 999, pair["value"])


def facets():
    result = {}
    for key in ("era", "medium", "style", "genre", "school"):
        counter = Counter()
        display = {}
        for w in all_works():
            v = (w.get(key) or "Unknown").strip()
            cf = v.casefold()
            # variants differing only in case count together; show the capitalized one
            if cf not in display or (display[cf][:1].islower() and v[:1].isupper()):
                display[cf] = v
            counter[cf] += 1
        items = [{"value": display[cf], "count": n} for cf, n in counter.items()]
        if key == "era":
            items.sort(key=_era_sort_key)
        else:
            items.sort(key=lambda p: (-p["count"], p["value"].casefold()))
        result[key] = items
    return result


def source_exists(source, source_id):
    """True if a work with this source marker is already in the library."""
    return ("%s-%s" % (source, source_id)) in scan()["src_ids"]


def _trash_work(w):
    """Move a work's image and sidecar into the trash dir, preserving the artist
    subfolder and stamping the name so nothing collides. Returns the new image path."""
    src = config.LIBRARY_DIR / w["rel"]
    rel = Path(w["rel"])
    dest_dir = config.TRASH_DIR / rel.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / ("%s  %s" % (stamp, rel.name))
    n = 2
    while dest.exists():
        dest = dest_dir / ("%s  %s  (%d)%s" % (stamp, rel.stem, n, rel.suffix))
        n += 1
    shutil.move(str(src), str(dest))
    sidecar = Path(str(src) + ".json")
    if sidecar.exists():
        shutil.move(str(sidecar), str(dest) + ".json")
    return dest


def delete_works(ids):
    """Move the given works to the trash. Returns (deleted_ids, errors)."""
    st = scan()
    deleted, errors = [], []
    for wid in ids:
        w = st["by_id"].get(wid)
        if not w:
            errors.append({"id": wid, "error": "not found"})
            continue
        try:
            dest = _trash_work(w)
            deleted.append(wid)
            print("trashed %s -> %s" % (w["rel"], dest), flush=True)
        except Exception as e:
            errors.append({"id": wid, "error": str(e)})
    if deleted:
        invalidate()
    return deleted, errors


def _set_sidecar_artist(image_path, artist):
    """Write artist into a work's sidecar, creating the sidecar if absent so the
    work groups by metadata rather than by whatever folder it happens to sit in."""
    sc = Path(str(image_path) + ".json")
    data = {}
    if sc.exists():
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["artist"] = artist
    sc.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def rename_artist(sources, target):
    """Consolidate every work whose artist matches one of `sources` under the
    canonical `target`: relocate its image + sidecar into target's folder and set
    the sidecar's artist field. Renaming to an existing artist thus merges them.
    Returns (moved, errors, id_map), where id_map is {old work id: new work id} —
    the move changes each work's id, and callers holding one need to follow it."""
    target = re.sub(r"\s+", " ", target or "").strip()
    if not target:
        raise ValueError("target name required")
    src_set = {re.sub(r"\s+", " ", s).strip().casefold() for s in sources}
    src_set.discard("")
    src_set.discard(target.casefold())  # nothing to do for works already canonical
    target_folder = config.LIBRARY_DIR / safe_name(target, 80)
    target_folder.mkdir(parents=True, exist_ok=True)

    st = scan(force=True)
    moved, errors, touched = 0, [], set()
    id_map = {}
    for w in list(st["works"]):
        if w["artist"].strip().casefold() not in src_set:
            continue
        src = config.LIBRARY_DIR / w["rel"]
        touched.add(src.parent)
        try:
            dest = target_folder / src.name
            if dest.resolve() != src.resolve():
                n = 2
                while dest.exists():
                    dest = target_folder / ("%s (%d)%s" % (src.stem, n, src.suffix))
                    n += 1
                shutil.move(str(src), str(dest))
                sc = Path(str(src) + ".json")
                if sc.exists():
                    shutil.move(str(sc), str(dest) + ".json")
            _set_sidecar_artist(dest, target)
            # The move renames the work (its id hashes its path). Report where each
            # one went, so callers holding an id can follow it rather than guess.
            id_map[w["id"]] = work_id_for(dest.relative_to(config.LIBRARY_DIR).as_posix())
            moved += 1
        except Exception as e:
            errors.append({"rel": w["rel"], "error": str(e)})

    for d in touched:  # tidy up now-empty source folders
        try:
            if d != target_folder and d.exists() and not any(d.iterdir()):
                d.rmdir()
        except Exception:
            pass
    invalidate()
    return moved, errors, id_map


def update_work(wid, fields):
    """Edit one work's sidecar metadata (title/artist/date/medium/description).
    If the artist changed, relocate the image + sidecar into the new artist's
    folder (which changes the work's id). Returns the updated work dict."""
    st = scan(force=True)
    w = st["by_id"].get(wid)
    if not w:
        raise KeyError("work not found")
    src = config.LIBRARY_DIR / w["rel"]
    if not src.exists():
        raise KeyError("file missing")
    sc = Path(str(src) + ".json")
    data = {}
    if sc.exists():
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    def _clean(v):
        return re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v

    if "title" in fields:
        data["title"] = _clean(fields["title"]) or data.get("title") or w["title"]
    if "date" in fields:
        d = _clean(fields.get("date")) or None
        data["date"] = d
        data["year"] = parse_year(d)
    if "medium" in fields:
        data["medium"] = _clean(fields.get("medium")) or None
    for k in ("style", "genre", "school"):
        if k in fields:
            data[k] = _clean(fields.get(k)) or None
    if "description" in fields:
        desc = fields.get("description")
        data["description"] = desc.strip() if isinstance(desc, str) and desc.strip() else None

    dest = src
    new_artist = _clean(fields.get("artist")) if "artist" in fields else None
    if new_artist and new_artist.casefold() != (w["artist"] or "").strip().casefold():
        data["artist"] = new_artist
        folder = config.LIBRARY_DIR / safe_name(new_artist, 80)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / src.name
        n = 2
        while dest.exists() and dest.resolve() != src.resolve():
            dest = folder / ("%s (%d)%s" % (src.stem, n, src.suffix))
            n += 1
        old_parent = src.parent
        shutil.move(str(src), str(dest))
        if sc.exists():
            try:
                sc.unlink()
            except Exception:
                pass
        try:
            if old_parent != folder and old_parent.exists() and not any(old_parent.iterdir()):
                old_parent.rmdir()
        except Exception:
            pass
    elif new_artist:
        data["artist"] = new_artist

    Path(str(dest) + ".json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    invalidate()
    st2 = scan(force=True)
    new_rel = dest.relative_to(config.LIBRARY_DIR).as_posix()
    new_id = hashlib.sha1(new_rel.encode("utf-8")).hexdigest()[:16]
    return st2["by_id"].get(new_id)


def update_works_meta(updates):
    """Apply non-relocating metadata (date/medium/style/description/title) to many
    works at once, writing each sidecar and rescanning only once. `updates` maps a
    work id to a {field: value} dict. Returns the count of works changed. (Artist
    isn't handled here — it would move files and change ids; use update_work.)"""
    st = scan()
    changed = 0
    for wid, fields in (updates or {}).items():
        w = st["by_id"].get(wid)
        if not w:
            continue
        src = config.LIBRARY_DIR / w["rel"]
        if not src.exists():
            continue
        sc = Path(str(src) + ".json")
        data = {}
        if sc.exists():
            try:
                data = json.loads(sc.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        touched = False
        for k, v in fields.items():
            if k == "date":
                d = re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v
                data["date"] = d or None
                data["year"] = parse_year(d)
            elif k in ("medium", "style", "genre", "school", "title"):
                cv = re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v
                data[k] = cv or None
            elif k == "description":
                data["description"] = v.strip() if isinstance(v, str) and v.strip() else None
            else:
                continue
            touched = True
        if touched:
            sc.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            changed += 1
    if changed:
        invalidate()
    return changed


def _artist_folder(artist_name):
    """Where an artist's works live. Reuses a folder that differs only by case or
    accents, so a work credited 'Anthony Van Dyck' or 'Theodore Gericault' files in
    with the existing 'Anthony van Dyck' or 'Théodore Géricault' instead of splitting
    the painter across two folders on disk."""
    want = safe_name(artist_name, 80)
    root = config.LIBRARY_DIR
    if root.exists():
        target = fold(want)
        hits = [d for d in root.iterdir()
                if d.is_dir() and not d.name.startswith(".") and fold(d.name) == target]
        if hits:
            # Where several spellings already exist on disk, file under the best one
            # — the same accented-wins rule the displayed name uses, so the folder
            # and the artist agree rather than depending on directory order.
            return sorted(hits, key=lambda d: (-diacritic_count(d.name), d.name))[0]
    return root / want


def save_work(artist, meta, tmp_path, job=None):
    """Move a downloaded temp file into the library and write its sidecar.
    Returns the final path. If a download `job` is given, records the artist the
    work was filed under so the UI can link to that artist afterwards."""
    artist_name = re.sub(r"\s+", " ", artist or "").strip() or "Unknown Artist"
    folder = _artist_folder(artist_name)
    folder.mkdir(parents=True, exist_ok=True)

    ext = Path(str(tmp_path)).suffix.lower() or ".jpg"
    if ext == ".jpeg":
        ext = ".jpg"
    bits = meta.get("title") or "Untitled"
    if meta.get("year"):
        bits += " (%s)" % meta["year"]
    if meta.get("source") and meta.get("source_id") is not None:
        bits += " [%s-%s]" % (meta["source"], meta["source_id"])
    path = folder / (safe_name(bits) + ext)
    n = 2
    while path.exists():
        path = folder / (safe_name(bits) + " (%d)%s" % (n, ext))
        n += 1

    shutil.move(str(tmp_path), str(path))
    sidecar = dict(meta)
    sidecar["artist"] = artist_name
    sidecar["saved"] = time.strftime("%Y-%m-%d %H:%M:%S")
    Path(str(path) + ".json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    with _lock:
        if meta.get("source") and meta.get("source_id") is not None:
            _state["src_ids"].add("%s-%s" % (meta["source"], meta["source_id"]))
    if job is not None:
        job.record_artist(artist_name)
    return path
