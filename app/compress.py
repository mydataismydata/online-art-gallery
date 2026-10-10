"""Gzip for the text the gallery sends: its scripts, styles, page and JSON.

waitress sends every file just as it lies on disk, and the museum walk's
scripts come to most of a megabyte that way (three.js alone is 742 KB; gzipped,
189 KB). So a response is compressed whenever the browser accepts gzip, it is
text of some kind, and it's big enough to be worth the trouble. Images are
compressed already and go out as they are.

A static file is compressed once per version, at the best level, and kept in
memory keyed by its path, size and modification time. Dynamic responses (the
API's JSON) are compressed as they go. Either way the ETag turns weak — the
bytes on the wire differ, the content doesn't — so a browser revalidating its
copy still gets a 304.
"""
import gzip
import os

from flask import request
from werkzeug.security import safe_join

from . import config

TEXT = ("text/", "application/javascript", "application/json", "application/xml", "image/svg+xml")
MIN_BYTES = 1024
_static = {}   # (path, size, mtime_ns) → gzipped bytes


def _static_path(app):
    """The file behind a static (or index) response, or None."""
    if request.endpoint == "static":
        path = safe_join(app.static_folder, (request.view_args or {}).get("filename", ""))
    elif request.endpoint == "index":
        path = os.path.join(str(config.STATIC_DIR), "index.html")
    else:
        return None
    return path if path and os.path.isfile(path) else None


def _gzipped_file(path):
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    body = _static.get(key)
    if body is None:
        with open(path, "rb") as f:
            body = gzip.compress(f.read(), 9)
        _static[key] = body
    return body


def init_app(app):
    @app.after_request
    def gzip_text(resp):
        if not (resp.mimetype or "").startswith(TEXT):
            return resp
        # caches between here and the browser must keep the two forms apart
        resp.vary.add("Accept-Encoding")
        if (resp.status_code != 200 or "Content-Encoding" in resp.headers
                or "gzip" not in request.headers.get("Accept-Encoding", "").lower()):
            return resp
        path = _static_path(app) if resp.direct_passthrough else None
        if path:
            if os.path.getsize(path) < MIN_BYTES:
                return resp
            body = _gzipped_file(path)
            if hasattr(resp.response, "close"):
                resp.response.close()        # the open file send_file was about to stream
            resp.direct_passthrough = False
        elif resp.is_streamed or resp.direct_passthrough:
            return resp                      # a stream, or a file we can't name: as it is
        else:
            data = resp.get_data()
            if len(data) < MIN_BYTES:
                return resp
            body = gzip.compress(data, 6)
        resp.set_data(body)
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers.pop("Accept-Ranges", None)   # byte ranges would name the plain file
        etag, weak = resp.get_etag()
        if etag and not weak:
            resp.set_etag(etag, weak=True)
        return resp

    return app
