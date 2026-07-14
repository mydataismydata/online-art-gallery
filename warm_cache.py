"""Pre-render thumbnails and screen-sized view derivatives for every work, so the
first time anyone opens the gallery it's fast instead of generating on demand.

Safe to run any time — it skips anything already cached. Run it once after
upgrading (view derivatives are new), using the same GALLERY_* env as the server:

    python warm_cache.py
"""
import sys
import time

from app import library, thumbs


def main():
    works = list(library.scan(force=True)["by_id"].values())
    total = len(works)
    print("Warming cache for %d works…" % total, flush=True)
    t0 = time.time()
    for i, w in enumerate(works, 1):
        for fn in (thumbs.thumb_for, thumbs.view_for):
            try:
                fn(w)
            except Exception as e:
                print("  ! %s: %s" % (w.get("rel"), e), flush=True)
        if i % 25 == 0 or i == total:
            print("  %d/%d (%.0fs)" % (i, total, time.time() - t0), flush=True)
    print("Done in %.0fs." % (time.time() - t0), flush=True)


if __name__ == "__main__":
    sys.exit(main())
