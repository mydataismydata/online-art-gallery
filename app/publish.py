"""Publishing the gallery as a public 'snapshot'.

Two directions, both talking to a dedicated git 'content' repo (kept separate from
the code repo):

  * publish_works(ids)   -- run on the LOCAL box (private mode). Copies each selected
    work's reduced-size (<=VIEW_MAX) WebP plus a completed placard into <repo>/works/,
    then commits and pushes. Each work is stamped with a persistent `pid`, so
    re-pushing a fixed placard or a better image updates the same public work
    instead of duplicating it. Artist bios (<repo>/artists/), collections
    (<repo>/collections/) and the curator's connections — hand-written links
    (<repo>/links/) and threads (<repo>/threads/) — go along with them.

  * pull_and_import()    -- run on the PUBLIC box (GALLERY_PUBLIC=1). git-pulls the
    repo and imports every works/<pid>.json into the local library, as if the work
    had been added by hand but with its placard already filled. Matching is by
    `pid`, so re-pulls update in place instead of duplicating.

Two ids matter here, and they are not the same thing. A work's `id` is the sha1 of
its path, so it is per-box and changes if the file ever moves; a `pid` is minted
once and stamped into the sidecar. Anything that has to name a painting across the
gap -- an artist's cover, a collection's membership -- travels as a pid and is
resolved back to a local id on arrival.

We shell out to `git` and rely on each box's own credentials (a normal remote on
the local box, a read-only deploy key on the VPS); the app never handles tokens.
"""
import hashlib
import json
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from . import config, library, thumbs, artistinfo, links, threads
from . import collections as coll
from .names import safe_name, parse_year, slugify, fold

WORKS_SUBDIR = "works"
ARTISTS_SUBDIR = "artists"
COLLECTIONS_SUBDIR = "collections"
LINKS_SUBDIR = "links"
THREADS_SUBDIR = "threads"

# Everything that isn't a work travels the same way: one deterministic JSON file
# per record, named by the record's own id, in its own directory. `_sync` writes
# one only when its bytes actually change, which is what keeps the repo quiet —
# re-exporting an untouched gallery stages nothing at all.
_EXTRAS = ("bios", "collections", "links", "threads")

# Placard fields carried to the public site: everything the viewer shows plus
# provenance. Width/height are intentionally omitted -- the public work's real
# dimensions come from the (reduced) image file itself when it's scanned.
_PLACARD_FIELDS = ("artist", "title", "date", "year", "medium", "style", "genre",
                   "school", "description", "type", "source", "source_url")

# Artist bio fields carried across. `updated` is deliberately not among them (it
# would churn the repo on every export) and neither is `cover` -- that's a local
# work id, and the same painting has a different id on the public box, so it
# travels as cover_pid instead.
_BIO_FIELDS = ("born", "died", "birthplace", "nationality", "movements",
               "description", "wikidata_id", "wikipedia_url")


# ---------------- repo location + config ----------------

