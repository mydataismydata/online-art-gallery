import os
import time

from flask import Blueprint, abort, jsonify, request, send_file

from . import (config, library, thumbs, artistinfo, auth, collections,
               metadata, ai, publish, site, links, threads)
from .downloads import manager
from .downloads.sources import (get_source, list_sources, custom,
                                list_builtin_configs, set_builtin_config, reset_builtin_config)

bp = Blueprint("api", __name__)


# Endpoints that don't exist on the public "snapshot" server (authoring, downloads,
# AI, sources): the public box is fed only by Pull. Blocked centrally so nothing —
# not even the owner — can add or mutate art there. On the private box PUBLIC is
# False, so this never fires. (Publish routes use the @private_only decorator.)
_PRIVATE_ONLY_ENDPOINTS = {
    "api.api_rescan", "api.api_artist_rename",
    "api.api_artist_lookup", "api.api_artist_ai_lookup", "api.api_artist_save",
    "api.api_work_find_metadata", "api.api_work_update",
    "api.api_ai_config", "api.api_ai_config_save",
    "api.api_work_autofill", "api.api_works_autofill_batch",
    "api.api_custom_sources", "api.api_custom_sources_save",
    "api.api_custom_sources_delete", "api.api_custom_sources_test",
    "api.api_sources", "api.api_sources_builtin",
    "api.api_sources_builtin_save", "api.api_sources_builtin_reset",
    "api.api_downloads", "api.api_downloads_start", "api.api_downloads_cancel",
}


@bp.before_request
def _block_private_in_public():
    if config.PUBLIC and request.endpoint in _PRIVATE_ONLY_ENDPOINTS:
        return jsonify({"error": "Not available on the public server."}), 403


# ==================== auth / session ====================

@bp.get("/api/session")
def api_session():
    """How the SPA learns who (if anyone) is logged in, and whether the very
    first Owner still needs to be created. Public by design."""
    user = auth.current_user()
    out = {"user": auth.public(user), "needs_setup": not auth.any_users(),
           "public": config.PUBLIC, "site_title": site.get_title(),
           "site_eyebrow": site.get_eyebrow(), "site_short": site.get_short()}
    # Footer totals, for whoever may actually see the gallery. Behind the login
    # wall we don't hand the size of the collection to an anonymous caller.
    if config.PUBLIC or user:
        out["counts"] = {"artists": len(library.artists()),
                         "works": len(library.all_works())}
    return jsonify(out)


@bp.post("/api/setup")
def api_setup():
    """One-time first-run: create the first Owner. Refused once any user exists."""
    if auth.any_users():
        return jsonify({"error": "Setup has already been completed."}), 403
    data = request.get_json(silent=True) or {}
    try:
        user = auth.create_user(data.get("username"), data.get("password"), "owner")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    auth.login_session(auth.get_user(user["username"]))
    return jsonify({"user": user})


@bp.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    rec = auth.verify_credentials(data.get("username"), data.get("password"))
    if not rec:
        return jsonify({"error": "Wrong username or password."}), 401
    auth.login_session(rec)
    return jsonify({"user": auth.public(rec)})


@bp.post("/api/logout")
def api_logout():
    auth.logout_session()
    return jsonify({"ok": True})


