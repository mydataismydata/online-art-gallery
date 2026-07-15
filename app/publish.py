"""Publishing the gallery as a public 'snapshot'.

Two directions, both talking to a dedicated git 'content' repo (kept separate from
the code repo):

  * publish_works(ids)   -- run on the LOCAL box (private mode). Copies each selected
    work's reduced-size (<=VIEW_MAX) WebP plus a completed placard into <repo>/works/,
    then commits and pushes. Each work is stamped with a persistent `pid`, so
    re-pushing a fixed placard or a better image updates the same public work
    instead of duplicating it.

  * pull_and_import()    -- run on the PUBLIC box (GALLERY_PUBLIC=1). git-pulls the
    repo and imports every works/<pid>.json into the local library, as if the work
    had been added by hand but with its placard already filled. Matching is by
    `pid`, so re-pulls update in place instead of duplicating.

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

from . import config, library, thumbs
from .names import safe_name, parse_year

WORKS_SUBDIR = "works"

# Placard fields carried to the public site: everything the viewer shows plus
# provenance. Width/height are intentionally omitted -- the public work's real
# dimensions come from the (reduced) image file itself when it's scanned.
_PLACARD_FIELDS = ("artist", "title", "date", "year", "medium", "style", "genre",
                   "school", "description", "type", "source", "source_url")


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
    st["last_export"] = last_export()
    try:
        st["new_count"] = len(unpublished_works())
    except Exception:
        st["new_count"] = None
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

    result = {"published": published, "pids": pids, "errors": errors,
              "committed": False, "pushed": False, "commit": None, "message": None}
    if not published:
        result["message"] = "Nothing to publish."
        return result

    # The sidecars just gained pids; drop the scan cache so those works stop
    # counting as "new" straight away.
    library.invalidate()
    _record_export(published)
    _git(repo, "add", "-A")
    _, staged, _ = _git(repo, "status", "--porcelain")
    if not staged.strip():
        result["message"] = "Already up to date — no changes to push."
        return result

    who = ", ".join(artists[:3]) + (" +%d more" % (len(artists) - 3) if len(artists) > 3 else "")
    msg = "Publish %d work(s)%s" % (published, (": " + who) if who else "")
    _git(repo, "commit", "-m", msg)
    result["committed"] = True
    _, sha_out, _ = _git(repo, "rev-parse", "--short", "HEAD", check=False)
    result["commit"] = sha_out.strip() or None
    try:
        _git(repo, "push", timeout=600)
        result["pushed"] = True
        result["message"] = "Pushed %d work(s) to the public server." % published
    except Exception as e:
        result["message"] = ("Committed locally but the push failed: %s — the commit "
                              "is saved; retry once git access is sorted." % e)
    return result


def publish_new():
    """Export every work that has never been published, in one commit + push."""
    works = unpublished_works()
    if not works:
        return {"published": 0, "new": 0, "pids": [], "errors": [], "committed": False,
                "pushed": False, "commit": None,
                "message": "No new artwork — everything is already on the public server."}
    result = publish_works([w["id"] for w in works])
    result["new"] = len(works)
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

    if added or updated:
        library.invalidate()
        _prewarm(touched)

    return {"added": added, "updated": updated, "unchanged": unchanged,
            "skipped": skipped, "errors": errors, "pull": pull_msg, "total": len(placards)}
