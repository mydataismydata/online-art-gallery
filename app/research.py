"""Owner-only research proxy behind the placard editor's browse pane.

Search engines and most websites refuse to load inside an iframe
(X-Frame-Options / frame-ancestors), and even when a page does load, the
browser's same-origin policy makes text selected in a cross-origin frame
invisible to the app around it. So the placard editor browses through this
proxy instead: pages are fetched server-side and re-served from our own
origin with their scripts stripped, assets rebased to the original site via
<base>, and a small snippet injected that reports text selections back to
the editor (and reroutes link clicks / GET form submits through the proxy so
navigation stays inside the pane).
"""
import html as html_mod
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import requests

from .downloads.util import session

MAX_BYTES = 5 * 1024 * 1024  # refuse to relay pages bigger than this

# Injected into every proxied page. Selection changes are posted to the parent
# (the editor); link clicks and GET form submits are rerouted through the proxy.
_SNIPPET = """
<script>
(function () {
  // The injected <base> rebases every relative URL to the ORIGINAL site, so the
  // proxy path must be pinned to our own origin explicitly.
  var PROXY = window.location.origin + "/research/page?url=";
  function report() {
    // Read via ranges, not Selection.toString(): toString() is layout-based and
    // returns "" in frames the browser considers unfocused/unpainted.
    var sel = document.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    var s = "";
    for (var i = 0; i < sel.rangeCount; i++) s += sel.getRangeAt(i).toString();
    // collapse runs of spaces but KEEP line breaks — they carry the formatting
    s = s.replace(/[ \\t\\r]+/g, " ").replace(/ ?\\n ?/g, "\\n").replace(/\\n{3,}/g, "\\n\\n").trim();
    if (s) parent.postMessage({ type: "placard-selection", text: s }, window.location.origin);
  }
  // Synchronous on purpose: the selection is final by mouseup, and setTimeout
  // gets throttled hard in backgrounded/unfocused frames.
  document.addEventListener("mouseup", report);
  document.addEventListener("touchend", report);
  document.addEventListener("keyup", function (e) {
    if (e.key === "Shift" || (e.key && e.key.indexOf("Arrow") === 0)) report();
  });
  document.addEventListener("click", function (e) {
    var n = e.target;
    while (n && n !== document && !(n.tagName === "A" && n.getAttribute("href"))) n = n.parentNode;
    if (!n || n === document) return;
    e.preventDefault();
    if (!/^https?:/i.test(n.href)) return;
    window.location.href = PROXY + encodeURIComponent(n.href);
  }, true);
  document.addEventListener("submit", function (e) {
    var f = e.target;
    e.preventDefault();
    try {
      if ((f.method || "get").toLowerCase() !== "get") return;
      var u = new URL(f.getAttribute("action") || window.location.href, document.baseURI);
      var fd = new FormData(f), qs = new URLSearchParams();
      fd.forEach(function (v, k) { qs.append(k, v); });
      u.search = qs.toString();
      window.location.href = PROXY + encodeURIComponent(u.href);
    } catch (err) { /* leave the form dead rather than escaping the proxy */ }
  }, true);
})();
</script>
"""


def _addr_ok(url):
    """True only for public http(s) hosts — keeps the proxy away from
    localhost / LAN / cloud-metadata addresses."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        port = p.port or (443 if p.scheme == "https" else 80)
        for info in socket.getaddrinfo(p.hostname, port):
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
        return True
    except (socket.gaierror, ValueError, OSError):
        return False


def error_page(msg):
    return ('<!doctype html><html><body style="font-family:Georgia,serif;color:#555;'
            'background:#f5f2ea;padding:26px;font-size:14px">%s</body></html>'
            % html_mod.escape(msg))


# e.g. <meta http-equiv="refresh" content="0;URL='https://…'"> — redirect
# interstitials (DuckDuckGo's result links use one). Followed server-side so the
# frame never navigates off our origin.
_META_REFRESH = re.compile(
    r"(?is)<meta[^>]+http-equiv\s*=\s*[\"']?refresh[\"']?[^>]*?url\s*=\s*['\"]?([^'\"<>]+)")


def fetch_page(url):
    """Fetch a public web page and return (sanitized_html, None), or (None, error)."""
    url = (url or "").strip()
    if not url:
        return None, "No URL given."
    sess = session()
    r = None
    for _hop in range(4):  # original page + up to 3 meta-refresh hops
        if not _addr_ok(url):
            return None, "That address can't be browsed from here (only public websites)."
        try:
            r = sess.get(url, timeout=20, allow_redirects=True)
        except requests.RequestException as e:
            return None, "Couldn't fetch the page: %s" % e
        if not _addr_ok(r.url):  # a redirect tried to point us somewhere non-public
            return None, "The page redirected to a non-public address."
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype:
            return None, "Not a web page (%s) — open it in a normal tab instead." % (ctype.split(";")[0] or "unknown type")
        if len(r.content) > MAX_BYTES:
            return None, "Page too large to display here."
        m = _META_REFRESH.search(r.text)
        if m and _hop < 3:
            url = urljoin(r.url, html_mod.unescape(m.group(1).strip()))
            continue
        break

    page = r.text
    # belt-and-braces: no meta-refresh may survive into the frame
    page = re.sub(r"(?is)<meta[^>]+http-equiv\s*=\s*[\"']?refresh[^>]*>", "", page)
    # Strip the site's scripts (they'd fight the proxy and can frame-bust),
    # embedded frames, CSP metas, inline event handlers and any <base> of its own.
    page = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", page)
    page = re.sub(r"(?is)<script\b[^>]*/\s*>", "", page)
    page = re.sub(r"(?is)<iframe\b[^>]*>.*?</iframe>", "", page)
    page = re.sub(r"(?is)<meta[^>]+http-equiv\s*=\s*[\"']?content-security-policy[^>]*>", "", page)
    page = re.sub(r"(?is)<base\b[^>]*>", "", page)
    page = re.sub(r"""(?is)\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", "", page)

    # Rebase relative asset URLs (images, stylesheets) to the original site,
    # then add our snippet. Only the document itself must be same-origin.
    inject = '<base href="%s">%s' % (html_mod.escape(r.url, quote=True), _SNIPPET)
    head = re.search(r"(?is)<head[^>]*>", page)
    if head:
        page = page[:head.end()] + inject + page[head.end():]
    else:
        page = inject + page
    return page, None