@bp.post("/api/account/password")
@auth.require_login
def api_account_password():
    """A signed-in user changing their own password (needs the current one)."""
    data = request.get_json(silent=True) or {}
    me = auth.current_user()
    if not auth.verify_credentials(me["username"], data.get("current_password")):
        return jsonify({"error": "Current password is wrong."}), 400
    try:
        auth.set_password(me["username"], data.get("new_password"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ==================== users (owner only) ====================

@bp.get("/api/users")
@auth.require_role("owner")
def api_users():
    return jsonify({"users": auth.list_users()})


@bp.post("/api/users")
@auth.require_role("owner")
def api_users_create():
    data = request.get_json(silent=True) or {}
    try:
        user = auth.create_user(data.get("username"), data.get("password"), data.get("role"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"user": user})


@bp.post("/api/users/<username>/role")
@auth.require_role("owner")
def api_users_role(username):
    data = request.get_json(silent=True) or {}
    try:
        user = auth.set_role(username, data.get("role"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"user": user})


@bp.post("/api/users/<username>/password")
@auth.require_role("owner")
def api_users_password(username):
    data = request.get_json(silent=True) or {}
    try:
        auth.set_password(username, data.get("password"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.delete("/api/users/<username>")
@auth.require_role("owner")
def api_users_delete(username):
    try:
        auth.delete_user(username)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"deleted": True})


# ==================== invites (owner issues Curator links) ====================

def _invite_url(token):
    """The full accept link the owner emails. request.host_url already reflects
    whatever origin the owner is browsing (LAN, Tailscale name, or the public host)."""
    return request.host_url.rstrip("/") + "/#/invite/" + token


@bp.get("/api/invites")
@auth.require_role("owner")
def api_invites():
    items = [dict(inv, url=_invite_url(inv["token"])) for inv in auth.list_invites()]
    return jsonify({"invites": items})


@bp.post("/api/invites")
@auth.require_role("owner")
def api_invites_create():
    data = request.get_json(silent=True) or {}
    me = auth.current_user()
    try:
        inv = auth.create_invite(data.get("email"), data.get("role") or "curator",
                                 me["username"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    inv["url"] = _invite_url(inv["token"])
    return jsonify({"invite": inv})


@bp.delete("/api/invites/<token>")
@auth.require_role("owner")
def api_invites_revoke(token):
    try:
        auth.revoke_invite(token)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"revoked": True})


@bp.get("/api/invite/<token>")
def api_invite_get(token):
    """Public: what the accept screen shows (invited email + role). No auth."""
    inv = auth.get_invite(token)
    if not inv:
        return jsonify({"error": "This invite link is no longer valid."}), 410
    return jsonify({"invite": inv})


@bp.post("/api/invite/accept")
def api_invite_accept():
    """Public: turn an invite into an account and sign the new user in."""
    data = request.get_json(silent=True) or {}
    try:
        user = auth.accept_invite(data.get("token"), data.get("username"),
                                  data.get("password"))
    except LookupError as e:
        return jsonify({"error": str(e)}), 410
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    auth.login_session(auth.get_user(user["username"]))
    return jsonify({"user": user})


# ==================== collections ====================

def _load_editable(cid):
    """Fetch a collection and confirm the current user may edit it.
    Returns (record, None) on success or (None, error_response) to return."""
    rec = collections.get_collection(cid)
    if not rec:
        return None, (jsonify({"error": "Collection not found."}), 404)
    if not collections.can_edit(rec, auth.current_user()):
        return None, (jsonify({"error": "You don't have permission to edit this collection."}), 403)
    return rec, None


@bp.get("/api/collections")
@auth.require_view
def api_collections():
    return jsonify({"collections": collections.list_summaries(auth.current_user())})


@bp.post("/api/collections")
@auth.require_role("curator")
def api_collections_create():
    data = request.get_json(silent=True) or {}
    try:
        rec = collections.create_collection(
            data.get("title"), data.get("description"), auth.current_user())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"collection": collections.detail(rec, auth.current_user())})


@bp.get("/api/collection/<cid>")
@auth.require_view
def api_collection(cid):
    rec = collections.get_collection(cid)
    if not rec:
        return jsonify({"error": "Collection not found."}), 404
    return jsonify({"collection": collections.detail(rec, auth.current_user())})


@bp.post("/api/collection/<cid>")
@auth.require_login
def api_collection_update(cid):
    rec, err = _load_editable(cid)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        rec = collections.update_collection(
            cid, title=data.get("title"), description=data.get("description"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"collection": collections.detail(rec, auth.current_user())})


@bp.delete("/api/collection/<cid>")
@auth.require_login
def api_collection_delete(cid):
    rec, err = _load_editable(cid)
    if err:
        return err
    collections.delete_collection(cid)
    return jsonify({"deleted": True})