def _load_cfg():
    try:
        data = json.loads(config.PUBLISH_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {}


def repo_path():
    """The content repo working tree: env override, else the saved Settings path,
    else a sibling of the project. Always returns a Path (which may not exist)."""
    if config.PUBLISH_REPO_ENV:
        return Path(config.PUBLISH_REPO_ENV).expanduser()
    saved = (_load_cfg().get("repo_path") or "").strip()
    if saved:
        return Path(saved).expanduser()
    return config.PUBLISH_REPO_DEFAULT


def set_repo_path(path):
    """Persist a Settings-chosen repo path (ignored when GALLERY_PUBLISH_REPO is set)."""
    p = (path or "").strip()
    cfg = _load_cfg()
    if p:
        cfg["repo_path"] = p
    else:
        cfg.pop("repo_path", None)
    config.PUBLISH_CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    return repo_status()


def _record_export(count):
    cfg = _load_cfg()
    cfg["last_export"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "count": count}
    config.PUBLISH_CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def last_export():
    rec = _load_cfg().get("last_export")
    return rec if isinstance(rec, dict) else None


def unpublished_works():
    """Everything imported since the last export: a work is 'new' until a publish
    stamps a pid into its sidecar, so this self-corrects (no timestamp bookkeeping)."""
    return [w for w in library.all_works() if not w.get("pid")]


def restated_works():
    """Published works whose placard no longer matches what the public site holds.

    "New" is pid-gated, and a published work keeps its pid forever — so a corrected
    placard would never appear in an export and would sit there looking done. This
    compares the fields only, never the image: it runs over the whole library on
    every Settings load, and rendering derivatives to compare shas would make that
    unbearable. A replaced image is still the explicit "Push to public" case."""
    repo = repo_path()
    if not _is_git_repo(repo):
        return []
    out = []
    for w in library.all_works():
        if not w.get("pid"):
            continue
        p = repo / WORKS_SUBDIR / (w["pid"] + ".json")
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue          # not published yet, or unreadable — not our business
        if any((w.get(f) or None) != (cur.get(f) or None) for f in _PLACARD_FIELDS):
            out.append(w)
    return out


# ---------------- pull suppression ----------------
# When the owner deletes a pulled work on the public server, its pid is remembered
# here so a later Pull doesn't re-import it (the pid is still in the content repo).

def suppressed_pids():
    return set(_load_cfg().get("suppressed") or [])


def suppress_pids(pids):
    add = [p for p in (pids or []) if p]
    if not add:
        return
    cfg = _load_cfg()
    cfg["suppressed"] = sorted(set(cfg.get("suppressed") or []) | set(add))
    config.PUBLISH_CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def _clean_suppressed(repo_pids):
    """A tombstone holds a Pull back from re-importing a work deleted on this
    box. Once the repo itself has dropped the pid there is nothing left to hold
    back — the stone comes up, or the list grows forever."""
    cfg = _load_cfg()
    cur = set(cfg.get("suppressed") or [])
    keep = sorted(cur & set(repo_pids))
    if len(keep) != len(cur):
        cfg["suppressed"] = keep
        config.PUBLISH_CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------- git plumbing ----------------

def _git(repo, *args, check=True, timeout=120):
    """Run a git command in `repo`; return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        capture_output=True, text=True, timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git failed").strip())
    return proc.returncode, proc.stdout, proc.stderr


def _is_git_repo(repo):
    if not repo.exists():
        return False
    try:
        code, out, _ = _git(repo, "rev-parse", "--is-inside-work-tree", check=False)
        return code == 0 and out.strip() == "true"
    except Exception:
        return False


def repo_status():
    """Summary for the Settings panel: where the repo is, whether it's usable, its
    remote + branch, how many works it holds, and whether the env var pinned the
    path (so the UI can disable editing)."""
    repo = repo_path()
    st = {
        "path": str(repo),
        "exists": repo.exists(),
        "is_git": False,
        "remote": None,
        "branch": None,
        "env_pinned": bool(config.PUBLISH_REPO_ENV),
        "works": None,
    }
    if _is_git_repo(repo):
        st["is_git"] = True
        _, out, _ = _git(repo, "remote", "get-url", "origin", check=False)
        st["remote"] = out.strip() or None
        _, out, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
        st["branch"] = out.strip() or None
    wd = repo / WORKS_SUBDIR
    if wd.is_dir():
        st["works"] = sum(1 for _ in wd.glob("*.json"))
    for key, sub in (("artists", ARTISTS_SUBDIR), ("collections", COLLECTIONS_SUBDIR),
                     ("links", LINKS_SUBDIR), ("threads", THREADS_SUBDIR)):
        d = repo / sub
        st[key] = sum(1 for _ in d.glob("*.json")) if d.is_dir() else None
    st["last_export"] = last_export()
    try:
        st["new_count"] = len(unpublished_works())
    except Exception:
        st["new_count"] = None
    try:
        st["placard_changes"] = len(restated_works())
    except Exception:
        st["placard_changes"] = None
    st["bio_changes"] = pending_bios()
    st["collection_changes"] = pending_collections()
    st["link_changes"] = pending_links()
    st["thread_changes"] = pending_threads()
    st["threads_held"] = held_threads()
    try:
        st["retire_count"] = len(retired_pids())
    except Exception:
        st["retire_count"] = None
    return st


def _require_repo():
    repo = repo_path()
    if not _is_git_repo(repo):
        raise RuntimeError(
            "No content repo at %s. Clone your private content repo there (or set "
            "its path in Settings) and try again." % repo)
    return repo


# ---------------- sidecar helpers ----------------

def _sidecar_path(work):
    return Path(str(config.LIBRARY_DIR / work["rel"]) + ".json")


def _load_sidecar(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_sidecar(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _ensure_pid(work):
    """The work's persistent publish id, minting one into the local sidecar on first
    publish. Reads the sidecar directly so a stale scan can't hand out a duplicate."""
    sc = _sidecar_path(work)
    data = _load_sidecar(sc)
    pid = work.get("pid") or data.get("pid")
    if not pid:
        pid = secrets.token_hex(8)
    if data.get("pid") != pid:
        data["pid"] = pid
        _save_sidecar(sc, data)
    return pid


# ---------------- artist bios ----------------

def _artist_blobs():
    """{slug: (name, json)} for every artist who has work on the public site and a
    bio worth sending. Deterministic bytes, so re-exporting an unchanged bio stages
    nothing and the repo stays quiet.

    Only artists with published work are included: a bio for a painter the public
    site has never heard of would import as an orphan."""
    pid_by_wid, names = {}, {}
    for w in library.all_works():
        if w.get("pid"):
            pid_by_wid[w["id"]] = w["pid"]
            names.setdefault((w.get("artist") or "").casefold(), w.get("artist"))

    out = {}
    for name in names.values():
        info = artistinfo.load(name) or {}
        rec = {"name": name}
        for f in _BIO_FIELDS:
            v = info.get(f)
            if v not in (None, "", []):
                rec[f] = v
        # The hand-made hang travels as pids, published works only: the private
        # box's ids mean nothing over there, and an unpublished painting simply
        # isn't in the public order — its neighbours keep their sequence.
        order = [pid_by_wid[wid] for wid in info.get("work_order") or []
                 if wid in pid_by_wid]
        if order:
            rec["work_order_pids"] = order
        if len(rec) == 1:                      # name only -- nothing to say
            continue
        cover = info.get("cover")
        if cover and cover in pid_by_wid:
            rec["cover_pid"] = pid_by_wid[cover]
        out[slugify(name)] = (
            name, json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True))
    return out


