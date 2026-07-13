"""Google Arts & Culture, downloaded tile-by-tile at full quality via dezoomify-rs.

Flow: resolve the artist to an /entity/ page (or accept a pasted entity URL),
harvest /asset/ links from the page's embedded data, then run dezoomify-rs on
each asset page. Metadata (title/artist/date) is scraped from each asset page.

Note: the entity page embeds only its first batch of works (typically 40-60).
For an artist's complete Google Arts & Culture holdings you may need to run the
job again later or use additional sources; the job log reports what it found.
"""
import html as html_mod
import os
import re
import shutil
import subprocess
import time
import uuid

from ... import config, library
from ...names import name_match, normalize_comma_name, parse_year, strip_diacritics, unshout
from ..util import session

ID = "gac"
LABEL = "Google Arts & Culture (dezoomify-rs)"
HINT = ("Finds the artist on artsandculture.google.com and pulls each work with "
        "dezoomify-rs — the same trick as the dezoomify app. Images are capped at "
        "%d px per side by default; raise Max size (up to ~60000) for gigapixel "
        "detail at the cost of minutes of CPU per painting. You can paste an entity "
        "URL (https://artsandculture.google.com/entity/…) directly if name search "
        "picks the wrong artist.")
PLACEHOLDER = "Artist name or entity URL, e.g. Adolph Menzel"
SUPPORTS_MAX_PX = True
# Largest zoom level fitting this many px per side is fetched when Max size is
# left blank. Uncapped Art-Project scans can exceed JPEG's 65535px hard limit —
# dezoomify-rs then burns minutes of CPU stitching an image it cannot encode.
MAX_PX_DEFAULT = 12000
HINT = HINT % MAX_PX_DEFAULT

GAC = "https://artsandculture.google.com"


def find_binary():
    env = os.environ.get("DEZOOMIFY_RS")
    if env and os.path.isfile(env):
        return env
    for name in ("dezoomify-rs", "dezoomify-rs.exe"):
        local = config.ROOT / name
        if local.is_file():
            return str(local)
    return shutil.which("dezoomify-rs")


def availability():
    binary = find_binary()
    if binary:
        return True, "using %s" % binary
    return False, ("dezoomify-rs not found. Put the binary next to serve.py, on PATH, "
                   "or set DEZOOMIFY_RS. Releases: github.com/lovasoa/dezoomify-rs")


def _get_html(sess, url):
    r = sess.get(url, timeout=60)
    r.raise_for_status()
    if "consent.google.com" in r.url:
        raise RuntimeError("Google is showing a consent page; open the site once in a "
                           "browser from this network, or paste an entity URL.")
    return r.text


def _og_title(page):
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', page)
    if not m:
        m = re.search(r'<title>([^<]+)</title>', page)
    if not m:
        return ""
    t = html_mod.unescape(m.group(1))
    # strip the site suffix: "… — Google Arts & Culture" / "… - Google Arts & Culture"
    t = re.sub(r"\s*[—-]\s*Google Arts & Culture\s*$", "", t)
    return t.strip()


def _find_entity(sess, job, query):
    page = _get_html(sess, GAC + "/search/entity?q=" + re.sub(r"\s+", "+", query.strip()))
    artists, others = [], []
    for raw in re.findall(r'"(/entity/[^"]+)"', page):
        path = raw.split("?")[0].split("\\")[0]
        is_artist = "artist" in raw[len(path):]
        bucket = artists if is_artist else others
        if path not in bucket:
            bucket.append(path)
    candidates = artists + [p for p in others if p not in artists]
    if not candidates:
        raise RuntimeError("No artist entity found for \"%s\". Try pasting the entity URL "
                           "from artsandculture.google.com." % query)
    tokens = [t for t in re.split(r"[^a-z0-9]+", strip_diacritics(query).lower()) if len(t) > 2]
    # Verify candidates by their page title; entity ids ("m057ldt") say nothing by themselves.
    for path in candidates[:5]:
        try:
            entity_page = _get_html(sess, GAC + path)
        except Exception:
            continue
        title = _og_title(entity_page)
        norm = strip_diacritics(title).lower()
        if title and (not tokens or any(t in norm for t in tokens)):
            job.log("Entity search matched: %s (\"%s\")" % (path, title))
            return GAC + path, entity_page
    # No candidate's page title contained any word from the query. Google's entity
    # search returns *associated* people too, so the top result is often a different
    # artist entirely (e.g. searching "Adrian Stokes" surfacing "Barbara Hepworth").
    # Refuse to guess — downloading the wrong artist's works is worse than failing.
    raise RuntimeError(
        "Google Arts & Culture has no entity whose name matches \"%s\" - the closest "
        "results are other artists, so nothing was downloaded. If the artist is on the "
        "site, open their page and paste its /entity/ URL (https://artsandculture."
        "google.com/entity/...) into the Artist box." % query)


def _harvest_assets(page):
    seen, out = set(), []
    for p in re.findall(r'"(?:https://artsandculture\.google\.com)?(/asset/[^"]+?)"', page):
        p = p.split("?")[0].split("\\")[0]
        if p.count("/") >= 3 and p not in seen:  # /asset/<slug>/<id>
            seen.add(p)
            out.append(p)
    return out


