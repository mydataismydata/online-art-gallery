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


def _retry_after(resp, fallback):
    """Seconds to wait before retrying, honouring a Retry-After header if present."""
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return min(float(ra), 120.0)
        except ValueError:
            pass
    return fallback


def request_with_retries(sess, url, attempts=3, backoff=1.5, **kwargs):
    """sess.get(url, **kwargs), retrying transient network errors with linear backoff.
    Also backs off and retries on HTTP 429/503 (rate limit / temporarily unavailable),
    honouring Retry-After. Other HTTP status errors are NOT retried here — callers still
    raise_for_status()."""
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
            wait = _retry_after(r, backoff * (i + 1) * 6)
            r.close()
            time.sleep(wait)
            last = None
            continue
        return r
    if last:
        raise last
    return r  # a 429/503 that exhausted retries — caller's raise_for_status() handles it


def fetch_json(sess, url, params=None, timeout=60):
    r = request_with_retries(sess, url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _download_once(sess, url, timeout, headers):
    r = request_with_retries(sess, url, stream=True, timeout=timeout, headers=headers)
    if r.status_code != 200:
        r.close()
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


def download_to_tmp(sess, url, timeout=300, referer=None, attempts=3, backoff=1.5):
    """Stream a URL to a temp file; returns the temp Path. Raises on non-image.
    Retries the whole download on transient network errors (including mid-stream)."""
    headers = {"Referer": referer} if referer else {}
    last = None
    for i in range(attempts):
        try:
            return _download_once(sess, url, timeout, headers)
        except _TRANSIENT as e:
            last = e
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
    raise last