# ---------------- repo records: diff / sync / read ----------------

def _diff(repo, subdir, blobs):
    """(key, label, blob) for each record the repo doesn't already hold verbatim."""
    d = repo / subdir
    out = []
    for key, (label, blob) in blobs.items():
        p = d / (key + ".json")
        try:
            if p.read_text(encoding="utf-8") == blob:
                continue
        except OSError:
            pass
        out.append((key, label, blob))
    return out


def _sync(repo, subdir, blobs):
    """Write every changed record into the repo. Returns their labels."""
    changed = _diff(repo, subdir, blobs)
    if changed:
        (repo / subdir).mkdir(parents=True, exist_ok=True)
    for key, _label, blob in changed:
        (repo / subdir / (key + ".json")).write_text(blob, encoding="utf-8")
    return [label for _key, label, _blob in changed]


def _pending(subdir, blobs_fn):
    """How many records differ from what the public site holds. None if there's no
    usable repo to compare against."""
    repo = repo_path()
    if not _is_git_repo(repo):
        return None
    try:
        return len(_diff(repo, subdir, blobs_fn()))
    except Exception:
        return None


def _read_records(repo, subdir):
    """Every readable record in a repo directory. Unparseable files are skipped
    rather than failing the pull — one bad file shouldn't cost the whole import."""
    d = repo / subdir
    if not d.is_dir():
        return []
    out = []
    for jf in sorted(d.glob("*.json")):
        try:
            rec = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    return out


def _prune(repo, subdir, blobs):
    """Delete repo records their blob-builder no longer emits: a collection whose
    last published work was retired, a link whose painter left the walls, a bio
    with nothing left to say. The repo is wholly this box's export — anything in
    it we wouldn't write today is yesterday's export, not somebody else's data."""
    d = repo / subdir
    if not d.is_dir():
        return []
    keep = set(blobs)
    gone = []
    for jf in d.glob("*.json"):
        if jf.stem in keep:
            continue
        try:
            jf.unlink()
        except OSError:
            continue
        gone.append(jf.stem)
    return gone


def pending_bios():
    return _pending(ARTISTS_SUBDIR, _artist_blobs)


# ---------------- retiring deleted works ----------------

def retired_pids():
    """pids still in the content repo whose local work is gone — published once,
    deleted here since. On the next push their files leave the repo; the public
    box removes its copies on its next pull."""
    repo = repo_path()
    if not _is_git_repo(repo):
        return []
    wd = repo / WORKS_SUBDIR
    if not wd.is_dir():
        return []
    have = {w["pid"] for w in library.all_works() if w.get("pid")}
    return sorted(jf.stem for jf in wd.glob("*.json") if jf.stem not in have)