# Detail rows are embedded as ["Label",[["value"]],0]
_DETAIL_PATTERNS = {
    "date": (r'"Date Created",\[\["([^"\\]+)"', r'"Date",\[\["([^"\\]+)"'),
    "medium": (r'"Technique and material",\[\["([^"\\]+)"',
               r'"Medium",\[\["([^"\\]+)"',
               r'"Materials",\[\["([^"\\]+)"'),
    "style": (r'"Art movement",\[\["([^"\\]+)"', r'"Art Movement",\[\["([^"\\]+)"'),
}


def _asset_details(page):
    out = {}
    for key, patterns in _DETAIL_PATTERNS.items():
        for pat in patterns:
            m = re.search(pat, page)
            if m:
                out[key] = html_mod.unescape(m.group(1)).strip()
                break
    return out


def _run_dezoomify(job, cmd, timeout=1800):
    """Run dezoomify-rs, polling so a job cancel kills it immediately.
    Returns (returncode, output); -1 = cancelled, -2 = timed out."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    start = time.time()
    while True:
        try:
            out, _ = proc.communicate(timeout=1)
            return proc.returncode, out or ""
        except subprocess.TimeoutExpired:
            if job.cancelled:
                proc.kill()
                proc.communicate()
                return -1, "cancelled"
            if time.time() - start > timeout:
                proc.kill()
                proc.communicate()
                return -2, "timed out after %ds" % timeout


def run(job):
    binary = find_binary()
    if not binary:
        raise RuntimeError(availability()[1])
    job.log("dezoomify-rs: %s" % binary)

    sess = session()
    q = job.query.strip()
    if "artsandculture.google.com" in q:
        entity_url = q.split("?")[0]
        entity_page = _get_html(sess, entity_url)
    else:
        entity_url, entity_page = _find_entity(sess, job, q)
    entity_artist = unshout(normalize_comma_name((_og_title(entity_page) or q).strip()))
    assets = _harvest_assets(entity_page)
    job.log("Artist page \"%s\": found %d works embedded in the page." % (entity_artist, len(assets)))
    if not assets:
        raise RuntimeError("No works found on the entity page. Google may have changed "
                           "their page format, or the entity has no assets.")

    max_items = job.opts.get("max_items")
    # JPEG cannot encode a side over 65535px; stay safely under it.
    max_px = min(job.opts.get("max_px") or MAX_PX_DEFAULT, 60000)

    for path in assets:
        if job.cancelled:
            return
        asset_url = GAC + path
        asset_id = path.rstrip("/").rsplit("/", 1)[-1]

        if library.source_exists(ID, asset_id):
            job.skipped += 1
            job.found += 1
            continue

        title, artist, details = None, None, {}
        try:
            asset_page = _get_html(sess, asset_url)
            og = _og_title(asset_page)
            if " - " in og:
                title, artist = og.rsplit(" - ", 1)
            else:
                title = og
            details = _asset_details(asset_page)
        except Exception as e:
            job.log("Could not read metadata for %s (%s); will still try to download." % (path, e))
        title = (title or asset_id).strip()
        # lending museums credit inconsistently: "Arthur STREETON" (shouted) or
        # "Daubigny, Charles-François" (surname-first). Normalize both to cut down
        # on the same painter splitting into several galleries.
        artist = unshout(normalize_comma_name((artist or "").strip()))

        # entity pages also embed "related" works by other painters — skip those
        if artist and not name_match(entity_artist, artist) and not name_match(artist, entity_artist):
            job.log("Skipping \"%s\" — credited to %s." % (title, artist))
            continue
        job.found += 1

        date_text = details.get("date")
        meta = {
            "title": title,
            "date": date_text,
            "year": parse_year(date_text),
            "medium": details.get("medium"),
            "style": details.get("style"),
            "type": "painting",
            "source": ID,
            "source_id": asset_id,
            "source_url": asset_url,
        }
        tmp = config.TMP_DIR / ("gac-%s.jpg" % uuid.uuid4().hex[:12])
        # No -l/--largest here: it overrides the max flags. --max-width/--max-height
        # alone pick the largest zoom level that fits, i.e. native size for ordinary
        # scans and the cap for gigapixel ones.
        cmd = [binary, "--max-width", str(max_px), "--max-height", str(max_px),
               asset_url, str(tmp)]
        job.log("Downloading \"%s\" (up to %d px)…" % (title, max_px))
        t0 = time.time()
        code, output = _run_dezoomify(job, cmd)
        if code == -1:
            if tmp.exists():
                tmp.unlink()
            return
        if code != 0 or not tmp.exists():
            job.failed += 1
            lines = output.strip().splitlines()
            reason = lines[-1] if lines else "dezoomify-rs error"
            if "too small or too large" in reason:
                reason += " — the stitched image exceeds what JPEG can encode; lower Max size."
            job.log("FAILED \"%s\" after %.0fs: %s" % (title, time.time() - t0, reason))
            if tmp.exists():
                tmp.unlink()
            continue
        size_mb = tmp.stat().st_size / 1048576.0
        saved_path = library.save_work(artist or entity_artist, meta, tmp)
        job.saved += 1
        job.log("Saved: %s (%.0fs, %.1f MB)" % (saved_path.name, time.time() - t0, size_mb))
        if max_items and job.saved >= max_items:
            job.log("Reached the requested maximum of %d works." % max_items)
            return
        time.sleep(1.0)