@bp.post("/api/collection/<cid>/works")
@auth.require_login
def api_collection_add_works(cid):
    rec, err = _load_editable(cid)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "No works selected."}), 400
    rec = collections.add_works(cid, [str(i) for i in ids])
    return jsonify({"collection": collections.detail(rec, auth.current_user())})


@bp.post("/api/collection/<cid>/works/remove")
@auth.require_login
def api_collection_remove_works(cid):
    rec, err = _load_editable(cid)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "No works selected."}), 400
    rec = collections.remove_works(cid, [str(i) for i in ids])
    return jsonify({"collection": collections.detail(rec, auth.current_user())})


# ==================== library (browse — any signed-in user) ====================

@bp.get("/api/artists")
@auth.require_view
def api_artists():
    arts = library.artists()
    return jsonify({
        "artists": arts,
        "total_works": len(library.all_works()),
    })


@bp.get("/api/works")
@auth.require_view
def api_works():
    works = library.query_works(
        artist=request.args.get("artist"),
        era=request.args.get("era"),
        medium=request.args.get("medium"),
        style=request.args.get("style"),
        genre=request.args.get("genre"),
        school=request.args.get("school"),
        q=request.args.get("q"),
    )
    return jsonify({"works": works})


@bp.get("/api/facets")
@auth.require_view
def api_facets():
    return jsonify(library.facets())


@bp.get("/api/featured")
@auth.require_view
def api_featured():
    """The work in the home hero. Rotates once a day rather than per request, so
    the front page has a 'today's painting' rather than a slot machine — and
    everyone looking at it sees the same one. Prefers works that have a
    description: those are the ones with something to read on the other side."""
    works = library.all_works()
    if not works:
        return jsonify({"work": None})
    pool = [w for w in works if (w.get("description") or "").strip()] or works
    day = int(time.strftime("%Y%j"))          # year + day-of-year
    return jsonify({"work": pool[day % len(pool)]})


@bp.get("/api/work/<wid>")
@auth.require_view
def api_work(wid):
    w = library.get(wid)
    if not w:
        abort(404)
    return jsonify(w)


# ==================== library management (owner only) ====================

@bp.post("/api/rescan")
@auth.require_role("owner")
def api_rescan():
    library.invalidate()
    st = library.scan(force=True)
    return jsonify({"works": len(st["works"])})


@bp.post("/api/artist/rename")
@auth.require_role("owner")
def api_artist_rename():
    data = request.get_json(silent=True) or {}
    to = (data.get("to") or "").strip()
    frm = data.get("from")
    if isinstance(frm, str):
        frm = [frm]
    frm = [s for s in (frm or []) if s and s.strip()]
    if not to or not frm:
        return jsonify({"error": "'from' and 'to' are required."}), 400
    try:
        moved, errors = library.rename_artist(frm, to)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    # carry the artist's saved bio (if any) over to the new name
    if to.strip().casefold() not in [f.strip().casefold() for f in frm]:
        for f in frm:
            info = artistinfo.load(f)
            if info:
                artistinfo.save(to, info)
                break
    # Curator notes and threads are written about a painter, not a folder — keep
    # them attached through a rename or a merge.
    for f in frm:
        links.rename(f, to)
        threads.rename(f, to)
    return jsonify({"moved": moved, "errors": errors, "to": to})


# Set an artist's representative thumbnail to one of their works (owner only).
@bp.post("/api/artist/cover")
@auth.require_role("owner")
def api_artist_cover():
    data = request.get_json(silent=True) or {}
    artist = (data.get("artist") or "").strip()
    wid = (data.get("work_id") or "").strip()
    if not artist or not wid:
        return jsonify({"error": "artist and work_id are required."}), 400
    w = library.get(wid)
    if not w or (w.get("artist") or "").strip().casefold() != artist.casefold():
        return jsonify({"error": "That work isn't one of this artist's."}), 400
    artistinfo.set_cover(artist, wid)
    return jsonify({"ok": True, "cover": wid})