# ---------------- collections ----------------

def _collection_blobs():
    """{cid: (title, json)} for every collection with published work in it.

    Membership travels as pids. A work id is the sha1 of the file's path on this
    box, and the same painting sits at a different path on the public one, so the
    ids a collection stores are meaningless over there — the pid is the only name
    for a painting both boxes agree on.

    A collection is skipped entirely while none of its works are published: it
    would arrive as an empty room. Publish the paintings and it follows on its own,
    because the blob it produces changes the moment they have pids."""
    pid_by_wid = {w["id"]: w["pid"] for w in library.all_works() if w.get("pid")}
    out = {}
    for rec in coll.all_records():
        cid = rec.get("id") or ""
        # Only the works that have actually gone over. A half-published collection
        # hangs the half that's there and repairs itself on the next export.
        pids = [pid_by_wid[wid] for wid in rec.get("work_ids") or []
                if wid in pid_by_wid]
        if not cid or not pids:
            continue
        blob = {
            "id": cid,
            "title": rec.get("title") or "",
            "description": rec.get("description") or "",
            "owner": rec.get("owner") or "",
            "owner_display": rec.get("owner_display") or "",
            "sort": coll.clean_sort(rec.get("sort")),
            "work_pids": pids,
        }
        out[cid] = (rec.get("title") or cid,
                    json.dumps(blob, ensure_ascii=False, indent=1, sort_keys=True))
    return out


def pending_collections():
    return _pending(COLLECTIONS_SUBDIR, _collection_blobs)


# ---------------- connections: hand-written links + threads ----------------

def _published_artists():
    """Folded names of every painter with work on the public site."""
    return {fold(w["artist"]) for w in library.all_works()
            if w.get("pid") and w.get("artist")}


def _link_blobs():
    """{id: (label, json)} for every hand-written link between two published painters.

    Links travel by artist NAME. That's what identifies a painter everywhere else in
    this app, and unlike a work id it means the same thing on both boxes — the public
    library rebuilds its own ids, but Delacroix is Delacroix.

    Only the hand-written kinds are stored at all: movement, place & time and subject
    are recomputed on the far side from the bios and the works' own tags, both of
    which already travel, so they arrive for free. A link naming a painter the public
    site hasn't got would never draw — the graph only joins artists it holds — so it
    stays home rather than leaking the name of someone who isn't there."""
    have = _published_artists()
    out = {}
    for l in links.stored_links():
        a, b = l.get("a") or "", l.get("b") or ""
        if fold(a) not in have or fold(b) not in have:
            continue
        rec = {"id": l["id"]}
        for f in ("a", "b", "type", "note", "directed", "created_by", "created"):
            v = l.get(f)
            if v not in (None, "", False):
                rec[f] = v
        out[l["id"]] = ("%s — %s" % (a, b),
                        json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True))
    return out


def _thread_blobs():
    """{id: (title, json)} for threads whose every painter is on the public site.

    A thread is an argument, and an argument with a step cut out of the middle is
    worse than no argument at all — so unlike a link, a thread travels whole or not
    at all. One goes over the moment the last painter it names does."""
    have = _published_artists()
    out = {}
    for t in threads.all_records():
        steps = t.get("steps") or []
        if len(steps) < 2 or any(fold(s.get("artist") or "") not in have for s in steps):
            continue
        rec = {"id": t["id"],
               "steps": [{"artist": s["artist"], "note": s.get("note") or ""}
                         for s in steps]}
        for f in ("title", "description", "created_by", "created"):
            v = t.get(f)
            if v not in (None, ""):
                rec[f] = v
        out[t["id"]] = (t.get("title") or t["id"],
                        json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True))
    return out


def pending_links():
    return _pending(LINKS_SUBDIR, _link_blobs)


def pending_threads():
    return _pending(THREADS_SUBDIR, _thread_blobs)


def held_threads():
    """Threads waiting on a painter who hasn't been published yet. Worth counting
    separately: they aren't pending — there's nothing the owner can push — and
    without saying so their absence from the public site looks like a fault."""
    try:
        have = _published_artists()
    except Exception:
        return 0
    n = 0
    for t in threads.all_records():
        steps = t.get("steps") or []
        if len(steps) >= 2 and any(fold(s.get("artist") or "") not in have for s in steps):
            n += 1
    return n


# ---------------- push (local box) ----------------

