import time
import uuid

import requests

from .. import config

# Transient network failures worth retrying: DNS blips (getaddrinfo failed),
# dropped connections, timeouts, and mid-stream truncation. A home network on a
# multi-request job will hit these occasionally; one shouldn't abort the job.
_TRANSIENT = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def session():
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    return s


def _retry_after(resp, fallback, cap):
    """Seconds to wait before retrying, honouring a Retry-After header if present.

    Take the server at its word up to `cap`. Wikimedia answers a bulk download with
    Retry-After: 600 and a scaled-image render with Retry-After: 1 — two limits an
    order of magnitude apart, and both are told to us plainly. Substituting our own
    guess for either means knocking on a door we've been asked not to knock on."""
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return min(float(ra), cap)
        except ValueError:
            pass
    return min(fallback, cap)


def _wait(seconds, on_wait, should_stop):
    """Sleep, but in slices, so a job asked to stop during a ten-minute cooldown
    stops. Returns False if it was cut short."""
    if on_wait:
        on_wait(seconds)
    end = time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            return True
        if should_stop and should_stop():
            return False
        time.sleep(min(1.0, left))


def request_with_retries(sess, url, attempts=3, backoff=1.5, max_wait=120,
                         on_wait=None, should_stop=None, **kwargs):
    """sess.get(url, **kwargs), retrying transient network errors with linear backoff.
    Also backs off and retries on HTTP 429/503 (rate limit / temporarily unavailable),
    honouring Retry-After up to max_wait. Other HTTP status errors are NOT retried here
    — callers still raise_for_status().

    max_wait bounds a single sleep: a quick metadata call shouldn't stall for ten
    minutes, but a download that has been told to wait that long should. on_wait is
    handed the seconds so a job can say why it has gone quiet."""
    last = None
    r = None
    for i in range(attempts):
        try:
            r = sess.get(url, **kwargs)
        except _TRANSIENT as e:
            last = e
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
            continue
        if r.status_code in (429, 503) and i < attempts - 1:
            wait = _retry_after(r, backoff * (i + 1) * 6, max_wait)
            r.close()
            if not _wait(wait, on_wait, should_stop):
                raise RuntimeError("cancelled while waiting out a rate limit")
            last = None
            continue
        return r
    if last:
        raise last
    return r  # a 429/503 that exhausted retries — caller's raise_for_status() handles it


def fetch_json(sess, url, params=None, timeout=60, **retry):
    """A JSON GET. Extra kwargs go to request_with_retries — a caller fetching
    something optional should pass attempts=1, max_wait=0 rather than spend minutes
    waiting out a rate limit for a nicety it can do without."""
    r = request_with_retries(sess, url, params=params, timeout=timeout, **retry)
    r.raise_for_status()
    return r.json()


def _download_once(sess, url, timeout, headers, max_wait, on_wait, should_stop):
    r = request_with_retries(sess, url, stream=True, timeout=timeout, headers=headers,
                             max_wait=max_wait, on_wait=on_wait, should_stop=should_stop)
    if r.status_code != 200:
        r.close()
        if r.status_code == 429:
            raise RuntimeError("rate limited (HTTP 429) even after waiting it out")
        raise RuntimeError("HTTP %d" % r.status_code)
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "image" not in ctype and "octet-stream" not in ctype:
        r.close()
        raise RuntimeError("not an image (%s)" % (ctype or "no content-type"))
    ext = ".jpg"
    if "png" in ctype:
        ext = ".png"
    elif "webp" in ctype:
        ext = ".webp"
    elif url.split("?")[0].lower().endswith(".png"):
        ext = ".png"
    path = config.TMP_DIR / ("dl-%s%s" % (uuid.uuid4().hex[:12], ext))
    try:
        with open(str(path), "wb") as f:
            for chunk in r.iter_content(1 << 16):
                if chunk:
                    f.write(chunk)
    except Exception:
        if path.exists():
            path.unlink()
        raise
    finally:
        r.close()
    if path.stat().st_size < 10240:
        path.unlink()
        raise RuntimeError("suspiciously small file (<10 KB)")
    return path


def download_to_tmp(sess, url, timeout=300, referer=None, attempts=3, backoff=1.5,
                    max_wait=900, on_wait=None, should_stop=None):
    """Stream a URL to a temp file; returns the temp Path. Raises on non-image.
    Retries the whole download on transient network errors (including mid-stream).

    max_wait defaults high here: a host that answers a big download with "come back
    in ten minutes" means it, and waiting is the only way through."""
    headers = {"Referer": referer} if referer else {}
    last = None
    for i in range(attempts):
        try:
            return _download_once(sess, url, timeout, headers, max_wait, on_wait, should_stop)
        except _TRANSIENT as e:
            last = e
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
    raise last