@bp.post("/api/works/delete")
@auth.require_role("owner")
def api_works_delete():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "No works selected."}), 400
    ids = [str(i) for i in ids]
    # On the public server, tombstone the publish-ids being removed so a later Pull
    # doesn't resurrect them (they're still present in the content repo).
    pids = []
    if config.PUBLIC:
        for wid in ids:
            w = library.get(wid)
            if w and w.get("pid"):
                pids.append(w["pid"])
    deleted, errors = library.delete_works(ids)
    if config.PUBLIC and pids:
        publish.suppress_pids(pids)
    return jsonify({"deleted": deleted, "errors": errors})


# ---------------- artist metadata ----------------

@bp.get("/api/artist_info")
@auth.require_view
def api_artist_info():
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    return jsonify({"name": name, "info": artistinfo.load(name)})


@bp.get("/api/artist/<name>/overview")
@auth.require_view
def api_artist_overview(name):
    """Everything the artist page's header needs in one round trip: the bio, the
    figures for the stats card, and the strongest connections for the strip."""
    conns = links.for_artist(name)
    return jsonify({
        "info": artistinfo.load(name),
        "connections": conns[:3],
        "stats": {
            "connections": len(conns),
            "collections": collections.count_containing_artist(name),
        },
    })


# ==================== connections ====================

@bp.get("/api/connections")
@auth.require_view
def api_connections():
    return jsonify(links.graph())


@bp.get("/api/artist/<name>/connections")
@auth.require_view
def api_artist_connections(name):
    return jsonify({"connections": links.for_artist(name)})


def _may_edit_link(rec, user):
    """Owners edit anything; a curator may only revise their own note."""
    if not user or not rec:
        return False
    if user.get("role") == "owner":
        return True
    return rec.get("created_by") == user.get("username")


@bp.post("/api/links")
@auth.require_role("curator")
def api_links_create():
    data = request.get_json(silent=True) or {}
    try:
        rec = links.create_link(
            data.get("a"), data.get("b"), data.get("type"), data.get("note"),
            directed=data.get("directed"),
            created_by=(auth.current_user() or {}).get("username"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"link": rec})


@bp.post("/api/links/<lid>")
@auth.require_role("curator")
def api_links_update(lid):
    rec = links.get_link(lid)
    if not rec:
        return jsonify({"error": "No such link."}), 404
    if not _may_edit_link(rec, auth.current_user()):
        return jsonify({"error": "That link isn't yours to edit."}), 403
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"link": links.update_link(lid, note=data.get("note"),
                                                  directed=data.get("directed"))})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LookupError as e:
        return jsonify({"error": str(e)}), 404


@bp.delete("/api/links/<lid>")
@auth.require_role("curator")
def api_links_delete(lid):
    rec = links.get_link(lid)
    if not rec:
        return jsonify({"error": "No such link."}), 404
    if not _may_edit_link(rec, auth.current_user()):
        return jsonify({"error": "That link isn't yours to remove."}), 403
    links.delete_link(lid)
    return jsonify({"deleted": lid})


# ---------------- threads ----------------

@bp.get("/api/threads")
@auth.require_view
def api_threads():
    user = auth.current_user()
    out = []
    for t in threads.list_threads():
        out.append(dict(t, can_edit=_may_edit_link(t, user)))
    return jsonify({"threads": out})


@bp.post("/api/threads")
@auth.require_role("curator")
def api_threads_create():
    data = request.get_json(silent=True) or {}
    try:
        rec = threads.create(data.get("title"), data.get("description"),
                             data.get("steps"),
                             created_by=(auth.current_user() or {}).get("username"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"thread": rec})


@bp.post("/api/threads/<tid>")
@auth.require_role("curator")
def api_threads_update(tid):
    rec = threads.get(tid)
    if not rec:
        return jsonify({"error": "No such thread."}), 404
    if not _may_edit_link(rec, auth.current_user()):
        return jsonify({"error": "That thread isn't yours to edit."}), 403
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"thread": threads.update(tid, data.get("title"),
                                                 data.get("description"), data.get("steps"))})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LookupError as e:
        return jsonify({"error": str(e)}), 404


@bp.delete("/api/threads/<tid>")
@auth.require_role("curator")
def api_threads_delete(tid):
    rec = threads.get(tid)
    if not rec:
        return jsonify({"error": "No such thread."}), 404
    if not _may_edit_link(rec, auth.current_user()):
        return jsonify({"error": "That thread isn't yours to remove."}), 403
    threads.delete(tid)
    return jsonify({"deleted": tid})