def _placard(work, pid, image_name, sha):
    # Kept deterministic (no wall-clock stamp) so an unchanged work re-published
    # produces identical bytes -> no git churn. The "when" lives in the local
    # sidecar's published_at instead.
    p = {"pid": pid, "image": image_name, "sha": sha}
    for f in _PLACARD_FIELDS:
        v = work.get(f)
        if v not in (None, ""):
            p[f] = v
    return p


def publish_works(ids):
    """Copy the selected works' reduced images + placards into the content repo,
    commit and push. Returns a summary dict."""
    repo = _require_repo()
    works_dir = repo / WORKS_SUBDIR
    works_dir.mkdir(parents=True, exist_ok=True)

    published, pids, artists, errors = 0, [], [], []
    for wid in ids or []:
        w = library.get(wid)
        if not w:
            errors.append({"id": wid, "error": "not found"})
            continue
        try:
            pid = _ensure_pid(w)
            deriv = thumbs.view_for(w)                     # cached <=VIEW_MAX WebP
            data = Path(deriv).read_bytes()
            sha = hashlib.sha1(data).hexdigest()
            (works_dir / (pid + ".webp")).write_bytes(data)
            _save_sidecar(works_dir / (pid + ".json"),
                          _placard(w, pid, pid + ".webp", sha))
            sc = _sidecar_path(w)                           # note it locally too
            sd = _load_sidecar(sc)
            sd["pid"] = pid
            sd["published_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_sidecar(sc, sd)
            published += 1
            pids.append(pid)
            if w.get("artist") and w["artist"] not in artists:
                artists.append(w["artist"])
        except Exception as e:
            errors.append({"id": wid, "error": str(e)})

    # The sidecars just gained pids; drop the scan cache so those works stop
    # counting as "new" straight away -- and so the bio sync below sees them as
    # published and carries their artists across.
    if published:
        library.invalidate()

    # Reconcile deletions: a work deleted here after being published leaves the
    # repo now, and the public box drops its copy on the next pull. Guarded
    # against a library that failed to mount — an empty scan must never read as
    # "the owner deleted everything" and strip the public gallery bare.
    retired = []
    try:
        orphans = retired_pids()
        if orphans and not any(w.get("pid") for w in library.all_works()):
            errors.append({"id": "retire", "error":
                "Every published work looks missing locally, so nothing was "
                "retired. If the library really is meant to be empty, clear "
                "works/ in the content repo by hand."})
            orphans = []
        for pid in orphans:
            pl = _load_sidecar(works_dir / (pid + ".json"))
            for fname in {pl.get("image") or "", pid + ".webp", pid + ".json"}:
                if not fname:
                    continue
                try:
                    (works_dir / fname).unlink()
                except FileNotFoundError:
                    pass
            retired.append(pid)
    except Exception as e:
        errors.append({"id": "retire", "error": str(e)})

    # Always: editing a bio, re-hanging a collection or an artist's gallery,
    # writing an influence link or a thread is a publishable change on its own,
    # with no new artwork attached. None of them write anything when unchanged,
    # so this is free — and _prune sweeps out records the retirement above just
    # orphaned (the bio of a painter with nothing left, the emptied collection).
    extra, pruned = {}, 0
    for name, subdir, blobs_fn in (
            ("bios", ARTISTS_SUBDIR, _artist_blobs),
            ("collections", COLLECTIONS_SUBDIR, _collection_blobs),
            ("links", LINKS_SUBDIR, _link_blobs),
            ("threads", THREADS_SUBDIR, _thread_blobs)):
        try:
            blobs = blobs_fn()
            extra[name] = _sync(repo, subdir, blobs)
            pruned += len(_prune(repo, subdir, blobs))
        except Exception as e:
            extra[name] = []
            errors.append({"id": name, "error": str(e)})

    result = {"published": published, "pids": pids, "errors": errors,
              "retired": len(retired), "pruned": pruned,
              "committed": False, "pushed": False, "commit": None, "message": None}
    result.update({k: len(extra[k]) for k in _EXTRAS})

    _git(repo, "add", "-A")
    _, staged, _ = _git(repo, "status", "--porcelain")
    if not staged.strip():
        result["message"] = ("Nothing to publish." if not ids
                             else "Already up to date — no changes to push.")
        return result
    if published:
        _record_export(published)

    _git(repo, "commit", "-m", _commit_message(published, artists, extra, retired, pruned))
    result["committed"] = True
    _, sha_out, _ = _git(repo, "rev-parse", "--short", "HEAD", check=False)
    result["commit"] = sha_out.strip() or None
    try:
        _git(repo, "push", timeout=600)
        result["pushed"] = True
        result["message"] = ("Pushed %s to the public server."
                             % _summary(published, extra, len(retired)))
    except Exception as e:
        result["message"] = ("Committed locally but the push failed: %s — the commit "
                              "is saved; retry once git access is sorted." % e)
    return result


def _plural(n, noun):
    return "%d %s%s" % (n, noun, "" if n == 1 else "s")


def _and(bits):
    if len(bits) > 1:
        return ", ".join(bits[:-1]) + " and " + bits[-1]
    return bits[0] if bits else "nothing"


# The singular of each extra, for prose. Keyed off _EXTRAS so a new kind of thing
# that travels can't be added to the export and forgotten in what it says it did.
_NOUN = {"bios": "bio", "collections": "collection", "links": "link",
         "threads": "thread"}


def _summary(works, extra, retired=0):
    bits = [_plural(works, "work")] if works else []
    for k in _EXTRAS:
        n = len(extra.get(k) or [])
        if n:
            bits.append(_plural(n, _NOUN[k]))
    if retired:
        bits.append("retired " + _plural(retired, "work"))
    return _and(bits)


def _names(items):
    return ", ".join(items[:3]) + (" +%d more" % (len(items) - 3) if len(items) > 3 else "")


def _commit_message(published, artists, extra, retired=(), pruned=0):
    parts = []
    if published:
        who = _names(artists)
        parts.append("%d work(s)%s" % (published, (": " + who) if who else ""))
    for k in _EXTRAS:
        items = extra.get(k) or []
        if items:
            parts.append("%d %s(s): %s" % (len(items), _NOUN[k], _names(items)))
    if retired:
        parts.append("retire %d work(s)" % len(retired))
    if pruned:
        parts.append("tidy %d stale record(s)" % pruned)
    return "Publish " + (" + ".join(parts) or "updates")


def publish_new():
    """Export everything the public site hasn't got: works never published, placards
    corrected since they were, and any bio written or revised. One commit, one push."""
    fresh = unpublished_works()
    fixed = restated_works()
    ids = [w["id"] for w in fresh] + [w["id"] for w in fixed]
    result = publish_works(ids)
    result["new"] = len(fresh)
    result["restated"] = len(fixed)
    if not result["committed"] and not result["errors"]:
        result["message"] = ("Nothing to send — the public server already matches "
                             "your gallery.")
    return result


# ---------------- pull + import (public box) ----------------

def _import_dest(artist, title):
    folder = config.LIBRARY_DIR / safe_name(artist or "Unknown Artist", 80)
    folder.mkdir(parents=True, exist_ok=True)
    base = safe_name(title or "Untitled")
    path = folder / (base + ".webp")
    n = 2
    while path.exists():
        path = folder / ("%s (%d).webp" % (base, n))
        n += 1
    return path


def _sidecar_from_placard(p):
    d = {"pid": p["pid"]}
    for f in _PLACARD_FIELDS:
        if p.get(f) not in (None, ""):
            d[f] = p[f]
    if p.get("date") and not d.get("year"):
        d["year"] = parse_year(p["date"])
    if p.get("sha"):
        d["sha"] = p["sha"]
    d["imported_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return d


def _import_new(repo, p):
    img = repo / WORKS_SUBDIR / (p.get("image") or (p["pid"] + ".webp"))
    dest = _import_dest(p.get("artist"), p.get("title"))
    shutil.copyfile(str(img), str(dest))
    _save_sidecar(Path(str(dest) + ".json"), _sidecar_from_placard(p))


def _import_update(repo, p, cur):
    """Update an already-imported work in place. Returns True if anything changed."""
    cur_path = config.LIBRARY_DIR / cur["rel"]
    cur_sc = Path(str(cur_path) + ".json")
    old = _load_sidecar(cur_sc)
    new_sc = _sidecar_from_placard(p)

    image_changed = bool(p.get("sha")) and p.get("sha") != old.get("sha")
    core = lambda d: {k: d.get(k) for k in _PLACARD_FIELDS}
    fields_changed = core(new_sc) != core(old)
    if not image_changed and not fields_changed:
        return False

    dest = cur_path
    new_artist = (p.get("artist") or "").strip()
    if new_artist and new_artist.casefold() != (cur.get("artist") or "").strip().casefold():
        folder = config.LIBRARY_DIR / safe_name(new_artist, 80)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / cur_path.name
        n = 2
        while dest.exists() and dest.resolve() != cur_path.resolve():
            dest = folder / ("%s (%d)%s" % (cur_path.stem, n, cur_path.suffix))
            n += 1
        old_parent = cur_path.parent
        shutil.move(str(cur_path), str(dest))
        if cur_sc.exists():
            shutil.move(str(cur_sc), str(dest) + ".json")
        try:
            if old_parent != folder and old_parent.exists() and not any(old_parent.iterdir()):
                old_parent.rmdir()
        except Exception:
            pass

    if image_changed:
        img = repo / WORKS_SUBDIR / (p.get("image") or (p["pid"] + ".webp"))
        shutil.copyfile(str(img), str(dest))

    _save_sidecar(Path(str(dest) + ".json"), new_sc)
    return True


def _import_artists(repo):
    """Import the published bios. Run AFTER the works, so the pid->id map is
    populated and an artist's folder already exists."""
    adir = repo / ARTISTS_SUBDIR
    out = {"added": 0, "updated": 0, "unchanged": 0}
    if not adir.is_dir():
        return out
    # The cover travels as a pid because work ids are per-box: the same painting
    # lives at a different path here and so hashes differently.
    wid_by_pid = {w["pid"]: w["id"] for w in library.all_works() if w.get("pid")}

    for jf in sorted(adir.glob("*.json")):
        try:
            rec = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        data = {f: rec[f] for f in _BIO_FIELDS if rec.get(f) not in (None, "", [])}
        cover = wid_by_pid.get(rec.get("cover_pid"))
        # The hang is stored as the PUBLISHED ids, verbatim — not resolved to this
        # box's work ids here. Resolving at import time meant the order silently
        # dropped any work not yet scannable (e.g. added in this very pull), which
        # is why an order used to need a second pull to appear. apply_order maps
        # the pids to works at serve time instead, when they're all present.
        order_pids = [p for p in rec.get("work_order_pids") or [] if isinstance(p, str)]
        if not data and not cover and not order_pids:
            continue
        if cover:
            data["cover"] = cover
        data["source"] = "published"

        cur = artistinfo.load(name)
        same = bool(cur) and all(
            (cur.get(f) or None) == (data.get(f) or None) for f in _BIO_FIELDS)
        same = same and (not cover or (cur or {}).get("cover") == cover)
        same = same and ((cur or {}).get("work_order_pids") or []) == order_pids
        if same:
            out["unchanged"] += 1
            continue
        artistinfo.save(name, data)
        # Set or cleared explicitly: save() preserves an existing hang, but here
        # the private box is the author — no order in the record means none here.
        if order_pids:
            artistinfo.set_order_pids(name, order_pids)
        elif (artistinfo.load(name) or {}).get("work_order_pids"):
            artistinfo.set_order_pids(name, None)
        out["updated" if cur else "added"] += 1
    return out


def _import_collections(repo):
    """Import the published collections, mapping each pid back to this box's work id.
    Run AFTER the works, so the map is populated and complete.

    Unlike works and bios, this doesn't skip a collection whose repo file is
    unchanged. Membership here is stored as local work ids, and those are not
    stable: a work that arrives under a corrected artist name moves to a new folder
    and is re-identified, which would silently drop it out of every collection
    holding it. Recomputing the ids from the pids on every pull repairs that with
    no change on the private box at all — so the comparison is against the live
    record, not the file."""
    out = {"added": 0, "updated": 0, "unchanged": 0}
    wid_by_pid = {w["pid"]: w["id"] for w in library.all_works() if w.get("pid")}

    for rec in _read_records(repo, COLLECTIONS_SUBDIR):
        # A pid with no work here is one the owner deleted on this box (it's
        # tombstoned, so it never came back) -- the collection simply hangs without it.
        rec["work_ids"] = [wid_by_pid[p] for p in rec.get("work_pids") or []
                           if p in wid_by_pid]
        if not rec["work_ids"]:
            continue
        try:
            out[coll.import_published(rec)] += 1
        except ValueError:
            continue                       # malformed record -- not worth failing over
    return out


def _prewarm(artists):
    wanted = {(a or "").casefold() for a in artists}
    try:
        for w in library.scan(force=True)["by_id"].values():
            if (w.get("artist") or "").casefold() not in wanted:
                continue
            for fn in (thumbs.thumb_for, thumbs.view_for):
                try:
                    fn(w)
                except Exception:
                    pass
    except Exception:
        pass


def pull_and_import():
    """Fast-forward the content repo and import every published work by pid."""
    repo = _require_repo()
    try:
        _, out, err = _git(repo, "pull", "--ff-only", timeout=600)
        lines = (out + err).strip().splitlines()
        pull_msg = lines[-1].strip() if lines else None
    except Exception as e:
        # A diverged/detached checkout still lets us import what's on disk; note it.
        pull_msg = "git pull skipped: %s" % e

    works_dir = repo / WORKS_SUBDIR
    placards = []
    if works_dir.is_dir():
        for jf in sorted(works_dir.glob("*.json")):
            try:
                p = json.loads(jf.read_text(encoding="utf-8"))
                if isinstance(p, dict) and p.get("pid"):
                    placards.append(p)
            except Exception:
                pass

    existing = {w["pid"]: w for w in library.all_works() if w.get("pid")}
    suppressed = suppressed_pids()
    added = updated = unchanged = skipped = 0
    errors, touched = [], set()
    for p in placards:
        try:
            if p["pid"] in suppressed:   # owner deleted it here; don't bring it back
                skipped += 1
                continue
            cur = existing.get(p["pid"])
            if cur is None:
                _import_new(repo, p)
                added += 1
                touched.add(p.get("artist") or "Unknown Artist")
            elif _import_update(repo, p, cur):
                updated += 1
                touched.add(p.get("artist") or cur.get("artist"))
            else:
                unchanged += 1
        except Exception as e:
            errors.append({"pid": p.get("pid"), "error": str(e)})

    # Reconcile deletions from the private box: a pid the repo no longer carries
    # was retired at the source, so its copy leaves this wall too — into the
    # trash, like any deletion, so a mistake is recoverable by hand. Refused
    # wholesale when the repo suddenly lists nothing at all: an empty or wrong
    # clone must not be allowed to empty the museum.
    removed = 0
    repo_pids = {p["pid"] for p in placards}
    gone = [w["id"] for w in existing.values() if w["pid"] not in repo_pids]
    if gone and not placards:
        errors.append({"pid": "removals", "error":
            "The repo lists no works at all; kept the %d local copies in case "
            "the pull itself failed." % len(gone)})
    elif gone:
        removed_ids, del_errs = library.delete_works(gone)
        removed = len(removed_ids)
        for e in del_errs:
            errors.append({"pid": e.get("id"), "error": e.get("error")})
        touched.update(w.get("artist") or "" for w in existing.values()
                       if w["id"] in set(removed_ids))
    _clean_suppressed(repo_pids)

    # Unconditionally: the bio import reads the pid->id map off a fresh scan, and
    # bios can change with no work changing at all.
    library.invalidate()
    try:
        bios = _import_artists(repo)
    except Exception as e:
        bios = {"added": 0, "updated": 0, "unchanged": 0}
        errors.append({"pid": "artists", "error": str(e)})
    # Collections resolve pids against the library, so they follow the works. Links
    # and threads name painters, who are just there once the works are.
    imports = {"collections": lambda: _import_collections(repo),
               "links": lambda: links.import_published(_read_records(repo, LINKS_SUBDIR)),
               "threads": lambda: threads.import_published(_read_records(repo, THREADS_SUBDIR))}
    got = {}
    for name, fn in imports.items():
        try:
            got[name] = fn()
        except Exception as e:
            got[name] = {"added": 0, "updated": 0, "unchanged": 0}
            errors.append({"pid": name, "error": str(e)})
    # The other half of reconciliation: imported collections, links and threads
    # whose repo record has gone were retired at the source. Only records marked
    # source == "published" are ever touched — what curators made on THIS box is
    # theirs, whatever happens over there.
    for name, subdir, prune in (
            ("collections", COLLECTIONS_SUBDIR, coll.prune_published),
            ("links", LINKS_SUBDIR, links.prune_published),
            ("threads", THREADS_SUBDIR, threads.prune_published)):
        try:
            got[name]["removed"] = prune(
                {r.get("id") for r in _read_records(repo, subdir)})
        except Exception as e:
            errors.append({"pid": name, "error": str(e)})
    if added or updated:
        _prewarm(touched)

    return dict({"added": added, "updated": updated, "unchanged": unchanged,
                 "skipped": skipped, "removed": removed, "errors": errors,
                 "pull": pull_msg, "total": len(placards), "bios": bios}, **got)
