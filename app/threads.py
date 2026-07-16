"""Threads: curator-written paths through the collection.

Where the map lets you wander, a thread is an argument — "how open-air river
painting left Barbizon and ended up beside the Yarra" — told as an ordered chain
of painters, each with a line saying why they're the next step.

Stored in data/threads.json. Unlike links, nothing here is derivable: a thread is
entirely a human's reading of the collection."""
import json
import secrets
import time

from . import config, library
from .names import fold

MAX_STEPS = 12


def _load():
    try:
        data = json.loads(config.THREADS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("threads"), list):
            return data["threads"]
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return []


def _save(threads):
    config.THREADS_FILE.write_text(
        json.dumps({"threads": threads}, ensure_ascii=False, indent=1), encoding="utf-8")


def _clean_steps(steps):
    out = []
    for s in (steps or []):
        artist = (s.get("artist") or "").strip()
        if not artist:
            continue
        out.append({"artist": artist, "note": (s.get("note") or "").strip()[:300]})
    if len(out) < 2:
        raise ValueError("A thread needs at least two painters — it's a path, not a pin.")
    if len(out) > MAX_STEPS:
        raise ValueError("Keep a thread to %d steps or fewer." % MAX_STEPS)
    return out


def _validate(title, steps):
    title = (title or "").strip()
    if not title:
        raise ValueError("Give the thread a title.")
    return title[:120], _clean_steps(steps)


def list_threads():
    """Threads with each step's artist resolved to a thumbnail. Steps naming an
    artist no longer in the library are dropped, and a thread left with fewer than
    two steps is hidden rather than shown broken — a repoint or a delete upstream
    shouldn't leave a dangling path on the page."""
    # Folded: a step written when the painter was spelled 'Theodore Gericault' still
    # finds them now the library prefers 'Théodore Géricault'. Matching on the exact
    # string would quietly drop the step, and then the whole thread.
    covers = {}
    for a in library.artists():
        covers[fold(a["name"])] = {"cover": a["cover"], "name": a["name"]}
    out = []
    for t in _load():
        steps = []
        for s in t["steps"]:
            hit = covers.get(fold(s["artist"]))
            if hit:
                steps.append({"artist": hit["name"], "note": s["note"], "cover": hit["cover"]})
        if len(steps) >= 2:
            out.append(dict(t, steps=steps))
    return out


def create(title, description, steps, created_by=None):
    title, steps = _validate(title, steps)
    threads = _load()
    rec = {
        "id": secrets.token_hex(6),
        "title": title,
        "description": (description or "").strip()[:600],
        "steps": steps,
        "created_by": created_by,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    threads.append(rec)
    _save(threads)
    return rec


def update(tid, title, description, steps):
    title, steps = _validate(title, steps)
    threads = _load()
    for t in threads:
        if t["id"] == tid:
            t.update({"title": title, "description": (description or "").strip()[:600],
                      "steps": steps})
            _save(threads)
            return t
    raise LookupError("No such thread.")


def get(tid):
    return next((t for t in _load() if t["id"] == tid), None)


def delete(tid):
    threads = _load()
    rest = [t for t in threads if t["id"] != tid]
    if len(rest) == len(threads):
        raise LookupError("No such thread.")
    _save(rest)


def rename(old, new):
    """Follow an artist rename/merge, so a thread keeps its path."""
    old_k = fold((old or "").strip())
    threads, touched = _load(), False
    for t in threads:
        for s in t["steps"]:
            if fold(s["artist"]) == old_k:
                s["artist"], touched = new, True
    if touched:
        _save(threads)