@bp.post("/api/artist_info/lookup")
@auth.require_role("owner")
def api_artist_lookup():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        found = artistinfo.fetch_from_wikidata(name)
    except Exception as e:
        return jsonify({"error": "Wikidata lookup failed: %s" % e}), 502
    if not found:
        return jsonify({"info": None, "message": "No matching artist found on Wikidata."})
    # Deliberately does NOT save: this hands the fields to the bio form, and the
    # owner decides. It used to write straight to disk, which quietly threw away
    # anything they'd typed and could redraw the connections map behind their back.
    return jsonify({"info": found, "matched_label": found.get("matched_label")})


@bp.post("/api/artist_info/ai_lookup")
@auth.require_role("owner")
def api_artist_ai_lookup():
    """Research an artist with the same AI the placard editor uses. Returns fields
    for review — never saves — plus the request/response trace."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    trace = {}
    try:
        fields = ai.autofill_artist(name, hint=data.get("hint"), trace=trace)
    except ai.AIError as e:
        return jsonify({"error": str(e), "trace": trace}), 502
    return jsonify({"fields": fields, "trace": trace})


@bp.post("/api/artist_info/save")
@auth.require_role("owner")
def api_artist_save():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    saved = artistinfo.save(name, artistinfo.normalize_manual(data))
    return jsonify({"info": saved})


# Batch metadata lookup: search the web and fill a work's field(s). Owner-only;
# the frontend calls it once per selected work so each request stays quick.
@bp.post("/api/work/<wid>/find_metadata")
@auth.require_role("owner")
def api_work_find_metadata(wid):
    data = request.get_json(silent=True) or {}
    fields = data.get("fields") or ["medium"]
    overwrite = bool(data.get("overwrite"))
    w = library.scan()["by_id"].get(wid)
    if not w:
        abort(404)
    want = [f for f in fields if overwrite or not w.get(f)]  # don't clobber existing values
    found = metadata.find_fields(w, want) if want else {}
    out = library.update_work(wid, found) if found else w
    return jsonify({"work": out, "found": found})


@bp.post("/api/work/<wid>")
@auth.require_role("owner")
def api_work_update(wid):
    data = request.get_json(silent=True) or {}
    fields = {k: data[k] for k in ("title", "artist", "date", "medium", "style",
                                   "genre", "school", "description") if k in data}
    try:
        w = library.update_work(wid, fields)
    except KeyError:
        abort(404)
    if not w:
        return jsonify({"error": "update failed"}), 500
    return jsonify({"work": w})


# ---------------- Auto-fill (AI metadata lookup — owner only) ----------------

@bp.get("/api/ai/config")
@auth.require_role("owner")
def api_ai_config():
    return jsonify(ai.public_config())


@bp.post("/api/ai/config")
@auth.require_role("owner")
def api_ai_config_save():
    data = request.get_json(silent=True) or {}
    return jsonify(ai.set_config(model=data.get("model"), api_key=data.get("api_key")))


# Research one work via the configured model and return the fields it found. Does
# NOT save — the editor populates the form so the owner can review before saving.
@bp.post("/api/work/<wid>/autofill")
@auth.require_role("owner")
def api_work_autofill(wid):
    w = library.scan()["by_id"].get(wid)
    if not w:
        abort(404)
    # The trace goes back either way — a failed call is exactly when the owner
    # needs to see what was sent and what came back.
    data = request.get_json(silent=True) or {}
    trace = {}
    try:
        fields = ai.autofill(w, hint=data.get("hint"), trace=trace)
    except ai.AIError as e:
        return jsonify({"error": str(e), "trace": trace}), 502
    return jsonify({"fields": fields, "trace": trace})


# Batch "Get metadata": fill several works at once, ONE AI call per distinct
# artist (works on an artist page all share one). Saves the found fields to each
# sidecar, filling only blanks unless overwrite is set. Owner-only.
@bp.post("/api/works/autofill_batch")
@auth.require_role("owner")
def api_works_autofill_batch():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    overwrite = bool(data.get("overwrite"))
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "No works selected."}), 400
    by_id = library.scan()["by_id"]
    works = [by_id[str(i)] for i in ids if by_id.get(str(i))]
    if not works:
        abort(404)

    groups = {}
    for w in works:
        groups.setdefault((w.get("artist") or "").strip(), []).append(w)

    updates, errors, calls = {}, [], 0
    for artist, ws in groups.items():
        try:
            results = ai.autofill_many(artist, ws)
            calls += 1
        except ai.AIError as e:
            errors.append(str(e))
            continue
        for w, found in zip(ws, results):
            want = {k: v for k, v in found.items() if overwrite or not w.get(k)}
            if want:
                updates[w["id"]] = want
    filled = library.update_works_meta(updates)
    if not calls and errors:
        return jsonify({"error": errors[0]}), 502
    out = {"filled": filled, "calls": calls, "artists": len(groups), "requested": len(works)}
    if errors:
        out["error"] = errors[0]
    return jsonify(out)


# Library totals + app version for the Settings header (owner only).
@bp.get("/api/stats")
@auth.require_role("owner")
def api_stats():
    s = library.stats()
    s["version"] = config.VERSION
    return jsonify(s)


# Site title (owner-set branding for the tab + header).
@bp.post("/api/site")
@auth.require_role("owner")
def api_site_save():
    data = request.get_json(silent=True) or {}
    out = {}
    if "title" in data:
        out["site_title"] = site.set_title(data.get("title"))
    if "eyebrow" in data:
        out["site_eyebrow"] = site.set_eyebrow(data.get("eyebrow"))
    if "short" in data:
        out["site_short"] = site.set_short(data.get("short"))
    out.setdefault("site_title", site.get_title())
    out.setdefault("site_eyebrow", site.get_eyebrow())
    out.setdefault("site_short", site.get_short())
    return jsonify(out)


# ==================== publish / pull (public snapshot) ====================

# On the LOCAL box: push selected works (reduced images + placards) to the content
# repo. On the PUBLIC box: pull them in. Repo config is local-box only.

@bp.get("/api/publish/status")
@auth.require_role("owner")
def api_publish_status():
    return jsonify(publish.repo_status())


@bp.post("/api/publish/config")
@auth.private_only
@auth.require_role("owner")
def api_publish_config():
    data = request.get_json(silent=True) or {}
    return jsonify(publish.set_repo_path(data.get("repo_path")))


@bp.post("/api/publish")
@auth.private_only
@auth.require_role("owner")
def api_publish():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "No works selected."}), 400
    try:
        result = publish.publish_works([str(i) for i in ids])
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@bp.post("/api/publish/new")
@auth.private_only
@auth.require_role("owner")
def api_publish_new():
    """Export everything imported since the last export (works with no pid yet)."""
    try:
        result = publish.publish_new()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@bp.post("/api/pull")
@auth.public_only
@auth.require_role("owner")
def api_pull():
    try:
        result = publish.pull_and_import()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


# ---------------- custom sources (Settings — owner only) ----------------

@bp.get("/api/custom_sources")
@auth.require_role("owner")
def api_custom_sources():
    return jsonify({"sources": custom.list_defs(), "presets": custom.PRESETS,
                    "field_keys": custom.FIELD_KEYS})


@bp.post("/api/custom_sources")
@auth.require_role("owner")
def api_custom_sources_save():
    data = request.get_json(silent=True) or {}
    try:
        cleaned = custom.upsert(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"source": cleaned})


@bp.delete("/api/custom_sources/<sid>")
@auth.require_role("owner")
def api_custom_sources_delete(sid):
    return jsonify({"removed": custom.remove(sid)})


@bp.post("/api/custom_sources/test")
@auth.require_role("owner")
def api_custom_sources_test():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Enter a test artist name."}), 400
    return jsonify(custom.dry_run(data.get("def") or {}, query))


# ==================== images (any signed-in user) ====================

def _img_response(path, mimetype, download_name=None):
    resp = send_file(str(path), mimetype=mimetype, conditional=True,
                     max_age=31536000, download_name=download_name)
    # Versioned URLs (?v=<mtime>) name an exact rendering, so cache them forever;
    # unversioned ones (e.g. artist covers by bare id) revalidate daily instead.
    if request.args.get("v"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.get("/thumb/<wid>")
@auth.require_view
def thumb(wid):
    w = library.get(wid)
    if not w:
        abort(404)
    try:
        path = thumbs.thumb_for(w)
    except Exception as e:
        print("thumb failed for %s: %s" % (w["rel"], e), flush=True)
        abort(500)
    return _img_response(path, "image/webp")


@bp.get("/img/<wid>")
@auth.require_view
def img(wid):
    """Screen-sized derivative for the fullscreen viewer — small and fast. The full
    original is at /orig."""
    w = library.get(wid)
    if not w or not (config.LIBRARY_DIR / w["rel"]).exists():
        abort(404)
    try:
        path = thumbs.view_for(w)
    except Exception as e:
        print("view failed for %s: %s" % (w["rel"], e), flush=True)
        abort(500)
    return _img_response(path, "image/webp")


@bp.get("/orig/<wid>")
@auth.require_view
def orig(wid):
    """The full-resolution original (browser-displayable), for a proper look or a
    download. Large — only fetched via the viewer's 'full resolution' link."""
    w = library.get(wid)
    if not w or not (config.LIBRARY_DIR / w["rel"]).exists():
        abort(404)
    # Non-web formats (e.g. a TIFF a museum served with a .jpg name) are converted
    # to a cached JPEG so the browser can display them; web formats serve as-is.
    path = thumbs.display_for(w)
    mimetype = "image/jpeg" if str(path).endswith(".disp.jpg") else None
    return _img_response(path, mimetype, download_name=os.path.basename(w["rel"]))


# ==================== downloads (owner only) ====================

@bp.get("/api/sources")
@auth.require_role("owner")
def api_sources():
    return jsonify({"sources": list_sources()})


# Built-in source configuration — owner oversight over how each searches/filters.
@bp.get("/api/sources/builtin")
@auth.require_role("owner")
def api_sources_builtin():
    return jsonify({"sources": list_builtin_configs()})


@bp.post("/api/sources/builtin/<sid>")
@auth.require_role("owner")
def api_sources_builtin_save(sid):
    data = request.get_json(silent=True) or {}
    try:
        cfg = set_builtin_config(sid, data.get("values") or {})
    except KeyError:
        abort(404)
    return jsonify({"source": cfg})


@bp.post("/api/sources/builtin/<sid>/reset")
@auth.require_role("owner")
def api_sources_builtin_reset(sid):
    try:
        cfg = reset_builtin_config(sid)
    except KeyError:
        abort(404)
    return jsonify({"source": cfg})


@bp.get("/api/downloads")
@auth.require_role("owner")
def api_downloads():
    tail = min(int(request.args.get("tail", 12)), 100)
    return jsonify({"jobs": [j.to_dict(tail) for j in manager.jobs()]})


@bp.post("/api/downloads")
@auth.require_role("owner")
def api_downloads_start():
    data = request.get_json(silent=True) or {}
    source = (data.get("source") or "").strip()
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Enter an artist name."}), 400
    try:
        get_source(source)
    except KeyError:
        return jsonify({"error": "Unknown source '%s'." % source}), 400

    opts = {}
    for key, lo, hi in (("max_items", 1, 10000), ("max_px", 256, 100000)):
        val = data.get(key)
        if val not in (None, ""):
            try:
                opts[key] = max(lo, min(hi, int(val)))
            except (TypeError, ValueError):
                return jsonify({"error": "%s must be a number." % key}), 400

    job = manager.start(source, query, opts)
    return jsonify(job.to_dict())


@bp.post("/api/downloads/<int:jid>/cancel")
@auth.require_role("owner")
def api_downloads_cancel(jid):
    job = manager.get(jid)
    if not job:
        abort(404)
    job.cancel()
    return jsonify(job.to_dict())
