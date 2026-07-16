"use strict";

const $ = (sel) => document.querySelector(sel);
const app = $("#app");

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* Case and accents flattened, matching names.fold() on the server. Nobody hunting
   for Géricault stops to type the accents, and half our sources can't spell them
   anyway. NFD splits an "é" into "e" + a combining mark; the range drops the mark. */
function fold(s) {
  return String(s == null ? "" : s).normalize("NFD")
    .replace(/[̀-ͯ]/g, "").toLowerCase();
}

/* ============================== session ============================== */

let SESSION = { user: null, needs_setup: false, public: false };
function role() { return SESSION.user ? SESSION.user.role : null; }
function isOwner() { return role() === "owner"; }
function canCurate() { return role() === "owner" || role() === "curator"; }
// The public "snapshot" deployment: anyone may browse anonymously, and all
// authoring/download tools are gone (fed instead by the owner's Pull button).
function isPublic() { return !!SESSION.public; }
function setUser(u) {
  SESSION = Object.assign({}, SESSION, { user: u, needs_setup: false });
}

// Owner-set wordmark (from /api/session), shown in the tab + header.
function siteTitle() { return (SESSION.site_title || "").trim() || "The Gallery"; }
function siteEyebrow() { return (SESSION.site_eyebrow || "").trim(); }
function siteShort() { return (SESSION.site_short || "").trim(); }
function applyTitle() {
  const t = siteTitle(), eb = siteEyebrow();
  document.title = t;                       // the tab always gets the real name
  const name = document.querySelector(".brand-name.full");
  if (name) name.textContent = t;
  // The phone wordmark. Falls back to the full title when no short one is set,
  // so a narrow header is never blank — CSS picks which of the two is on show.
  const compact = document.querySelector(".brand-name.compact");
  if (compact) compact.textContent = siteShort() || t;
  // Two-tier wordmark: the eyebrow is optional, so collapse it when unset
  // rather than leaving an empty gold line above the title.
  const e = document.querySelector(".brand-eyebrow");
  if (e) { e.textContent = eb; e.hidden = !eb; }
  // Dropping the eyebrow makes the header shorter, and anything sticking to its
  // underside (the Settings index) has to know by how much — otherwise the page
  // scrolls through the gap between them.
  const bar = $("#topbar");
  if (bar) document.documentElement.style.setProperty("--topbar-h", bar.offsetHeight + "px");
}

/* The footer carries the wordmark and the size of the collection. Hidden until
   there's a gallery to describe — the login wall and setup get a bare page. */
function renderFoot() {
  const f = $("#foot");
  if (!f) return;
  const c = SESSION.counts;
  if (!c) { f.hidden = true; return; }
  const parts = [c.artists + (c.artists === 1 ? " painter" : " painters"),
                 c.works + (c.works === 1 ? " work" : " works"),
                 "maintained with care"];
  f.querySelector(".foot-mark").textContent =
    [siteEyebrow(), siteTitle()].filter(Boolean).join(" ");
  f.querySelector(".foot-stats").textContent = parts.join(" · ");
  f.hidden = false;
}

/* Every view's content sits in this container; the home hero deliberately
   renders outside it so it can span the full width of the window. */
function page(html, cls) {
  return '<div class="page' + (cls ? " " + cls : "") + '">' + html + "</div>";
}

/* The phone menu: nav and the user chip fold behind the three lines rather than
   stacking down the page. Desktop never sees it — the panel is a media query. */
function setNavOpen(open) {
  const bar = $("#topbar"), btn = $("#navtoggle");
  if (!bar || !btn) return;
  bar.classList.toggle("navopen", open);
  btn.setAttribute("aria-expanded", String(open));
}

function wireNavToggle() {
  const btn = $("#navtoggle");
  if (!btn) return;
  btn.addEventListener("click", () => setNavOpen(!$("#topbar").classList.contains("navopen")));
  // Tapping outside the open panel should shut it, but not a tap inside it.
  document.addEventListener("click", (e) => {
    if (!$("#topbar").classList.contains("navopen")) return;
    if (e.target.closest("#topbar")) return;
    setNavOpen(false);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setNavOpen(false); });
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.status + " " + r.statusText, body = null;
    try { body = await r.json(); if (body && body.error) msg = body.error; } catch (e) {}
    // A 401 while we thought we were signed in means the session lapsed — drop to login.
    if (r.status === 401 && SESSION.user) {
      SESSION.user = null;
      renderNav();
      loginView();
    }
    const err = new Error(msg);
    err.status = r.status;
    err.body = body;      // keep the payload: failures carry detail worth showing
    throw err;
  }
  return r.json();
}

function errbox(e) {
  app.innerHTML = page('<div class="errbox">Something went wrong: ' + esc(e.message || e) + "</div>");
}

/* Cache-busting image URLs: ?v=<mtime> names an exact rendering, so the browser
   may cache it forever (the server marks versioned image URLs immutable) yet
   refetches when the file changes. thumb = grid, view = fullscreen (screen-
   sized), orig = full resolution. */
function imgVer(w) { return "?v=" + Math.floor(w.mtime || 0); }
function thumbSrc(w) { return "/thumb/" + w.id + imgVer(w); }
function viewSrc(w) { return "/img/" + w.id + imgVer(w); }
function origSrc(w) { return "/orig/" + w.id + imgVer(w); }

/* ============================== router ============================== */

let pollTimer = null;
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
function ensurePolling() { if (!pollTimer) pollTimer = setInterval(refreshJobs, 1500); }

/* multi-select state for deleting / collecting works; reset whenever the route changes */
const SEL = { on: false, ids: new Set() };
function resetSel() { SEL.on = false; SEL.ids.clear(); }

function setNav(which) {
  document.querySelectorAll("#mainnav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === which);
  });
}

function renderNav() {
  const nav = $("#mainnav"), ub = $("#userbox");
  if (!nav || !ub) return;
  // Private box with nobody signed in: the whole site sits behind the login wall.
  // Public box: anyone may browse, so we still build the nav for anonymous visitors.
  if (!SESSION.user && !isPublic()) { nav.innerHTML = ""; ub.innerHTML = ""; return; }
  const links = [
    ["#/", "home", "Artists"],
    ["#/browse/era", "browse", "Browse"],
    ["#/connections", "connections", "Connections"],
    ["#/collections", "collections", "Collections"],
  ];
  if (isOwner()) links.push(["#/settings", "settings", "Settings"]);
  // No "Add artist" on the public snapshot — even for the owner. It's a nav link
  // now rather than a filled button: gold is wayfinding here, not a call to action.
  if (isOwner() && !isPublic()) links.push(["#/add", "add", "Add artist"]);
  nav.innerHTML = links.map(([href, key, label]) =>
    '<a href="' + href + '" data-nav="' + key + '">' + esc(label) + "</a>").join("") +
    '<span class="nav-div" aria-hidden="true"></span>';
  if (SESSION.user) {
    const u = SESSION.user;
    ub.innerHTML =
      '<span class="who"><span class="uname">' + esc(u.username) + "</span>" +
      '<span class="role-badge ' + esc(u.role) + '">' + esc(u.role) + "</span></span>" +
      '<button id="logout" class="linkbtn">Log out</button>';
    $("#logout").addEventListener("click", doLogout);
  } else {
    // Anonymous visitor on the public site: offer sign-in (accounts are invite-only).
    ub.innerHTML = '<a href="#/login" class="signin">Sign in</a>';
  }
}

/* Re-read the session and repaint the chrome. Used after any sign-in/out, since
   who you are decides the nav, and whether you may see the collection at all
   decides whether the footer has counts to show. */
async function refreshSession() {
  try {
    SESSION = await (await fetch("/api/session")).json();
  } catch (e) {
    SESSION = { user: null, needs_setup: false };
  }
  applyTitle();
  renderNav();
  renderFoot();
}

async function doLogout() {
  try { await api("/api/logout", { method: "POST" }); } catch (e) {}
  setUser(null);
  await refreshSession();
  route();
}

function goHome() {
  if (location.hash && location.hash !== "#/" && location.hash !== "#") location.hash = "#/";
  else route();
}

function route() {
  closeViewer();
  stopPolling();
  resetSel();
  setNavOpen(false);          // a nav tap navigates AND closes the menu behind it
  // Split the query off first: #/connections?artist=…&mode=map deep-links the map.
  const raw = location.hash.slice(1) || "/";
  const qi = raw.indexOf("?");
  const query = new URLSearchParams(qi < 0 ? "" : raw.slice(qi + 1));
  const segs = (qi < 0 ? raw : raw.slice(0, qi))
    .split("/").filter(Boolean).map(decodeURIComponent);
  // Accepting an invite works with no session, in either mode — check it first.
  if (segs[0] === "invite" && segs[1]) return acceptInviteView(segs[1]);
  // Auth gates: first-run setup, then the login wall — but the public snapshot
  // lets anyone browse without an account.
  if (SESSION.needs_setup) return setupView();
  if (!SESSION.user && !isPublic()) return loginView();

  window.scrollTo(0, 0);
  if (segs.length === 0) return homeView();
  if (segs[0] === "login") return SESSION.user ? void goHome() : loginView();
  if (segs[0] === "artist" && segs[1]) return artistView(segs[1]);
  if (segs[0] === "browse") return browseView(segs[1] || "era", segs[2] || null);
  if (segs[0] === "connections")
    return connectionsView(query.get("artist"), query.get("mode"));
  if (segs[0] === "collections") return collectionsView();
  if (segs[0] === "collection" && segs[1]) return collectionView(segs[1]);
  // Adding art doesn't exist on the public box, even for the owner.
  if (segs[0] === "add") return (isOwner() && !isPublic()) ? addView(segs[1] || "") : void goHome();
  if (segs[0] === "settings") return isOwner() ? settingsView() : void goHome();
  homeView();
}
window.addEventListener("hashchange", route);

async function boot() {
  try {
    const r = await fetch("/api/session");
    SESSION = await r.json();
  } catch (e) {
    SESSION = { user: null, needs_setup: false };
  }
  applyTitle();
  renderNav();
  renderFoot();
  wireNavToggle();
  route();
}

/* ============================== auth views ============================== */

/* `title` is plain text and gets escaped here — callers must NOT pre-escape it,
   or an apostrophe in the site name arrives as a visible &#39;. `sub` is trusted
   markup (it carries <b> and links). */
function authShell(title, sub, formHtml) {
  renderNav();
  app.innerHTML =
    '<div class="authwrap"><div class="authcard">' +
    "<h1>" + esc(title) + "</h1>" +
    (sub ? '<p class="sub">' + sub + "</p>" : "") +
    formHtml + "</div></div>";
}

function setupView() {
  authShell(
    "Welcome to " + siteTitle(),
    "Create the first account — the <b>Owner</b>, who runs the gallery and adds everyone else.",
    '<form class="authform" id="setupform">' +
    "<label>Username<input id=\"su-user\" autocomplete=\"username\"></label>" +
    "<label>Password<input id=\"su-pass\" type=\"password\" autocomplete=\"new-password\"></label>" +
    "<label>Confirm password<input id=\"su-pass2\" type=\"password\" autocomplete=\"new-password\"></label>" +
    '<button type="submit" class="cta-btn">Create Owner account</button>' +
    '<p class="formmsg err" id="su-msg"></p></form>');
  $("#setupform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#su-msg");
    const u = $("#su-user").value.trim(), p = $("#su-pass").value, p2 = $("#su-pass2").value;
    if (p !== p2) { msg.textContent = "The two passwords don't match."; return; }
    try {
      const r = await api("/api/setup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: p }),
      });
      setUser(r.user);
      await refreshSession();
      goHome();
    } catch (err) { msg.textContent = err.message; }
  });
  $("#su-user").focus();
}

function loginView() {
  authShell(
    siteTitle(),
    "Please sign in to continue.",
    '<form class="authform" id="loginform">' +
    "<label>Username<input id=\"li-user\" autocomplete=\"username\"></label>" +
    "<label>Password<input id=\"li-pass\" type=\"password\" autocomplete=\"current-password\"></label>" +
    '<button type="submit" class="cta-btn">Sign in</button>' +
    '<p class="formmsg err" id="li-msg"></p></form>');
  $("#loginform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#li-msg");
    try {
      const r = await api("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: $("#li-user").value.trim(), password: $("#li-pass").value }),
      });
      setUser(r.user);
      await refreshSession();
      goHome();
    } catch (err) { msg.textContent = err.message; }
  });
  $("#li-user").focus();
}

/* Accept an owner-issued invite: pick a username + password and the account is
   created with the invited role, then signed in. Reachable with no session. */
async function acceptInviteView(token) {
  let inv;
  try {
    inv = (await api("/api/invite/" + encodeURIComponent(token))).invite;
  } catch (e) {
    authShell("Invitation",
      "",
      '<p class="sub">' + esc(e.message || "This invite link is no longer valid.") + "</p>" +
      '<p style="margin-top:14px"><a href="#/">Go to the gallery</a></p>');
    return;
  }
  authShell(
    "Join " + siteTitle(),
    "You've been invited as a <b>" + esc(inv.role) + "</b>" +
      (inv.email ? " — " + esc(inv.email) : "") + ". Pick a username and password.",
    '<form class="authform" id="acceptform">' +
    "<label>Username<input id=\"iv-user\" autocomplete=\"username\"></label>" +
    "<label>Password<input id=\"iv-pass\" type=\"password\" autocomplete=\"new-password\"></label>" +
    "<label>Confirm password<input id=\"iv-pass2\" type=\"password\" autocomplete=\"new-password\"></label>" +
    '<button type="submit" class="cta-btn">Create account</button>' +
    '<p class="formmsg err" id="iv-msg"></p></form>');
  $("#acceptform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#iv-msg");
    const u = $("#iv-user").value.trim(), p = $("#iv-pass").value, p2 = $("#iv-pass2").value;
    if (p !== p2) { msg.textContent = "The two passwords don't match."; return; }
    try {
      const r = await api("/api/invite/accept", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token, username: u, password: p }),
      });
      setUser(r.user);
      await refreshSession();
      goHome();
    } catch (err) { msg.textContent = err.message; }
  });
  $("#iv-user").focus();
}

/* ============================== works grid + selection ============================== */

function workFigure(w, i, showArtist) {
  const meta = [showArtist ? w.artist : null, w.date || w.year, w.medium]
    .filter(Boolean).join(" · ");
  // The public snapshot serves one reduced size for every work, so the pixel
  // dimensions say nothing there — only show them on the private box.
  const dim = (!isPublic() && w.width && w.height)
    ? '<span class="wdim">' + w.width + " × " + w.height + "</span>"
    : "";
  // Only offer the placard where there's something to read.
  const info = w.description
    ? '<button class="winfo" type="button" title="Show the placard"' +
      ' aria-label="Show the placard for ' + esc(w.title) + '">i</button>'
    : "";
  return (
    '<figure class="work" data-i="' + i + '" data-id="' + w.id + '">' +
      '<div class="wimg"><img src="' + thumbSrc(w) + '" loading="lazy" alt="">' +
      '<span class="checkmark" aria-hidden="true"></span>' + dim + "</div>" +
      '<figcaption><div class="wtext"><div class="wt">' + esc(w.title) + "</div>" +
      (meta ? '<div class="wm">' + esc(meta) + "</div>" : "") +
      "</div>" + info + "</figcaption></figure>"
  );
}

/* The available Select-mode actions depend on role + page. On museum grids a
   curator can collect and an owner can also delete; on a collection they own an
   editor can remove. Visitors get no toolbar at all (the grid stays view-only). */
function browseCtx(opts) {
  opts = opts || {};
  const actions = [];
  if (canCurate()) actions.push("collect");
  // Authoring (add/edit/AI/publish) lives only on the private box; the public
  // snapshot is fed by Pull. Curating what's on show — deleting works and choosing
  // an artist's thumbnail — is allowed on both boxes.
  if (isOwner()) {
    if (!isPublic()) {
      // Artist page: batch "Get metadata" via the AI (one call per artist). Browse
      // grids mix artists, so they keep the free per-work Wikidata "Find metadata".
      actions.push(opts.artist ? "aimeta" : "metadata");
      if (opts.artist) actions.push("publish");
    }
    if (opts.artist) actions.push("setcover");   // artist pages only
    actions.push("pinhero");                     // any grid — it's one work, anywhere
    actions.push("delete");
  }
  return { actions, artist: opts.artist };
}
function collectionCtx(c) {
  return c.can_edit ? { actions: ["uncollect"], collectionId: c.id } : { actions: [] };
}

/* `head` puts the Select control on the section heading's baseline (the artist
   page); without one the toolbar just sits above the grid. */
function worksSection(works, showArtist, ctx, head) {
  ctx = ctx || { actions: [] };
  const tools = ctx.actions.length
    ? '<div class="worktools"><button id="selbtn" class="toolbtn">Select</button><span id="selctl"></span></div>'
    : "";
  return (
    (head ? '<div class="sechead">' + head + tools + "</div>" : tools) +
    '<div class="masonry" id="grid">' +
    works.map((w, i) => workFigure(w, i, showArtist)).join("") + "</div>"
  );
}

function bindWorks(works, showArtist, rerender, ctx) {
  ctx = ctx || { actions: [] };
  const grid = $("#grid");
  grid.addEventListener("click", (e) => {
    const fig = e.target.closest(".work");
    if (!fig) return;
    // The info button is an explicit target: it never selects or opens the viewer.
    if (e.target.closest(".winfo")) {
      const w = works[parseInt(fig.dataset.i, 10)];
      if (w) workInfoDialog(w);
      return;
    }
    if (SEL.on) {
      const id = fig.dataset.id;
      if (SEL.ids.has(id)) SEL.ids.delete(id); else SEL.ids.add(id);
      fig.classList.toggle("selected", SEL.ids.has(id));
      renderSelCtl(works, rerender, ctx);
    } else {
      openViewer(works, parseInt(fig.dataset.i, 10));
    }
  });
  const selbtn = $("#selbtn");
  if (selbtn) {
    selbtn.addEventListener("click", () => {
      SEL.on = !SEL.on;
      if (!SEL.on) {
        SEL.ids.clear();
        grid.querySelectorAll(".work.selected").forEach((f) => f.classList.remove("selected"));
      }
      renderSelCtl(works, rerender, ctx);
    });
    renderSelCtl(works, rerender, ctx);
  }
}

function renderSelCtl(works, rerender, ctx) {
  ctx = ctx || { actions: [] };
  const btn = $("#selbtn");
  if (!btn) return;
  const grid = $("#grid");
  btn.textContent = SEL.on ? "Done" : "Select";
  btn.classList.toggle("active", SEL.on);
  if (grid) grid.classList.toggle("selecting", SEL.on);
  const ctl = $("#selctl");
  if (!SEL.on) { ctl.innerHTML = ""; return; }
  const all = SEL.ids.size === works.length && works.length > 0;
  const n = SEL.ids.size;
  const tag = n ? " " + n : "";
  let html = '<button id="selall" class="linkbtn">' + (all ? "Select none" : "Select all") + "</button>";
  if (ctx.actions.includes("collect"))
    html += '<button id="selcollect" class="toolbtn"' + (n ? "" : " disabled") + ">Add to collection" + tag + "</button>";
  if (ctx.actions.includes("metadata"))
    html += '<button id="selmeta" class="toolbtn"' + (n ? "" : " disabled") + ">Find metadata" + tag + "</button>";
  if (ctx.actions.includes("aimeta"))
    html += '<button id="selaimeta" class="toolbtn"' + (n ? "" : " disabled") + ">Get metadata" + tag + "</button>";
  if (ctx.actions.includes("setcover"))
    html += '<button id="selcover" class="toolbtn"' + (n === 1 ? "" : " disabled") +
      ' title="Pick exactly one work">Set as thumbnail</button>';
  if (ctx.actions.includes("pinhero"))
    html += '<button id="selpin" class="toolbtn"' + (n === 1 ? "" : " disabled") +
      ' title="Show this painting on the front page. Pick exactly one work.">Pin to hero</button>';
  if (ctx.actions.includes("publish"))
    html += '<button id="selpublish" class="toolbtn"' + (n ? "" : " disabled") +
      ' title="Push these works to the public server">Push to public' + tag + "</button>";
  if (ctx.actions.includes("uncollect"))
    html += '<button id="seluncollect" class="danger"' + (n ? "" : " disabled") + ">Remove" + tag + "</button>";
  if (ctx.actions.includes("delete"))
    html += '<button id="seldel" class="danger"' + (n ? "" : " disabled") + ">Delete" + tag + "</button>";
  ctl.innerHTML = html;
  $("#selall").addEventListener("click", () => {
    if (all) SEL.ids.clear();
    else works.forEach((w) => SEL.ids.add(w.id));
    grid.querySelectorAll(".work").forEach((f) =>
      f.classList.toggle("selected", SEL.ids.has(f.dataset.id)));
    renderSelCtl(works, rerender, ctx);
  });
  const collect = $("#selcollect");
  if (collect) collect.addEventListener("click", () => addSelectionToCollection(rerender));
  const unc = $("#seluncollect");
  if (unc) unc.addEventListener("click", () => removeSelectionFromCollection(ctx.collectionId, rerender));
  const del = $("#seldel");
  if (del) del.addEventListener("click", () => deleteSelection(rerender));
  const meta = $("#selmeta");
  if (meta) meta.addEventListener("click", () => findSelectionMetadata(works, rerender));
  const aimeta = $("#selaimeta");
  if (aimeta) aimeta.addEventListener("click", () => getSelectionMetadata(ctx.artist, rerender));
  const cover = $("#selcover");
  if (cover) cover.addEventListener("click", () => setArtistCover(ctx.artist, rerender));
  const pin = $("#selpin");
  if (pin) pin.addEventListener("click", () => pinHero(rerender));
  const pub = $("#selpublish");
  if (pub) pub.addEventListener("click", () => publishSelection(ctx.artist, rerender));
}

/* "Set as thumbnail": make the one selected work the artist's representative
   cover on the Artists grid. Enabled only when exactly one work is selected. */
async function setArtistCover(artist, rerender) {
  const ids = Array.from(SEL.ids);
  if (ids.length !== 1 || !artist) return;
  try {
    await api("/api/artist/cover", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ artist: artist, work_id: ids[0] }),
    });
    resetSel();
    toast("Thumbnail set for " + artist + ".");
    if (rerender) rerender();
  } catch (e) { alert(e.message); }
}

/* "Pin to hero": make the one selected work the painting that greets visitors on
   the front page, instead of the daily rotation. Unpin from Settings → Display. */
async function pinHero(rerender) {
  const ids = Array.from(SEL.ids);
  if (ids.length !== 1) return;
  try {
    const r = await api("/api/featured", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ work_id: ids[0] }),
    });
    resetSel();
    toast("“" + (r.featured.title || "That work") + "” now greets visitors on the front page.");
    if (rerender) rerender();
  } catch (e) { toast(e.message); }
}

/* "Find metadata": for each selected work, search the web and fill any missing
   fields (medium for now). Runs one work at a time so each request stays quick
   and we can show live progress; skips works that already have the field. */
async function findSelectionMetadata(works, rerender) {
  const ids = Array.from(SEL.ids);
  if (!ids.length) return;
  const total = ids.length;
  let done = 0, filled = 0;
  toast("Finding metadata… 0/" + total);
  for (const id of ids) {
    try {
      const r = await api("/api/work/" + encodeURIComponent(id) + "/find_metadata", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fields: ["medium"] }),
      });
      if (r.found && r.found.medium) filled++;
    } catch (e) { /* keep going through the batch */ }
    done++;
    toast("Finding metadata… " + done + "/" + total + " · " + filled + " filled");
  }
  toast("Filled medium on " + filled + " of " + total + " work" + (total === 1 ? "" : "s") + ".");
  resetSel();
  if (rerender) rerender();
}

/* "Get metadata" (artist page): send all the selected works to the AI in ONE
   call for the artist; it fills any blank date/medium/genre/description on each.
   Saved server-side (no per-work review), so this is owner-only and batched. */
async function getSelectionMetadata(artist, rerender) {
  const ids = Array.from(SEL.ids);
  if (!ids.length) return;
  const btn = $("#selaimeta");
  if (btn) { btn.disabled = true; btn.textContent = "Getting metadata…"; }
  toast("Getting metadata for " + ids.length + " work" + (ids.length === 1 ? "" : "s") +
        "… this can take a moment.");
  try {
    const r = await api("/api/works/autofill_batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids }),
    });
    resetSel();
    let msg = "Filled metadata on " + r.filled + " of " + (r.requested || ids.length) +
              " work" + ((r.requested || ids.length) === 1 ? "" : "s") + ".";
    if (r.error) msg += " (" + r.error + ")";
    toast(msg);
    if (rerender) rerender();
  } catch (e) {
    toast(e.message);
    if (btn) { btn.disabled = false; btn.textContent = "Get metadata"; }
  }
}

/* "Push to public": copy the selected works (reduced images + completed placards)
   into the content repo and git-push them to the public server. Re-pushing an
   already-published work updates it in place. Owner-only, private box. */
async function publishSelection(artist, rerender) {
  const ids = Array.from(SEL.ids);
  if (!ids.length) return;
  const btn = $("#selpublish");
  if (btn) { btn.disabled = true; btn.textContent = "Pushing…"; }
  toast("Pushing " + ids.length + " work" + (ids.length === 1 ? "" : "s") +
        " to the public server…");
  try {
    const r = await api("/api/publish", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids }),
    });
    resetSel();
    toast(r.message || ("Pushed " + r.published + " work(s)."));
    if (rerender) rerender();
  } catch (e) {
    toast(e.message);
    if (btn) { btn.disabled = false; btn.textContent = "Push to public"; }
  }
}

async function deleteSelection(rerender) {
  const n = SEL.ids.size;
  if (!n) return;
  if (!confirm("Delete " + n + (n === 1 ? " work" : " works") +
      "?\n\nThey'll be moved to the trash folder and removed from the gallery.")) return;
  try {
    await api("/api/works/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: Array.from(SEL.ids) }),
    });
    resetSel();
    if (rerender) rerender();
  } catch (e) { alert("Delete failed: " + e.message); }
}

async function removeSelectionFromCollection(cid, rerender) {
  const n = SEL.ids.size;
  if (!n) return;
  if (!confirm("Remove " + n + (n === 1 ? " work" : " works") + " from this collection?")) return;
  try {
    await api("/api/collection/" + encodeURIComponent(cid) + "/works/remove", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: Array.from(SEL.ids) }),
    });
    resetSel();
    if (rerender) rerender();
  } catch (e) { alert("Remove failed: " + e.message); }
}

async function addSelectionToCollection(rerender) {
  const n = SEL.ids.size;
  if (!n) return;
  const ids = Array.from(SEL.ids);
  let mine;
  try { mine = (await api("/api/collections")).collections.filter((c) => c.can_edit); }
  catch (e) { alert(e.message); return; }
  const rows = mine.map((c) =>
    '<button class="addmenu-item" data-id="' + esc(c.id) + '">' + esc(c.title) +
    ' <span class="tiny">' + c.count + "</span></button>").join("");
  const m = modal(
    "<h2>Add " + n + (n === 1 ? " work" : " works") + " to…</h2>" +
    '<div class="addmenu-list">' +
    (rows || '<p class="tiny">You have no collections yet — create one below.</p>') + "</div>" +
    '<div class="bf-actions"><button class="toolbtn" id="addmenu-new">+ New collection</button>' +
    '<button class="linkbtn" id="addmenu-cancel">cancel</button>' +
    '<span class="formmsg err" id="addmenu-msg"></span></div>');
  $("#addmenu-cancel").addEventListener("click", m.close);
  m.el.querySelectorAll(".addmenu-item").forEach((b) =>
    b.addEventListener("click", () => addIdsToCollection(b.dataset.id, ids, m, rerender)));
  $("#addmenu-new").addEventListener("click", () => {
    m.close();
    newCollectionDialog((col) => addIdsToCollection(col.id, ids, null, rerender));
  });
}

async function addIdsToCollection(cid, ids, m, rerender) {
  try {
    const r = await api("/api/collection/" + encodeURIComponent(cid) + "/works", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids }),
    });
    if (m) m.close();
    resetSel();
    toast("Added to “" + r.collection.title + "”.");
    if (rerender) rerender();
  } catch (e) {
    if (m) $("#addmenu-msg").textContent = e.message;
    else alert(e.message);
  }
}

/* ============================== home / artists ============================== */

/* Years an artist is represented by, e.g. "1872–1892" or a bare "1617". */
function artistYears(a) {
  if (!a.year_min) return "";
  return a.year_max && a.year_max !== a.year_min ? a.year_min + "–" + a.year_max : String(a.year_min);
}

function artistCardHtml(a) {
  const yr = artistYears(a);
  return (
    '<a class="artist-card" href="#/artist/' + encodeURIComponent(a.name) + '">' +
      '<span class="cover"><img src="/thumb/' + a.cover + '" loading="lazy" alt=""></span>' +
      '<span class="meta"><span class="name">' + esc(a.name) + "</span>" +
      '<span class="sub">' + a.count + (a.count === 1 ? " work" : " works") +
      (yr ? " · " + yr : "") + "</span></span></a>"
  );
}

/* The hero: one painting, full-bleed, with the page's only gold call to action.
   Falls back to nothing at all rather than an empty band when the library has
   no works to feature. */
function heroHtml(w) {
  if (!w) return "";
  const meta = [w.artist, w.date || w.year, w.medium].filter(Boolean).join(" · ");
  return (
    '<section class="hero">' +
    '<img src="' + viewSrc(w) + '" alt="">' +
    '<div class="scrim-x"></div><div class="scrim-y"></div>' +
    '<div class="hero-in">' +
    '<span class="hero-eyebrow">From the collection</span>' +
    '<a class="hero-title" href="#/artist/' + encodeURIComponent(w.artist || "") +
      '" id="hero-work">' + esc(w.title) + "</a>" +
    (meta ? '<span class="hero-meta">' + esc(meta) + "</span>" : "") +
    '<a class="hero-cta" href="#/artist/' + encodeURIComponent(w.artist || "") +
      '" id="hero-cta">View this work →</a>' +
    "</div></section>"
  );
}

const SORTS = {
  az: ["Sorted A–Z", (a, b) => a.name.localeCompare(b.name)],
  works: ["Most works", (a, b) => b.count - a.count || a.name.localeCompare(b.name)],
  early: ["Earliest first", (a, b) => (a.year_min || 9999) - (b.year_min || 9999)],
};

async function homeView() {
  setNav("home");
  try {
    const [d, feat] = await Promise.all([
      api("/api/artists"),
      api("/api/featured").catch(() => ({ work: null })),
    ]);
    if (!d.artists.length) {
      app.innerHTML = page(
        '<div class="emptybox"><div class="big">The gallery is empty.</div>' +
        (isOwner()
          ? 'Run <code>python import_samples.py</code> to bring in your starter paintings, ' +
            'copy images into the <code>library/</code> folder, or ' +
            '<a href="#/add">download an artist’s works</a>.'
          : "Ask an Owner to add some artworks.") + "</div>");
      return;
    }
    const sortOpts = Object.keys(SORTS).map((k) =>
      '<option value="' + k + '">' + esc(SORTS[k][0]) + "</option>").join("");
    const addCard = isOwner()
      ? '<a class="artist-card add-card" href="#/add">' +
        '<span class="cover"><span>+</span></span>' +
        '<span class="meta"><span class="name">Add artist</span>' +
        '<span class="sub">download new works</span></span></a>'
      : "";
    app.innerHTML = heroHtml(feat.work) + page(
      '<div class="pagehead"><div><h1>Artists</h1>' +
      '<p class="sub" id="acount"></p></div>' +
      '<div class="headact">' +
      '<label class="searchbox"><span class="mag" aria-hidden="true">⌕</span>' +
      '<input id="asearch" type="search" placeholder="Search artists…" autocomplete="off" ' +
      'aria-label="Search artists"></label>' +
      '<select class="sortctl" id="asort" aria-label="Sort artists">' + sortOpts + "</select>" +
      "</div></div>" +
      '<div class="artist-grid" id="agrid"></div>');

    // The hero's two links both point at the painting; open it in the viewer
    // rather than just landing on the artist page.
    if (feat.work) {
      ["#hero-work", "#hero-cta"].forEach((sel) => {
        const el = $(sel);
        if (el) el.addEventListener("click", (e) => { e.preventDefault(); openViewer([feat.work], 0); });
      });
    }

    const search = $("#asearch"), sort = $("#asort"), grid = $("#agrid"), count = $("#acount");
    const paint = () => {
      const q = fold(search.value.trim());
      const list = (q ? d.artists.filter((a) => fold(a.name).includes(q)) : d.artists)
        .slice().sort(SORTS[sort.value][1]);
      grid.innerHTML = list.map(artistCardHtml).join("") + (q ? "" : addCard);
      count.textContent = q
        ? list.length + (list.length === 1 ? " painter" : " painters") + " matching “" + search.value.trim() + "”"
        : d.artists.length + " painters · " + d.total_works + " works";
    };
    search.addEventListener("input", paint);
    sort.addEventListener("change", paint);
    paint();
  } catch (e) { errbox(e); }
}

/* One card in the artist page's Connections strip. Curator notes are quoted and
   set in italic serif — a human wrote them; derived links are plain sans, because
   they're the machine reporting a fact. */
function connCardHtml(c) {
  const t = LINK_TYPES[c.type] || LINK_TYPES.movement;
  const note = c.type === "curator" ? "“" + esc(c.note) + "”" : esc(c.note);
  return (
    '<a class="conncard" href="#/connections?artist=' + encodeURIComponent(c.other) +
      '" style="' + typeVars(t) + '">' +
    '<img src="/thumb/' + c.cover + '" loading="lazy" alt="">' +
    '<span class="cbody">' +
    '<span class="ctype"><span class="cdot" style="background:' + t.color + '"></span>' +
    '<span class="clabel" style="color:' + t.color + '">' + esc(t.label || c.type) + "</span></span>" +
    '<span class="cname">' + esc(c.other) + "</span>" +
    (c.note ? '<span class="cnote' + (c.type === "curator" ? " quoted" : "") + '">' + note + "</span>" : "") +
    "</span></a>"
  );
}

function connStripHtml(name, conns, total) {
  const cards = conns.map(connCardHtml).join("");
  const ghost =
    '<a class="conncard ghost" href="#/connections?artist=' + encodeURIComponent(name) + '">' +
    '<span class="star">✳</span><span class="glabel">See ' + esc(name) +
    " among all the painters<br>on the connections map</span></a>";
  // Collapsed, the heading has to carry the whole story — say how many threads are
  // folded away, not just that the section exists.
  const explain = total
    ? total + (total === 1 ? " link" : " links") + " to painters in this museum"
    : "not yet connected to anyone here";
  return (
    '<section class="connstrip"><div class="sechead">' +
    '<div class="titlegroup"><h2 class="dh">' +
    '<button class="disclosure" id="conn-toggle" aria-expanded="false">' +
    '<span class="lbl">Connections</span><span class="caret">▾</span></button></h2>' +
    '<span class="note">' + esc(explain) + "</span></div>" +
    '<a class="conn-open" href="#/connections?artist=' + encodeURIComponent(name) + '">' +
    "Open the connections map →</a></div>" +
    '<div class="conngrid" id="conngrid" hidden>' + cards + ghost + "</div></section>"
  );
}

async function artistView(name) {
  setNav("home");
  try {
    const [d, ov] = await Promise.all([
      api("/api/works?artist=" + encodeURIComponent(name)),
      api("/api/artist/" + encodeURIComponent(name) + "/overview")
        .catch(() => ({ info: null, connections: [], stats: {} })),
    ]);
    const works = d.works;
    if (!works.length) {
      app.innerHTML = page('<div class="emptybox">No works found for ' + esc(name) + ".</div>");
      return;
    }
    // The works carry the spelling the library settled on; the URL may hold an older
    // one — a bookmark from before this painter got their accents back. Show, link
    // and rename under the real name rather than whatever was typed to get here.
    name = works[0].artist || name;
    const info = ov.info || {}, stats = ov.stats || {};
    const years = works.map((w) => w.year).filter(Boolean);
    const span = years.length
      ? Math.min.apply(null, years) + (years.length > 1 ? "–" + Math.max.apply(null, years) : "")
      : "";
    const life = [info.born, info.died].filter(Boolean).join("–");
    // nationality · movement · city — the one gold line on the page.
    const eyebrow = [info.nationality, (info.movements || [])[0], info.birthplace]
      .filter(Boolean).join(" · ");
    const ownerTools = isOwner()
      ? '<div class="ownertools"><span class="otlabel">Owner tools</span>' +
        '<button class="linkbtn" id="rename-btn" title="Edit this artist’s name.">Edit artist</button>' +
        '<button class="linkbtn" id="repoint-btn" title="Merge this artist into another artist already in your library — fixes the same painter appearing under different name spellings.">Repoint</button>' +
        '<button class="linkbtn" id="bio-toggle" aria-expanded="false">Bio &amp; details<span class="caret">▾</span></button>' +
        "</div>"
      // The prose is already above; this reveals the movements and life dates, so
      // it doesn't promise a biography it isn't holding.
      : (info.movements || info.born
          ? '<button class="disclosure" id="bio-toggle" aria-expanded="false">' +
            'Details<span class="caret">▾</span></button>'
          : "");
    const addMore = (isOwner() && !isPublic())
      ? '<a class="cta-btn" href="#/add/' + encodeURIComponent(name) + '">+ Add more from this artist</a>'
      : "";
    const statRow = (k, v, lead) =>
      '<div class="statrow' + (lead ? " lead" : "") + '"><span class="k">' + k +
      '</span><span class="v">' + esc(String(v)) + "</span></div>";

    app.innerHTML = page(
      '<a class="back" href="#/">← All artists</a>' +
      '<section class="artist-head"><div>' +
      '<div class="artist-title" id="artist-title"><h1>' + esc(name) + "</h1>" +
      (life ? '<span class="artist-life">' + esc(life) + "</span>" : "") + "</div>" +
      (eyebrow ? '<p class="artist-eyebrow">' + esc(eyebrow) + "</p>" : "") +
      (info.description
        ? '<div class="artist-bio clamped" id="artist-bio">' +
          richDescHtml(info.description) + "</div>" +
          '<button class="disclosure" id="bio-more" aria-expanded="false" hidden>' +
          '<span class="lbl">Read full biography</span><span class="caret">▾</span></button>'
        : "") +
      ownerTools +
      '<div id="biobar" hidden></div>' +
      "</div>" +
      '<div class="statcard">' +
      statRow("Works", works.length, true) +
      (span ? statRow("Dates in collection", span) : "") +
      statRow("In collections", stats.collections || 0) +
      statRow("Connections", stats.connections || 0) +
      addMore + "</div></section>" +
      connStripHtml(name, ov.connections || [], stats.connections || 0) +
      '<section class="works-sec">' +
      worksSection(works, false, browseCtx({ artist: name }),
        "<h2>Works <span class=\"note\">" + works.length +
        (span ? " · " + span : "") + "</span></h2>") +
      "</section>", "tight");

    renderBio(name, ov.info);
    wireDisclosure("bio-toggle", "biobar");
    wireDisclosure("conn-toggle", "conngrid");
    wireBioClamp();
    if (isOwner()) { wireRename(name); wireRepoint(name); }
    bindWorks(works, false, () => artistView(name), browseCtx({ artist: name }));
    const g = document.getElementById("grid");
    if (g) g.classList.add("show-dims");   // dimension pills only on the artist page
  } catch (e) { errbox(e); }
}

/* The bio opens clamped to a few lines — a researched one runs long enough to
   push the paintings off the screen, which is the wrong way round for a museum.
   The toggle only appears when there's actually more to read, so a one-line bio
   doesn't sprout a control that does nothing. */
function wireBioClamp() {
  const bio = $("#artist-bio"), more = $("#bio-more");
  if (!bio || !more) return;
  if (bio.scrollHeight <= bio.clientHeight + 2) return;   // it all fits; no toggle
  more.hidden = false;
  more.addEventListener("click", () => {
    const open = !bio.classList.toggle("clamped");
    more.classList.toggle("open", open);
    more.setAttribute("aria-expanded", String(open));
    more.querySelector(".lbl").textContent = open ? "Show less" : "Read full biography";
  });
}

/* ---------- disclosures (collapsible bio) ---------- */

/* Toggle a hidden panel from a caret button; rotates the caret when open. */
function wireDisclosure(toggleId, panelId) {
  const btn = document.getElementById(toggleId);
  const panel = document.getElementById(panelId);
  if (!btn || !panel) return;
  btn.addEventListener("click", () => {
    const willOpen = panel.hidden;
    panel.hidden = !willOpen;
    btn.classList.toggle("open", willOpen);
    btn.setAttribute("aria-expanded", String(willOpen));
  });
}

/* ---------- artist bio (movements, dates, birthplace) ---------- */

function renderBio(name, info) {
  const box = $("#biobar");
  if (!box) return;
  if (!info) {
    box.innerHTML = isOwner()
      ? '<div class="bio empty">' +
        '<button id="bio-edit" class="toolbtn">＋ Add artist details</button>' +
        '<span class="tiny">Life dates, movements and a biography — look them up or ' +
        "type them yourself.</span></div>"
      : "";
    if (isOwner()) wireBio(name, info);
    return;
  }
  const life = [info.born, info.died].filter(Boolean).join(" – ");
  const place = [info.birthplace, info.nationality].filter(Boolean)
    .filter((v, i, a) => a.indexOf(v) === i).join(", ");
  const facts = [];
  if (life) facts.push('<span class="fact"><b>Life</b>&nbsp; ' + esc(life) + "</span>");
  if (place) facts.push('<span class="fact"><b>From</b>&nbsp; ' + esc(place) + "</span>");
  const movements = (info.movements || []).map((m) => '<span class="mv">' + esc(m) + "</span>").join("");
  const ctl = isOwner()
    ? '<div class="bioctl"><button id="bio-edit" class="linkbtn">Edit &amp; look up</button>' +
      '<span id="bio-msg" class="bio-msg"></span></div>'
    : "";
  box.innerHTML =
    '<div class="bio">' +
    (movements ? '<div class="movements">' + movements + "</div>" : "") +
    (facts.length ? '<div class="facts">' + facts.join("") + "</div>" : "") +
    ctl + "</div>";
  if (isOwner()) wireBio(name, info);
}

function wireBio(name, info) {
  const ed = $("#bio-edit");
  if (ed) ed.addEventListener("click", () => renderBioForm(name, info || {}));
}

/* The bio form is the ONLY way a bio gets written, and looking one up lives
   inside it: research fills the fields, you read them, Save persists. Nothing
   reaches disk until you say so — a lookup used to overwrite the record on the
   spot, which quietly discarded hand edits and could redraw the connections map
   (which derives movement and place links from these very fields). */
function renderBioForm(name, info) {
  const box = $("#biobar");
  const f = (id, label, val, extra) =>
    "<label>" + label + (extra || "") + '<input id="bf-' + id + '" value="' + esc(val || "") + '"></label>';
  box.innerHTML =
    '<form class="bioform" id="bioform">' +
    '<div class="ew-autobar">' +
    '<button type="button" class="toolbtn" id="bf-auto">Look up with AI</button>' +
    '<button type="button" class="linkbtn" id="bf-wd">Wikidata only</button>' +
    '<span class="tiny ew-autohint">Researches this painter and fills the fields below — ' +
    "review before saving.</span>" +
    '<span class="formmsg" id="bf-auto-msg"></span></div>' +
    '<label class="ew-hintrow">Which painter? ' +
    '<span class="tiny">optional · sent with the AI lookup only — use it when the name ' +
    "is ambiguous</span>" +
    '<textarea id="bf-hint" rows="2" placeholder="e.g. the younger Brueghel, who copied ' +
    'his father\'s compositions"></textarea></label>' +
    '<div id="bf-trace-box"></div>' +
    '<div class="bf-row">' + f("born", "Born", info.born, ' <span class="tiny">year</span>') +
      f("died", "Died", info.died, ' <span class="tiny">year</span>') + "</div>" +
    '<div class="bf-row">' + f("birthplace", "Birthplace", info.birthplace) +
      f("nationality", "Nationality", info.nationality) + "</div>" +
    f("mv", "Movements", (info.movements || []).join(", "),
      ' <span class="tiny">comma-separated · these cluster the connections map</span>') +
    "<label>Biography</label>" + fmtBarHtml() +
    '<div class="richtext bio-rich" id="bf-desc" contenteditable="true"></div>' +
    '<div class="bf-actions"><button type="submit" class="cta-btn">Save</button>' +
    '<button type="button" id="bf-cancel" class="linkbtn">cancel</button>' +
    '<span id="bio-msg" class="bio-msg"></span></div></form>';
  $("#bf-cancel").addEventListener("click", () => reloadBio(name));

  const bioEd = $("#bf-desc");
  bioEd.innerHTML = richDescHtml(info.description || "");   // legacy plain text keeps its breaks
  wireFmtBar(box, bioEd, (msg) => { $("#bio-msg").textContent = msg; });

  // The Wikidata pointers ride along invisibly; a lookup can improve them.
  const refs = { wikidata_id: info.wikidata_id, wikipedia_url: info.wikipedia_url };

  const flash = (el) => {
    el.classList.add("justfilled");
    setTimeout(() => el.classList.remove("justfilled"), 900);
  };
  /* Fill the form from a lookup and report what actually moved. Only fields the
     lookup has an answer for are touched — a blank shouldn't wipe what's there —
     and the message names the fields we set rather than every key in the payload
     (Wikidata's carries `source` and `matched_label` too). */
  const applyFields = (fx) => {
    const done = [];
    const fill = (id, v, label) => {
      if (!v) return;
      const el = $(id);
      el.value = v;
      flash(el);
      done.push(label);
    };
    fill("#bf-born", fx.born, "born");
    fill("#bf-died", fx.died, "died");
    fill("#bf-birthplace", fx.birthplace, "birthplace");
    fill("#bf-nationality", fx.nationality, "nationality");
    fill("#bf-mv", (fx.movements || []).join(", "), "movements");
    // The biography is a rich editor, not an input — it takes markup, not a value.
    if (fx.description) {
      bioEd.innerHTML = aiTextToRich(fx.description);
      flash(bioEd);
      done.push("biography");
    }
    return done.length ? "Filled " + done.join(", ") + ". Review, then Save." : "";
  };

  $("#bf-auto").addEventListener("click", async () => {
    const btn = $("#bf-auto"), msg = $("#bf-auto-msg"), label = btn.textContent;
    btn.disabled = true; btn.textContent = "Researching…";
    msg.className = "formmsg"; msg.textContent = "";
    try {
      const hint = $("#bf-hint").value.trim();
      const r = await api("/api/artist_info/ai_lookup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(hint ? { name: name, hint: hint } : { name: name }),
      });
      $("#bf-trace-box").innerHTML = traceHtml(r.trace);
      const said = applyFields(r.fields || {});
      msg.className = said ? "formmsg ok" : "formmsg";
      msg.textContent = said || "The AI found nothing for this one.";
    } catch (e) {
      msg.className = "formmsg err"; msg.textContent = e.message;
      $("#bf-trace-box").innerHTML = traceHtml(e.body && e.body.trace);
    } finally { btn.disabled = false; btn.textContent = label; }
  });

  // The free, structured fallback — no API credits, no invention. Worth keeping
  // for when the AI is down or the answer looks wrong.
  $("#bf-wd").addEventListener("click", async () => {
    const btn = $("#bf-wd"), msg = $("#bf-auto-msg"), label = btn.textContent;
    btn.disabled = true; btn.textContent = "Searching Wikidata…";
    msg.className = "formmsg"; msg.textContent = "";
    try {
      const r = await api("/api/artist_info/lookup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name }),
      });
      if (!r.info) { msg.textContent = r.message || "No match on Wikidata."; return; }
      if (r.info.wikidata_id) refs.wikidata_id = r.info.wikidata_id;
      if (r.info.wikipedia_url) refs.wikipedia_url = r.info.wikipedia_url;
      const said = applyFields(r.info);
      msg.className = "formmsg ok";
      msg.textContent = (said || "Nothing new from Wikidata.") +
        (r.matched_label && r.matched_label !== name ? " (matched “" + r.matched_label + "”)" : "");
    } catch (e) {
      msg.className = "formmsg err"; msg.textContent = e.message;
    } finally { btn.disabled = false; btn.textContent = label; }
  });
  $("#bioform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      name: name,
      born: $("#bf-born").value, died: $("#bf-died").value,
      birthplace: $("#bf-birthplace").value, nationality: $("#bf-nationality").value,
      movements: $("#bf-mv").value,
      // Same allowlist the placard uses; an empty editor saves as "" rather than
      // whatever <br> the browser left lying in it.
      description: bioEd.textContent.trim() ? sanitizeRich(bioEd.innerHTML) : "",
      // Not shown in the form, but a Wikidata lookup may have found better ones
      // than the record started with.
      wikidata_id: refs.wikidata_id, wikipedia_url: refs.wikipedia_url,
    };
    try {
      await api("/api/artist_info/save", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      // Re-render the page, not just this panel: the header prose, the gold
      // nationality line and the connections count all read from what just changed.
      artistView(name);
      toast("Bio saved.");
    } catch (err) { $("#bio-msg").textContent = err.message; }
  });
}

async function reloadBio(name) {
  try {
    const r = await api("/api/artist_info?name=" + encodeURIComponent(name));
    renderBio(name, r.info);
  } catch (e) { renderBio(name, null); }
}

function wireRename(name) {
  const btn = $("#rename-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const box = $("#artist-title");
    box.classList.add("editing");
    box.innerHTML =
      '<input id="ren-in" autocomplete="off">' +
      '<button class="toolbtn" id="ren-save">Save</button>' +
      '<button class="linkbtn" id="ren-cancel">cancel</button>' +
      '<span id="ren-msg" class="bio-msg"></span>';
    const inp = $("#ren-in");
    inp.value = name;
    inp.focus();
    inp.select();
    $("#ren-cancel").addEventListener("click", () => artistView(name));
    const submit = async () => {
      const to = inp.value.trim();
      if (!to || to === name) return artistView(name);
      $("#ren-save").disabled = true;
      $("#ren-msg").textContent = "Saving…";
      try {
        await api("/api/artist/rename", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ from: [name], to: to }),
        });
        if ("#/artist/" + encodeURIComponent(to) === location.hash) artistView(to);
        else location.hash = "#/artist/" + encodeURIComponent(to);
      } catch (e) {
        $("#ren-msg").textContent = e.message;
        $("#ren-save").disabled = false;
      }
    };
    $("#ren-save").addEventListener("click", submit);
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
      else if (e.key === "Escape") artistView(name);
    });
  });
}

/* Repoint: merge this artist into another already in the library. Picks a target
   from the existing artists, then reuses /api/artist/rename (which merges when the
   target already exists) so name variants of one painter collapse into one. */
function wireRepoint(name) {
  const btn = $("#repoint-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    let artists;
    try {
      const d = await api("/api/artists");
      const key = fold(name.trim());
      artists = (d.artists || []).filter((a) => fold(a.name.trim()) !== key);
    } catch (e) { toast(e.message); return; }
    if (!artists.length) { toast("There are no other artists to repoint to yet."); return; }

    const m = modal("");
    const inner = m.el.querySelector(".modal");

    const rowsHtml = (list) =>
      list.length
        ? list.map((a) =>
            '<button class="addmenu-item repoint-row" data-name="' + esc(a.name) + '">' +
            esc(a.name) + '<span class="tiny">' + a.count +
            (a.count === 1 ? " work" : " works") + "</span></button>").join("")
        : '<p class="rp-sub">No matches.</p>';

    function renderPicker() {
      inner.innerHTML =
        "<h2>Repoint “" + esc(name) + "”</h2>" +
        '<p class="rp-sub">Move every work by “' + esc(name) + "” under another artist already in " +
        "your library — they take on that artist’s exact name. Use it to merge the same painter " +
        "when their name is spelled differently across works.</p>" +
        '<input id="rp-search" class="rp-search" placeholder="Filter artists…" autocomplete="off">' +
        '<div id="rp-list" class="addmenu-list">' + rowsHtml(artists) + "</div>" +
        '<div class="modal-actions"><button class="linkbtn" id="rp-close">cancel</button></div>';
      const search = inner.querySelector("#rp-search");
      const listEl = inner.querySelector("#rp-list");
      search.focus();
      search.addEventListener("input", () => {
        const q = fold(search.value.trim());
        listEl.innerHTML = rowsHtml(q ? artists.filter((a) => fold(a.name).includes(q)) : artists);
      });
      inner.querySelector("#rp-close").addEventListener("click", m.close);
      listEl.addEventListener("click", (e) => {
        const row = e.target.closest(".repoint-row");
        if (row) renderConfirm(row.getAttribute("data-name"));
      });
    }

    function renderConfirm(target) {
      inner.innerHTML =
        "<h2>Repoint into “" + esc(target) + "”?</h2>" +
        '<p class="rp-sub">All works by “' + esc(name) + "” will be moved into “" + esc(target) +
        "” and renamed to match. You can reverse this later by repointing back.</p>" +
        '<div class="modal-actions">' +
        '<button class="toolbtn" id="rp-go">Repoint</button>' +
        '<button class="linkbtn" id="rp-back">back</button>' +
        '<span id="rp-msg" class="bio-msg"></span></div>';
      inner.querySelector("#rp-back").addEventListener("click", renderPicker);
      const go = inner.querySelector("#rp-go");
      go.addEventListener("click", async () => {
        go.disabled = true;
        inner.querySelector("#rp-msg").textContent = "Repointing…";
        try {
          const r = await api("/api/artist/rename", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ from: [name], to: target }),
          });
          m.close();
          const n = r.moved || 0;
          toast("Moved " + n + (n === 1 ? " work into “" : " works into “") + target + "”");
          location.hash = "#/artist/" + encodeURIComponent(target);
        } catch (e) {
          inner.querySelector("#rp-msg").textContent = e.message;
          go.disabled = false;
        }
      });
    }

    renderPicker();
  });
}

/* ============================== browse ============================== */

const FACETS = [["era", "Era"], ["medium", "Medium"], ["style", "Style"],
                ["genre", "Genre"], ["school", "School"]];

/* ============================== connections ============================== */

/* The five kinds of thread. Mirrors links.TYPE_META on the server; the colours
   are design tokens and also appear in style.css, so they're stated where each
   surface draws them rather than plumbed through every response. */
const LINK_TYPES = {
  movement:   { label: "Movement",     color: "#7f96ad", dash: "",    w: 1.4 },
  influence:  { label: "Influence",    color: "#c2a061", dash: "",    w: 1.9 },
  place_time: { label: "Place & time", color: "#7fa389", dash: "7 5", w: 1.4 },
  subject:    { label: "Subject",      color: "#a884a3", dash: "2 5", w: 1.6 },
  curator:    { label: "Curator note", color: "#bf7e63", dash: "",    w: 2.4 },
};
const LINK_ORDER = ["movement", "influence", "place_time", "subject", "curator"];

/* #rrggbb + alpha -> #rrggbbaa, so a type's colour can be handed to CSS at the
   design's exact opacities without keeping a parallel rgba() table. */
function hexA(hex, alpha) {
  return hex + Math.round(alpha * 255).toString(16).padStart(2, "0");
}
function typeVars(t) {
  return "--tc:" + t.color + ";--tcb:" + hexA(t.color, .55) + ";--tcbg:" + hexA(t.color, .05);
}

async function browseView(facet, value) {
  setNav("browse");
  if (!FACETS.some((f) => f[0] === facet)) facet = "era";
  try {
    const [facets, arts] = await Promise.all([
      api("/api/facets"),
      api("/api/artists").catch(() => ({ total_works: null })),
    ]);
    const tabs = FACETS.map((f) =>
      '<a href="#/browse/' + f[0] + '" class="' + (f[0] === facet ? "active" : "") + '">' + f[1] + "</a>"
    ).join("");
    const chips = (facets[facet] || []).map((v) =>
      '<a class="chip' + (value && v.value.toLowerCase() === value.toLowerCase() ? " active" : "") +
      '" href="#/browse/' + facet + "/" + encodeURIComponent(v.value) + '">' +
      esc(v.value) + ' <span class="n">' + v.count + "</span></a>"
    ).join("");
    const sub = arts.total_works != null
      ? arts.total_works + " works across the collection" : "";
    app.innerHTML = page(
      '<div class="pagehead"><div><h1>Browse</h1>' +
      (sub ? '<p class="sub">' + esc(sub) + "</p>" : "") + "</div></div>" +
      '<div class="facet-tabs">' + tabs + "</div>" +
      '<div class="chips">' + chips + '<span class="chip-summary" id="browse-sum"></span></div>' +
      "<div id='browse-body'></div>");
    const body = $("#browse-body");
    if (!value) {
      body.innerHTML = '<div class="emptybox">Pick a ' + esc(facet) + " above to see its works.</div>";
      return;
    }
    const d = await api("/api/works?" + facet + "=" + encodeURIComponent(value));
    const works = d.works;
    // The chip already names the filter; the summary line says how much of the
    // collection it turned out to be.
    $("#browse-sum").textContent = works.length
      ? esc(value) + " — " + works.length + (works.length === 1 ? " work" : " works")
      : "";
    body.innerHTML = works.length
      ? worksSection(works, true, browseCtx())
      : '<div class="emptybox">Nothing here yet.</div>';
    if (works.length) bindWorks(works, true, () => browseView(facet, value), browseCtx());
  } catch (e) { errbox(e); }
}

/* ============================== connections page ============================== */

/* Survives re-renders within the page; `off` is the set of type filters the
   viewer has switched off, and selection deliberately persists across a mode
   switch so you keep your painter when you jump from map to timeline. So does
   the map's zoom and pan (z, px, py) — clicking a painter re-renders the whole
   page, and it would be maddening to be thrown back to the wide shot each time. */
const CONN = { mode: "map", sel: null, off: new Set(), data: null, threads: [],
               z: 1, px: 0, py: 0 };
const CONN_MODES = ["map", "timeline", "threads"];
const SUBLINE = {
  map: "every painter, five kinds of thread",
  timeline: "four centuries, side by side",
  threads: "curated paths through the collection",
};

function connNode(id) { return (CONN.data.nodes || []).find((n) => n.id === id); }
function nodeSize(n) { return 44 + Math.min(n.works, 20) * 2; }   // 44–84px
function activeLinks() { return CONN.data.links.filter((l) => !CONN.off.has(l.type)); }

/* Keep the address bar shareable without re-routing: assigning location.hash
   would fire hashchange and reload the whole graph on every click. */
function connSyncUrl() {
  const q = new URLSearchParams();
  if (CONN.sel) q.set("artist", connNode(CONN.sel) ? connNode(CONN.sel).name : CONN.sel);
  if (CONN.mode !== "map") q.set("mode", CONN.mode);
  const s = q.toString();
  history.replaceState(null, "", "#/connections" + (s ? "?" + s : ""));
}

function connSelect(id) {
  CONN.sel = CONN.sel === id ? null : id;
  connSyncUrl();
  renderConnections();
}

async function connectionsView(artist, mode) {
  setNav("connections");
  if (CONN_MODES.includes(mode)) CONN.mode = mode;
  try {
    const [g, th] = await Promise.all([
      api("/api/connections"),
      api("/api/threads").catch(() => ({ threads: [] })),
    ]);
    CONN.data = g;
    CONN.threads = th.threads || [];
    CONN.z = 1; CONN.px = 0; CONN.py = 0;   // a fresh graph opens on the wide shot
    if (artist) {
      const hit = g.nodes.find((n) => n.name.toLowerCase() === artist.toLowerCase());
      CONN.sel = hit ? hit.id : null;
    }
    if (CONN.sel && !connNode(CONN.sel)) CONN.sel = null;   // artist since removed
    renderConnections();
  } catch (e) { errbox(e); }
}

function renderConnections() {
  const g = CONN.data;
  if (!g.nodes.length) {
    app.innerHTML = page(
      '<div class="pagehead"><div><h1>Connections</h1></div></div>' +
      '<div class="emptybox"><div class="big">No painters to connect yet.</div>' +
      "Once the gallery has a few artists, this map draws the threads between them.</div>");
    return;
  }
  const seg = CONN_MODES.map((m) =>
    '<button type="button" data-mode="' + m + '" class="' + (CONN.mode === m ? "on" : "") + '">' +
    m.charAt(0).toUpperCase() + m.slice(1) + "</button>").join("");
  const chips = LINK_ORDER.map((t) => {
    const meta = LINK_TYPES[t], on = !CONN.off.has(t);
    const count = (g.types[t] || {}).count || 0;
    return '<button type="button" class="typechip' + (on ? " on" : "") + '" data-type="' + t +
      '" style="' + (on ? "border-color:" + hexA(meta.color, .47) + ";" : "") + '"' +
      ' aria-pressed="' + on + '">' +
      '<span class="dot"' + (on ? ' style="background:' + meta.color + '"' : "") + "></span>" +
      esc(meta.label) + " <span class=\"tc-n\">" + count + "</span></button>";
  }).join("");

  const body = CONN.mode === "map" ? mapHtml()
             : CONN.mode === "timeline" ? timelineHtml()
             : threadsHtml();

  app.innerHTML =
    '<div class="conn-sub"><div class="titlegroup"><h1>Connections</h1>' +
    '<span class="conn-subline">' + esc(SUBLINE[CONN.mode]) + "</span></div>" +
    '<div class="conn-ctl"><div class="segmented">' + seg + "</div>" +
    '<div class="typechips">' + chips + "</div></div></div>" +
    '<div class="conn-body"><div class="conn-main">' + body + "</div>" +
    '<aside class="conn-aside">' + asideHtml() + "</aside></div>";

  app.querySelectorAll(".segmented button").forEach((b) =>
    b.addEventListener("click", () => {
      CONN.mode = b.dataset.mode; connSyncUrl(); renderConnections();
    }));
  app.querySelectorAll(".typechip").forEach((b) =>
    b.addEventListener("click", () => {
      const t = b.dataset.type;
      if (CONN.off.has(t)) CONN.off.delete(t); else CONN.off.add(t);
      renderConnections();
    }));
  app.querySelectorAll("[data-select]").forEach((el) =>
    el.addEventListener("click", (e) => { e.preventDefault(); connSelect(el.dataset.select); }));
  wireAside();
  wireMap();
}

/* ---------- map ---------- */

/* A quadratic bézier between two nodes, trimmed to each rim so the curve starts
   at the edge of the portrait rather than under it. The control point is pushed
   perpendicular to the chord — a straight line between every pair would collapse
   parallel links on top of each other.

   ppu is the rendered pixels per canvas unit. The trim is the one screen-space
   measure in here, because a portrait is a fixed px circle at every zoom, so it
   has to be converted into canvas units or the threads detach from the faces as
   you lean in. Where the portraits overlap outright the trim is capped, leaving
   a stub tucked under them rather than a curve drawn backwards. */
function edgePath(a, b, ppu) {
  const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
  let r1 = (nodeSize(a) / 2 + 6) / ppu, r2 = (nodeSize(b) / 2 + 6) / ppu;
  const cap = len * 0.86;
  if (r1 + r2 > cap) { const f = cap / (r1 + r2); r1 *= f; r2 *= f; }
  const x1 = a.x + dx * (r1 / len), y1 = a.y + dy * (r1 / len);
  const x2 = b.x - dx * (r2 / len), y2 = b.y - dy * (r2 / len);
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const k = Math.min(60, Math.max(18, len * 0.13));
  return "M " + x1 + " " + y1 + " Q " + (mx - dy / len * k) + " " + (my + dx / len * k) +
         " " + x2 + " " + y2;
}

/* Redrawn on every zoom step, since the rim trim moves with it — a few dozen
   paths of string, and the weights are pinned to px so a thread stays a thread. */
function edgesHtml(ppu) {
  const sel = CONN.sel;
  return activeLinks().map((l) => {
    const a = connNode(l.a_id), b = connNode(l.b_id);
    if (!a || !b) return "";
    const t = LINK_TYPES[l.type] || LINK_TYPES.movement;
    const touches = sel && (l.a_id === sel || l.b_id === sel);
    const op = sel ? (touches ? .95 : .07) : (l.type === "curator" ? .7 : .45);
    return '<path d="' + edgePath(a, b, ppu) + '" stroke="' + t.color +
      '" stroke-width="' + t.w + '" vector-effect="non-scaling-stroke"' +
      (t.dash ? ' stroke-dasharray="' + t.dash + '"' : "") +
      ' opacity="' + op + '" fill="none"></path>';
  }).join("");
}

function mapHtml() {
  const g = CONN.data, sel = CONN.sel;
  const nbr = new Set();
  if (sel) activeLinks().forEach((l) => {
    if (l.a_id === sel) nbr.add(l.b_id);
    if (l.b_id === sel) nbr.add(l.a_id);
  });

  const nodes = g.nodes.map((n) => {
    const s = nodeSize(n), isSel = sel === n.id;
    const dim = sel && !isSel && !nbr.has(n.id);
    return '<button type="button" class="map-node' + (isSel ? " sel" : "") + (dim ? " dim" : "") +
      '" data-select="' + esc(n.id) + '" style="left:' + (n.x / g.canvas.w * 100).toFixed(2) +
      "%;top:" + (n.y / g.canvas.h * 100).toFixed(2) + '%">' +
      '<img src="/thumb/' + n.cover + '" loading="lazy" alt="" style="width:' + s +
      "px;height:" + s + 'px">' +
      '<span class="nlabel">' + esc(n.name) + "</span></button>";
  }).join("");

  const clusters = (g.clusters || []).map((c) =>
    '<span class="cluster-label" style="left:' + (c.x / g.canvas.w * 100).toFixed(2) +
    "%;top:" + (c.y / g.canvas.h * 100).toFixed(2) + '%">' + esc(c.label) + "</span>").join("");

  /* The svg is left empty: its threads can't be drawn until the window has been
     measured, so wireMap fills them in on the same tick, before the paint. */
  return (
    '<div class="map-view" id="mapview">' +
    '<div class="map-canvas" id="mapcanvas">' +
    '<svg viewBox="0 0 ' + g.canvas.w + " " + g.canvas.h +
    '" preserveAspectRatio="none" aria-hidden="true"></svg>' +
    clusters + nodes + "</div>" +
    '<div class="map-zoom">' +
    '<button type="button" id="mapin" title="Zoom in" aria-label="Zoom in">+</button>' +
    '<button type="button" id="mapout" title="Zoom out" aria-label="Zoom out">&minus;</button>' +
    "</div></div>" +
    '<p class="conn-caption">Click a painter to trace their connections · scroll or pinch ' +
    "to spread them out · node size follows works in the collection" +
    (g.truncated ? " · showing the " + g.nodes.length + " best-connected of " +
      (g.nodes.length + g.truncated) + " painters" : "") + "</p>"
  );
}

/* ---------- map zoom ---------- */

/* Zoom spreads the map rather than magnifying it: the canvas box grows, the
   percentage node positions spread apart with it, and everything measured in
   pixels — the portraits, their labels, the thread weights — stays exactly as it
   was. So leaning in buys room between painters, which is the whole point. */
const Z_MIN = 1;

function mapView() { return $("#mapview"); }

/* Enough zoom to pull the portraits apart at any width. A flat cap can't do it:
   a phone starts the canvas at a quarter of its design size, where even 2x
   leaves the faces touching. */
function zMax(view) {
  return Math.min(8, Math.max(2.5, 2600 / (view.clientWidth || 1000)));
}

function applyZoom() {
  const view = mapView();
  if (!view) return;
  const w = view.clientWidth, h = view.clientHeight;
  CONN.z = Math.min(zMax(view), Math.max(Z_MIN, CONN.z));
  // Never pan past an edge, which at rest pins the canvas back into the corner.
  CONN.px = Math.min(0, Math.max(w * (1 - CONN.z), CONN.px));
  CONN.py = Math.min(0, Math.max(h * (1 - CONN.z), CONN.py));

  const canvas = $("#mapcanvas");
  canvas.style.setProperty("--z", CONN.z);
  canvas.style.setProperty("--px", CONN.px.toFixed(1) + "px");
  canvas.style.setProperty("--py", CONN.py.toFixed(1) + "px");
  canvas.querySelector("svg").innerHTML = edgesHtml(w * CONN.z / CONN.data.canvas.w);

  view.classList.toggle("pannable", CONN.z > Z_MIN);
  $("#mapin").disabled = CONN.z >= zMax(view) - 1e-3;
  $("#mapout").disabled = CONN.z <= Z_MIN + 1e-3;
}

/* Holds the point under the cursor (or the pinch, or the middle of the window)
   still while the map spreads around it. Returns whether anything actually
   moved, so the wheel knows whether it owes the page a scroll. */
function zoomTo(z, cx, cy) {
  const view = mapView();
  if (!view) return false;
  const z0 = CONN.z, z1 = Math.min(zMax(view), Math.max(Z_MIN, z));
  if (Math.abs(z1 - z0) < 5e-4) return false;
  const r = view.getBoundingClientRect();
  const fx = cx == null ? r.width / 2 : cx - r.left;
  const fy = cy == null ? r.height / 2 : cy - r.top;
  CONN.px = fx - (fx - CONN.px) * (z1 / z0);
  CONN.py = fy - (fy - CONN.py) * (z1 / z0);
  CONN.z = z1;
  applyZoom();
  return true;
}

function wireMap() {
  const view = mapView();
  if (!view) return;
  applyZoom();

  view.addEventListener("wheel", (e) => {
    // Only swallow the page scroll when the wheel actually moved the map, so
    // scrolling on past a map that's already zoomed out still leaves the page.
    const px = e.deltaY * (e.deltaMode === 1 ? 32 : e.deltaMode === 2 ? 320 : 1);
    if (zoomTo(CONN.z * Math.exp(-px * 0.002), e.clientX, e.clientY)) e.preventDefault();
  }, { passive: false });

  $("#mapin").addEventListener("click", () => zoomTo(CONN.z * 1.45));
  $("#mapout").addEventListener("click", () => zoomTo(CONN.z / 1.45));

  /* One finger drags, two pinch. Pointer events cover mouse, touch and pen at
     once; the moves are watched on the window so a drag that outruns the cursor
     and leaves the map doesn't just stop dead. */
  const pts = new Map();
  let drag = null, pinch = null, swallow = false;

  const span = () => {
    const [a, b] = Array.from(pts.values());
    return {
      d: Math.hypot(a.x - b.x, a.y - b.y) || 1,
      cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2,
    };
  };

  view.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    // The +/- control sits over the map but isn't part of it: a press that drifts
    // a few px there should still count as a press, not a drag that pans the map
    // and then eats its own click.
    if (e.target.closest(".map-zoom")) return;
    swallow = false;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2) { pinch = span(); drag = null; }
    else if (pts.size === 1) drag = { x: e.clientX, y: e.clientY, moved: 0 };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  });

  function onMove(e) {
    const p = pts.get(e.pointerId);
    if (!p) return;
    p.x = e.clientX; p.y = e.clientY;
    if (pinch && pts.size >= 2) {
      // The fingers' midpoint is both the zoom focus and the pan handle, which
      // is what a two-finger drag feels like everywhere else.
      const m = span();
      CONN.px += m.cx - pinch.cx;
      CONN.py += m.cy - pinch.cy;
      pinch.cx = m.cx; pinch.cy = m.cy;
      if (!zoomTo(CONN.z * (m.d / pinch.d), m.cx, m.cy)) applyZoom();
      pinch.d = m.d;
      swallow = true;
    } else if (drag && CONN.z > Z_MIN) {
      CONN.px += e.clientX - drag.x;
      CONN.py += e.clientY - drag.y;
      drag.moved += Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y);
      drag.x = e.clientX; drag.y = e.clientY;
      if (drag.moved > 6) { swallow = true; view.classList.add("panning"); }
      applyZoom();
    }
  }

  function onUp(e) {
    pts.delete(e.pointerId);
    if (pts.size < 2) pinch = null;
    if (pts.size) return;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    window.removeEventListener("pointercancel", onUp);
    view.classList.remove("panning");
    drag = null;
  }

  // A drag that happens to end on a painter shouldn't also open them.
  view.addEventListener("click", (e) => {
    if (swallow) { e.stopPropagation(); e.preventDefault(); swallow = false; }
  }, true);
}

/* Both the rim trim and the pan clamp are measured against the window, so a
   resize has to redraw them. Registered once — the map's own DOM is replaced on
   every render, and a listener per render would pile up. */
window.addEventListener("resize", () => { if (mapView()) applyZoom(); });

/* ---------- timeline ---------- */

function timelineHtml() {
  const g = CONN.data;
  const dated = g.nodes.filter((n) => n.born && n.died);
  if (!dated.length) {
    return '<div class="emptybox"><div class="big">No life dates yet.</div>' +
      "The timeline plots birth and death years — look an artist up from their page " +
      "(<b>Owner tools → Bio &amp; details</b>) and they'll appear here.</div>";
  }
  const Y0 = Math.floor(Math.min.apply(null, dated.map((n) => n.born)) / 50) * 50;
  const Y1 = Math.ceil(Math.max.apply(null, dated.map((n) => n.died)) / 50) * 50;
  const span = Math.max(1, Y1 - Y0);
  const pct = (y) => ((y - Y0) / span * 100).toFixed(2);

  let ticks = "";
  for (let y = Y0; y <= Y1; y += 50) {
    ticks += '<span class="tl-line" style="left:' + pct(y) + '%"></span>' +
             '<span class="tl-year" style="left:' + pct(y) + '%">' + y + "</span>";
  }
  // Group under movement headings, oldest movement first — the same reading order
  // the map lays out left-to-right.
  const groups = {};
  dated.forEach((n) => (groups[n.movement] = groups[n.movement] || []).push(n));
  const order = Object.keys(groups).sort((a, b) =>
    Math.min.apply(null, groups[a].map((n) => n.born)) -
    Math.min.apply(null, groups[b].map((n) => n.born)) || a.localeCompare(b));

  const rows = order.map((m) =>
    '<div class="tl-head">' + esc(m) + "</div>" +
    groups[m].slice().sort((a, b) => a.born - b.born).map((n) => {
      const life = Math.max(1, n.died - n.born);
      const tick = n.year_min
        ? '<span class="tl-tick" style="left:' + ((n.year_min - n.born) / life * 100).toFixed(2) +
          "%;width:" + Math.max(1.5, ((n.year_max - n.year_min) / life * 100)).toFixed(2) + '%"></span>'
        : "";
      return '<div class="tl-row"><button type="button" class="tl-bar' +
        (CONN.sel === n.id ? " sel" : "") + '" data-select="' + esc(n.id) +
        '" style="left:' + pct(n.born) + "%;width:" + ((life / span) * 100).toFixed(2) + '%">' +
        '<img src="/thumb/' + n.cover + '" loading="lazy" alt="">' +
        '<span class="nm">' + esc(n.name) + "</span>" +
        '<span class="yr">' + n.born + "–" + n.died + "</span>" + tick + "</button></div>";
    }).join("")).join("");

  const undated = g.nodes.length - dated.length;
  return '<div class="tl-wrap">' + ticks + "<div>" + rows + "</div>" +
    '<p class="conn-caption">Bars span lifetimes · the gold tick marks the years ' +
    "represented in this collection" +
    (undated ? " · " + undated + " painter" + (undated === 1 ? "" : "s") +
      " without life dates aren't plotted" : "") + "</p></div>";
}

/* ---------- threads ---------- */

function threadsHtml() {
  const mine = canCurate();
  if (!CONN.threads.length) {
    return '<div class="emptybox"><div class="big">No threads yet.</div>' +
      "A thread is a path someone walked through the collection — a handful of painters " +
      "in order, each with a line saying why they follow the last." +
      (mine ? '<div style="margin-top:18px"><button class="cta-btn" id="th-new">+ New thread</button></div>' : "") +
      "</div>";
  }
  const items = CONN.threads.map((t, i) => {
    const steps = t.steps.map((s, j) => {
      // Resolve the step to a real node rather than lowercasing the name into an
      // id — a step whose painter isn't on the map just isn't clickable.
      const node = CONN.data.nodes.find((n) => n.name.toLowerCase() === s.artist.toLowerCase());
      return (j ? '<span class="th-arrow">→</span>' : "") +
        '<button type="button" class="th-step"' +
        (node ? ' data-select="' + esc(node.id) + '"' : " disabled") + ">" +
        '<img src="/thumb/' + s.cover + '" loading="lazy" alt="">' +
        '<span><span class="nm">' + esc(s.artist) + "</span>" +
        (s.note ? '<span class="note">' + esc(s.note) + "</span>" : "") + "</span></button>";
    }).join("");
    return '<div class="thread">' +
      '<div class="th-eyebrow">Thread ' + String(i + 1).padStart(2, "0") + "</div>" +
      '<div class="th-title">' + esc(t.title) + "</div>" +
      (t.description ? '<div class="th-desc">' + esc(t.description) + "</div>" : "") +
      '<div class="th-chain">' + steps + "</div>" +
      (t.can_edit
        ? '<div class="th-act"><button class="linkbtn" data-edit-thread="' + esc(t.id) +
          '">edit</button><button class="linkbtn" data-del-thread="' + esc(t.id) +
          '">delete</button></div>'
        : "") + "</div>";
  }).join("");
  return '<div class="threads-wrap">' + items +
    '<p class="conn-caption">Threads are curator-written paths through the collection — ' +
    "click any step for details</p>" +
    (mine ? '<div style="margin-top:18px"><button class="cta-btn" id="th-new">+ New thread</button></div>' : "") +
    "</div>";
}

/* ---------- side panel ---------- */

function asideHtml() {
  const g = CONN.data;
  if (!CONN.sel || !connNode(CONN.sel)) {
    const rows = LINK_ORDER.map((t) => {
      const meta = LINK_TYPES[t], info = g.types[t] || {};
      const style = t === "place_time" ? "dashed" : t === "subject" ? "dotted" : "solid";
      return '<span class="legend-row"><span class="lr">' +
        '<span class="swatch" style="border-top:' + (t === "curator" ? 3 : 2) + "px " + style +
        " " + meta.color + '"></span>' +
        '<span class="lname">' + esc(meta.label) + "</span>" +
        '<span class="lcount">' + (info.count || 0) +
        ((info.count || 0) === 1 ? " link" : " links") + "</span></span>" +
        '<span class="ldesc">' + esc(info.desc || "") + "</span></span>";
    }).join("");
    const first = g.nodes.slice().sort((a, b) => b.works - a.works)[0];
    return '<p class="aside-label">Reading the map</p>' + rows +
      '<p class="aside-explain">Click any painter to see who they knew, followed, or ' +
      "answered. Owners and curators can add their own links with a note — the " +
      "terracotta threads.</p>" +
      (first ? '<button type="button" class="cta-btn" data-select="' + esc(first.id) +
        '">Try it — start with ' + esc(first.name.split(" ").pop()) + "</button>" : "");
  }

  const n = connNode(CONN.sel);
  const conns = activeLinks().filter((l) => l.a_id === CONN.sel || l.b_id === CONN.sel);
  const rows = conns.map((l) => {
    const other = connNode(l.a_id === CONN.sel ? l.b_id : l.a_id);
    const t = LINK_TYPES[l.type] || LINK_TYPES.movement;
    if (!other) return "";
    const mineToEdit = l.id && canCurate();
    return '<button type="button" class="aside-conn" data-select="' + esc(other.id) + '">' +
      '<span class="ct"><span class="cdot" style="background:' + t.color + '"></span>' +
      '<span class="clabel" style="color:' + t.color + '">' + esc(t.label) + "</span>" +
      (mineToEdit ? '<span class="cedit" data-del-link="' + esc(l.id) + '" role="button">remove</span>' : "") +
      "</span>" +
      '<span class="cname">' + esc(other.name) + "</span>" +
      (l.note ? '<span class="cnote">' + esc(l.note) + "</span>" : "") + "</button>";
  }).join("");
  const dates = [n.born && n.died ? n.born + "–" + n.died : "",
                 n.works + (n.works === 1 ? " work" : " works") + " in the collection"]
                .filter(Boolean).join(" · ");
  return (
    '<div class="aside-head"><img src="/thumb/' + n.cover + '" alt="">' +
    '<button type="button" class="aside-x" id="aside-x" aria-label="Deselect">✕</button></div>' +
    "<h2>" + esc(n.name) + "</h2>" +
    '<p class="aside-dates">' + esc(dates) + "</p>" +
    (n.movement ? '<p class="aside-mov">' + esc(n.movement) + "</p>" : "") +
    '<div class="aside-rule"></div>' +
    '<p class="aside-label">' + conns.length + " connection" + (conns.length === 1 ? "" : "s") + "</p>" +
    (rows || '<p class="tiny">No connections of the kinds you\'re showing.</p>') +
    '<a class="cta-btn" href="#/artist/' + encodeURIComponent(n.name) + '">View artist page →</a>' +
    (canCurate() ? '<button type="button" class="ghost-btn" id="aside-add">+ Add a curator link</button>' : "")
  );
}

function wireAside() {
  const x = $("#aside-x");
  if (x) x.addEventListener("click", () => { CONN.sel = null; connSyncUrl(); renderConnections(); });
  const add = $("#aside-add");
  if (add) add.addEventListener("click", () => addLinkDialog(connNode(CONN.sel)));
  const nt = $("#th-new");
  if (nt) nt.addEventListener("click", () => threadDialog(null));
  app.querySelectorAll("[data-edit-thread]").forEach((b) =>
    b.addEventListener("click", () =>
      threadDialog(CONN.threads.find((t) => t.id === b.dataset.editThread))));
  app.querySelectorAll("[data-del-thread]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Delete this thread? The paintings stay where they are.")) return;
      try {
        await api("/api/threads/" + encodeURIComponent(b.dataset.delThread), { method: "DELETE" });
        CONN.threads = (await api("/api/threads")).threads;
        renderConnections();
      } catch (e) { toast(e.message); }
    }));
  // The remove affordance sits inside a button that selects the other artist, so
  // it has to claim the click before the selection handler sees it.
  app.querySelectorAll("[data-del-link]").forEach((el) =>
    el.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!confirm("Remove this link?")) return;
      try {
        await api("/api/links/" + encodeURIComponent(el.dataset.delLink), { method: "DELETE" });
        CONN.data = await api("/api/connections");
        renderConnections();
        toast("Link removed.");
      } catch (err) { toast(err.message); }
    }));
}

/* ---------- authoring ---------- */

function addLinkDialog(from) {
  if (!from) return;
  const others = CONN.data.nodes.filter((n) => n.id !== from.id);
  const opts = others.map((n) =>
    '<option value="' + esc(n.id) + '">' + esc(n.name) + "</option>").join("");
  const m = modal(
    "<h2>Link " + esc(from.name) + " to…</h2>" +
    '<form class="authform" id="lkform">' +
    "<label>Painter<select id=\"lk-b\">" + opts + "</select></label>" +
    '<label>Kind<select id="lk-type">' +
    '<option value="curator">Curator note — your own sentence</option>' +
    '<option value="influence">Influence — one shaped the other</option></select></label>' +
    '<label class="lk-dir" hidden><span class="optrow">' +
    '<input type="checkbox" id="lk-dir"><span>' + esc(from.name) +
    " influenced them (rather than the other way round)</span></span></label>" +
    "<label>Note<textarea id=\"lk-note\" rows=\"3\" placeholder=\"" +
    "e.g. Degas admired Menzel and painted a copy of The Dinner at the Ball from memory, 1879." +
    "\"></textarea></label>" +
    '<div class="bf-actions"><button type="submit" class="cta-btn">Add link</button>' +
    '<button type="button" class="linkbtn" id="lk-cancel">cancel</button>' +
    '<span class="formmsg err" id="lk-msg"></span></div></form>');
  const q = (s) => m.el.querySelector(s);
  const type = q("#lk-type");
  type.addEventListener("change", () => {
    q(".lk-dir").hidden = type.value !== "influence";
  });
  q("#lk-cancel").addEventListener("click", m.close);
  q("#lkform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const b = others.find((n) => n.id === q("#lk-b").value);
    try {
      await api("/api/links", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          a: from.name, b: b.name, type: type.value, note: q("#lk-note").value,
          directed: q("#lk-dir").checked,
        }),
      });
      m.close();
      CONN.data = await api("/api/connections");
      renderConnections();
      toast("Linked " + from.name + " and " + b.name + ".");
    } catch (err) { q("#lk-msg").textContent = err.message; }
  });
}

/* Compose a thread: a title, a why, and an ordered chain of painters. Steps are
   added one at a time rather than through a multi-select, because the order is
   the whole argument. */
function threadDialog(existing) {
  const nodes = CONN.data.nodes;
  const steps = existing
    ? existing.steps.map((s) => ({ artist: s.artist, note: s.note }))
    : [];
  const m = modal(
    "<h2>" + (existing ? "Edit thread" : "New thread") + "</h2>" +
    '<form class="authform" id="thform">' +
    '<label>Title<input id="th-title" placeholder="The road to plein air"></label>' +
    "<label>What it argues <span class=\"tiny\">optional</span>" +
    '<textarea id="th-desc" rows="2" placeholder="How open-air river painting left ' +
    'Barbizon and ended up beside the Yarra."></textarea></label>' +
    '<label>Steps <span class="tiny">in order — at least two</span></label>' +
    '<div id="th-steps" class="th-steps"></div>' +
    '<div class="th-addrow"><select id="th-pick">' +
    nodes.map((n) => '<option value="' + esc(n.name) + '">' + esc(n.name) + "</option>").join("") +
    '</select><button type="button" class="toolbtn" id="th-add">Add step</button></div>' +
    '<div class="bf-actions"><button type="submit" class="cta-btn">Save</button>' +
    '<button type="button" class="linkbtn" id="th-cancel">cancel</button>' +
    '<span class="formmsg err" id="th-msg"></span></div></form>');
  m.el.querySelector(".modal").classList.add("modal-wide");
  const q = (s) => m.el.querySelector(s);
  if (existing) { q("#th-title").value = existing.title; q("#th-desc").value = existing.description || ""; }

  const paint = () => {
    q("#th-steps").innerHTML = steps.map((s, i) =>
      '<div class="th-srow"><span class="th-n">' + (i + 1) + "</span>" +
      '<span class="th-sa">' + esc(s.artist) + "</span>" +
      '<input class="th-sn" data-i="' + i + '" value="' + esc(s.note) +
      '" placeholder="why they follow the last one">' +
      '<button type="button" class="linkbtn" data-up="' + i + '"' + (i ? "" : " disabled") + ">↑</button>" +
      '<button type="button" class="linkbtn" data-rm="' + i + '">remove</button></div>').join("") ||
      '<p class="tiny">No steps yet — pick a painter below.</p>';
    q("#th-steps").querySelectorAll(".th-sn").forEach((inp) =>
      inp.addEventListener("input", () => { steps[+inp.dataset.i].note = inp.value; }));
    q("#th-steps").querySelectorAll("[data-rm]").forEach((b) =>
      b.addEventListener("click", () => { steps.splice(+b.dataset.rm, 1); paint(); }));
    q("#th-steps").querySelectorAll("[data-up]").forEach((b) =>
      b.addEventListener("click", () => {
        const i = +b.dataset.up;
        steps.splice(i - 1, 0, steps.splice(i, 1)[0]);
        paint();
      }));
  };
  paint();
  q("#th-add").addEventListener("click", () => {
    steps.push({ artist: q("#th-pick").value, note: "" });
    paint();
  });
  q("#th-cancel").addEventListener("click", m.close);
  q("#thform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { title: q("#th-title").value, description: q("#th-desc").value, steps: steps };
    try {
      await api(existing ? "/api/threads/" + encodeURIComponent(existing.id) : "/api/threads", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      m.close();
      CONN.threads = (await api("/api/threads")).threads;
      CONN.mode = "threads";
      renderConnections();
      toast(existing ? "Thread updated." : "Thread created.");
    } catch (err) { q("#th-msg").textContent = err.message; }
  });
}

/* ============================== collections ============================== */

/* The cover mosaic: a lead image full-height beside two stacked and dimmed, so a
   collection reads as a room rather than a single painting. Degrades to whatever
   it has — three, two, one, or the empty glyph. */
function mosaicHtml(covers) {
  if (!covers || !covers.length) return '<span class="col-nocover">◫</span>';
  const cell = (id, cls) =>
    '<span class="' + cls + '"><img src="/thumb/' + id + '" loading="lazy" alt=""></span>';
  if (covers.length === 1) return cell(covers[0], "m1 wide");
  return cell(covers[0], "m1") + cell(covers[1], "m2") +
    (covers[2] ? cell(covers[2], "m3") : '<span class="m3"></span>');
}

function collectionCard(c) {
  const role = c.owner_role
    ? '<span class="role-badge ' + esc(c.owner_role) + '">' + esc(c.owner_role) + "</span>"
    : "";
  return (
    '<a class="col-card" href="#/collection/' + encodeURIComponent(c.id) + '">' +
      '<span class="col-mosaic' + ((c.covers || []).length === 1 ? " single" : "") + '">' +
      mosaicHtml(c.covers) + "</span>" +
      '<span class="cbody"><span class="ctitle">' + esc(c.title) + "</span>" +
      '<span class="col-byline"><span class="by">' +
      c.count + (c.count === 1 ? " work" : " works") +
      (c.owner_display ? " · " + esc(c.owner_display) : "") + "</span>" + role + "</span>" +
      (c.description ? '<span class="col-note">“' + esc(c.description) + "”</span>" : "") +
      "</span></a>"
  );
}

async function collectionsView() {
  setNav("collections");
  try {
    const d = await api("/api/collections");
    const cards = d.collections.map(collectionCard).join("");
    const newCard = canCurate()
      ? '<a class="col-card col-new" id="newcol" href="#/collections">' +
        '<span class="col-mosaic"><span class="plus">+</span>' +
        '<span class="nlabel">New collection</span></span>' +
        '<span class="cbody"><span class="col-note">Pick works from Browse with Select, ' +
        "then gather them under a title and a short note.</span></span></a>"
      : "";
    const count = d.collections.length;
    app.innerHTML = page(
      '<div class="pagehead"><div><h1>Collections</h1><p class="sub">' +
      count + (count === 1 ? " collection" : " collections") +
      " · Curators can gather works into their own rooms</p></div>" +
      (isOwner()
        ? '<div class="headact"><a class="conn-open" href="#/settings">' +
          "Invite a curator from settings →</a></div>"
        : "") + "</div>" +
      (count || canCurate()
        ? '<div class="col-grid">' + cards + newCard + "</div>"
        : '<div class="emptybox"><div class="big">No collections yet.</div>' +
          "Curators gather works into themed collections that everyone can browse.</div>"));
    const nc = $("#newcol");
    if (nc) nc.addEventListener("click", (e) => {
      e.preventDefault();
      newCollectionDialog((col) => { location.hash = "#/collection/" + encodeURIComponent(col.id); });
    });
  } catch (e) { errbox(e); }
}

async function collectionView(cid) {
  setNav("collections");
  try {
    const d = await api("/api/collection/" + encodeURIComponent(cid));
    const c = d.collection;
    const works = c.works;
    const editable = c.can_edit;
    const ctl = editable
      ? '<div class="col-ctl"><button class="linkbtn" id="col-edit">edit details</button>' +
        '<button class="danger" id="col-delete">Delete collection</button></div>'
      : "";
    const head =
      '<a class="back" href="#/collections">← All collections</a>' +
      '<div class="pagehead" style="margin-top:26px"><div><h1>' + esc(c.title) + "</h1>" +
      '<p class="sub">' + works.length + (works.length === 1 ? " work" : " works") +
      (c.owner_display ? " · curated by " + esc(c.owner_display) : "") + "</p>" +
      (c.description ? '<p class="col-desc">“' + esc(c.description) + "”</p>" : "") +
      ctl + "</div></div>";
    if (!works.length) {
      app.innerHTML = page(head + '<div class="emptybox">' +
        (editable
          ? "This collection is empty. Browse the museum, hit <b>Select</b>, then " +
            "<b>Add to collection</b> to gather works here."
          : "Nothing here yet.") + "</div>", "tight");
    } else {
      app.innerHTML = page(head + worksSection(works, true, collectionCtx(c)), "tight");
      bindWorks(works, true, () => collectionView(cid), collectionCtx(c));
    }
    if (editable) {
      $("#col-edit").addEventListener("click", () =>
        editCollectionDialog(c, () => collectionView(cid)));
      $("#col-delete").addEventListener("click", async () => {
        if (!confirm("Delete the collection “" + c.title + "”?\n\n" +
            "The artworks stay in the museum; only this collection is removed.")) return;
        try {
          await api("/api/collection/" + encodeURIComponent(cid), { method: "DELETE" });
          location.hash = "#/collections";
        } catch (e) { alert(e.message); }
      });
    }
  } catch (e) { errbox(e); }
}

function newCollectionDialog(onDone) {
  const m = modal(
    "<h2>New collection</h2>" +
    '<form class="authform" id="ncform">' +
    "<label>Title<input id=\"nc-title\" autocomplete=\"off\"></label>" +
    '<label>Description <span class="tiny">optional</span>' +
    "<textarea id=\"nc-desc\" rows=\"3\"></textarea></label>" +
    '<div class="bf-actions"><button type="submit" class="cta-btn">Create</button>' +
    '<button type="button" class="linkbtn" id="nc-cancel">cancel</button>' +
    '<span class="formmsg err" id="nc-msg"></span></div></form>');
  $("#nc-cancel").addEventListener("click", m.close);
  $("#ncform").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/collections", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: $("#nc-title").value, description: $("#nc-desc").value }),
      });
      m.close();
      onDone(r.collection);
    } catch (err) { $("#nc-msg").textContent = err.message; }
  });
  setTimeout(() => $("#nc-title").focus(), 30);
}

function editCollectionDialog(c, onDone) {
  const m = modal(
    "<h2>Edit collection</h2>" +
    '<form class="authform" id="ecform">' +
    '<label>Title<input id="ec-title" value="' + esc(c.title) + '"></label>' +
    '<label>Description <span class="tiny">optional</span>' +
    '<textarea id="ec-desc" rows="3">' + esc(c.description || "") + "</textarea></label>" +
    '<div class="bf-actions"><button type="submit" class="cta-btn">Save</button>' +
    '<button type="button" class="linkbtn" id="ec-cancel">cancel</button>' +
    '<span class="formmsg err" id="ec-msg"></span></div></form>');
  $("#ec-cancel").addEventListener("click", m.close);
  $("#ecform").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/collection/" + encodeURIComponent(c.id), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: $("#ec-title").value, description: $("#ec-desc").value }),
      });
      m.close();
      onDone(r.collection);
    } catch (err) { $("#ec-msg").textContent = err.message; }
  });
  setTimeout(() => $("#ec-title").focus(), 30);
}

/* ============================== add / downloads ============================== */

let sourcesCache = null;

async function addView(prefill) {
  setNav("add");
  try {
    if (!sourcesCache) sourcesCache = (await api("/api/sources")).sources;
    const sources = sourcesCache;
    const options = sources.map((s) =>
      '<option value="' + esc(s.id) + '">' + esc(s.label) + (s.available ? "" : " (unavailable)") + "</option>"
    ).join("");
    app.innerHTML = page(
      '<div class="pagehead"><div><h1>Add an artist</h1>' +
      '<p class="sub" id="f-sub">Download every painting by an artist from a source ' +
      "into your library.</p></div></div>" +
      '<div class="addwrap">' +
      '<form class="dlform" id="dlform">' +
        "<label>Source</label><select id=\"f-source\">" + options + "</select>" +
        '<label id="f-query-label">Artist</label>' +
        '<input type="file" id="f-file" class="filepick" style="display:none">' +
        "<input id=\"f-query\" autocomplete=\"off\">" +
        '<div class="row2"><div><label>Max works <span style="text-transform:none">(optional)</span></label>' +
        '<input id="f-max" type="number" min="1" placeholder="all"></div>' +
        '<div id="f-px-wrap"><label>Max size, px <span style="text-transform:none">(optional)</span></label>' +
        '<input id="f-px" type="number" min="256" placeholder="native"></div></div>' +
        "<button id=\"f-go\">Start download</button>" +
        '<p class="hint" id="f-hint"></p><p class="warn" id="f-warn"></p>' +
        '<p class="formmsg" id="f-msg"></p>' +
      "</form>" +
      '<div><div class="sechead" style="margin-bottom:16px"><h2>Downloads</h2></div>' +
      '<div id="jobs"></div></div></div>');

    const sel = $("#f-source"), hint = $("#f-hint"), warn = $("#f-warn"), q = $("#f-query");
    const file = $("#f-file");
    function syncSource() {
      const s = sources.find((x) => x.id === sel.value);
      hint.textContent = s.hint;
      warn.textContent = s.available ? "" : s.note;
      q.placeholder = s.placeholder;
      $("#f-px-wrap").style.display = s.supports_max_px ? "" : "none";
      $("#f-px").placeholder = s.max_px_default ? "default " + s.max_px_default : "native";
      // A source that takes a file gets the picker; the text field stays for a URL
      // or a path that really is on the server.
      $("#f-query-label").textContent = s.query_label || "Artist";
      $("#f-sub").textContent = s.accepts_file
        ? "Import a list of individual works — a museum's own export of a room, say — "
          + "rather than everything by one painter."
        : "Download every painting by an artist from a source into your library.";
      file.style.display = s.accepts_file ? "" : "none";
      file.accept = s.file_accept || "";
      if (!s.accepts_file) file.value = "";
    }
    sel.addEventListener("change", syncSource);
    syncSource();
    if (prefill) q.value = prefill;

    $("#dlform").addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = $("#f-msg");
      msg.className = "formmsg";
      msg.textContent = "";
      const body = {
        source: sel.value,
        query: q.value.trim(),
        max_items: $("#f-max").value || null,
        max_px: $("#f-px").value || null,
      };
      try {
        $("#f-go").disabled = true;
        // Read the chosen file here rather than sending a path: the gallery may be
        // on another machine entirely, where that path means nothing.
        const f = file.files && file.files[0];
        if (f) {
          body.csv_text = await f.text();
          body.query = f.name;
        }
        await api("/api/downloads", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        msg.className = "formmsg ok";
        msg.textContent = "Queued. Progress appears on the right.";
        q.value = "";
        file.value = "";
        refreshJobs();
      } catch (err) {
        msg.className = "formmsg err";
        msg.textContent = err.message;
      } finally {
        $("#f-go").disabled = false;
      }
    });

    refreshJobs();  // starts polling itself only while a download is active
  } catch (e) { errbox(e); }
}

function srcLabel(id) {
  const s = (sourcesCache || []).find((x) => x.id === id);
  return s ? s.label : id;
}

async function refreshJobs() {
  const box = $("#jobs");
  if (!box) { stopPolling(); return; }
  let jobs;
  try { jobs = (await api("/api/downloads?tail=14")).jobs; } catch (e) { return; }
  if (!jobs.length) {
    box.innerHTML = '<div class="emptybox">No downloads yet.</div>';
    stopPolling();
    return;
  }
  const anyActive = jobs.some((j) => j.status === "running" || j.status === "queued");
  box.innerHTML = jobs.map((j) => {
    const counts = "matched " + j.found + " · saved " + j.saved +
      " · already had " + j.skipped + " · failed " + j.failed;
    const active = j.status === "running" || j.status === "queued";
    const artists = (j.artists || []).length
      ? '<div class="job-artists">' + (j.artists.length > 1 ? "Artists: " : "Artist: ") +
        j.artists.map((a) =>
          '<a class="alink" href="#/artist/' + encodeURIComponent(a) + '">' + esc(a) + "</a>").join(", ") +
        "</div>"
      : "";
    return (
      '<div class="job"><div class="head">' +
        '<span class="q">' + esc(j.query) + "</span>" +
        '<span class="src">' + esc(srcLabel(j.source)) + "</span>" +
        '<span class="badge ' + j.status + '">' + j.status + "</span></div>" +
        '<div class="counts">' + counts + (j.message ? " · " + esc(j.message) : "") + "</div>" +
        artists +
        "<pre>" + esc((j.log || []).join("\n")) + "</pre>" +
        (active ? '<button class="cancel" data-id="' + j.id + '">Cancel</button>' : "") +
      "</div>"
    );
  }).join("");
  // Auto-scroll logs to the newest line only while work is ongoing; once a job is
  // finished the list stops refreshing, so the user can scroll up freely.
  if (anyActive) box.querySelectorAll("pre").forEach((p) => { p.scrollTop = p.scrollHeight; });
  box.querySelectorAll(".cancel").forEach((b) => {
    b.addEventListener("click", () =>
      api("/api/downloads/" + b.dataset.id + "/cancel", { method: "POST" }).then(refreshJobs));
  });
  if (anyActive) ensurePolling(); else stopPolling();
}

/* ============================== settings ============================== */

let fieldKeys = ["title", "artist", "date", "year", "medium", "style", "image", "id"];

function fmtBytes(n) {
  if (n == null) return "—";
  n = Number(n);
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 || n >= 100 ? Math.round(n) : n.toFixed(1)) + " " + u[i];
}

/* Settings page header: title on the left, app version + library totals on the
   right, the version aligned with the "Settings" heading. */
function settingsHeadHtml(s) {
  s = s || {};
  const a = s.artists || 0, im = s.images || 0;
  const stats = [
    a + (a === 1 ? " artist" : " artists"),
    im + (im === 1 ? " image" : " images"),
    fmtBytes(s.images_bytes || 0) + " of images",
    s.disk_free != null ? fmtBytes(s.disk_free) + " free" : "",
  ].filter(Boolean);
  return '<div class="pagehead settings-head"><h1>Settings</h1>' +
    '<div class="settings-meta"><div class="app-version">v' + esc(s.version || "?") + "</div>" +
    '<div class="app-stats">' + stats.map((r) => "<span>" + esc(r) + "</span>").join("") +
    "</div></div></div>";
}

/* Settings is one long page, so it carries its own index. Only lists the sections
   this box actually has — the public server has no downloads or AI to jump to. */
function setNavHtml(sections) {
  return '<nav class="setnav">' + sections.map(([id, label]) =>
    '<a href="#set-' + id + '" data-sec="' + id + '">' + esc(label) + "</a>").join("") + "</nav>";
}

function wireSetNav() {
  const links = Array.from(document.querySelectorAll(".setnav a"));
  if (!links.length) return;
  links.forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();     // a bare #hash would be read as a route
    const t = document.getElementById("set-" + a.dataset.sec);
    if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  // Light the section whose heading last crossed the index bar.
  const secs = links.map((a) => document.getElementById("set-" + a.dataset.sec)).filter(Boolean);
  const sync = () => {
    let cur = secs[0];
    for (const s of secs) if (s.getBoundingClientRect().top <= 140) cur = s;
    links.forEach((a) => a.classList.toggle("active", cur && ("set-" + a.dataset.sec) === cur.id));
  };
  window.addEventListener("scroll", sync, { passive: true });
  sync();
}

/* Every settings section wears the same head: serif title, one line of sans
   explaining it, hairline rule. */
function setSec(id, title, note, body) {
  return '<section class="setsec" id="set-' + id + '"><div class="sechead"><h2>' + esc(title) +
    "</h2>" + (note ? '<p class="note">' + note + "</p>" : "") + "</div>" + body + "</section>";
}

async function settingsView() {
  setNav("settings");
  if (isPublic()) return settingsPublicView();
  try {
    const [srcData, usersData, builtinData, aiData, statsData, pubData, feat] = await Promise.all([
      api("/api/custom_sources"),
      api("/api/users"),
      api("/api/sources/builtin"),
      api("/api/ai/config"),
      api("/api/stats"),
      api("/api/publish/status").catch(() => null),
      api("/api/featured").catch(() => null),
    ]);
    if (srcData.field_keys) fieldKeys = srcData.field_keys;
    const presets = srcData.presets || [];
    app.innerHTML = page(
      settingsHeadHtml(statsData) +
      setNavHtml([["display", "Display"], ["people", "People"], ["public", "Public server"],
                  ["ai", "Auto-fill"], ["builtin", "Built-in sources"], ["sources", "Download sources"]]) +
      displayPanelHtml(feat) +
      usersPanelHtml() +
      publishPanelHtml(pubData) +
      aiPanelHtml(aiData) +
      builtinSourcesHtml(builtinData.sources || []) +
      setSec("sources", "Download sources",
        "Add JSON-API museum sources to scan for works. The built-in sources " +
        "(Google Arts &amp; Culture, The Met, Art Institute of Chicago, Cleveland) are always available.",
        '<div class="setwrap">' +
        '<div id="srccol"><p class="aside-label" style="margin-bottom:10px">Your custom sources</p>' +
        '<div id="srclist"></div></div>' +
        "<div>" + sourceFormHtml(presets) + "</div></div>"));
    renderUsers(usersData.users);
    wireAddUser();
    wireInvites();
    wireDisplayPanel();
    wireAiPanel(aiData);
    renderSourceList(srcData.sources || []);
    wireSourceForm(presets);
    wireBuiltinSources();
    wirePublishPanel();
    wireSetNav();
  } catch (e) { errbox(e); }
}

/* Settings on the public snapshot: no authoring/download/AI panels (those routes
   are refused there). Just the header, a Pull button, Display, and People. */
async function settingsPublicView() {
  try {
    const [usersData, statsData, pubData, feat] = await Promise.all([
      api("/api/users"),
      api("/api/stats"),
      api("/api/publish/status").catch(() => null),
      api("/api/featured").catch(() => null),
    ]);
    app.innerHTML = page(
      settingsHeadHtml(statsData) +
      setNavHtml([["pull", "Pull artwork"], ["display", "Display"], ["people", "People"]]) +
      pullPanelHtml(pubData) +
      displayPanelHtml(feat) +
      usersPanelHtml());
    renderUsers(usersData.users);
    wireAddUser();
    wireInvites();
    wireDisplayPanel();
    wirePullPanel();
    wireSetNav();
  } catch (e) { errbox(e); }
}

/* ---------- publish / pull (public snapshot) ---------- */

function repoPill(st) {
  if (!st) return '<span class="repo-pill bad">status unavailable</span>';
  if (st.is_git) return '<span class="repo-pill ok">repo connected</span>';
  if (st.exists) return '<span class="repo-pill bad">folder isn’t a git repo</span>';
  return '<span class="repo-pill bad">repo not found</span>';
}

// Private box: shows where the content repo is, lets the owner set its path, and
// exports everything added since the last export in one go.
function publishPanelHtml(st) {
  const pinned = st && st.env_pinned;
  const path = st ? st.path : "";
  const remote = st && st.remote ? st.remote : "—";
  const worksN = st && st.works != null ? st.works : "—";
  const newN = st && st.new_count != null ? st.new_count : null;
  const plaN = st && st.placard_changes != null ? st.placard_changes : null;
  const bioN = st && st.bio_changes != null ? st.bio_changes : null;
  const last = st && st.last_export;
  const lastTxt = last
    ? "Last export: " + last.at + " · " + last.count + " work(s)."
    : "No exports yet.";
  // Three separate reasons to export: a new painting, a placard you've corrected
  // since publishing it, or a rewritten bio. Any one of them stands alone.
  const n = (v, one, many) => v + " " + (v === 1 ? one : many);
  const pend = [];
  if (newN) pend.push(n(newN, "new work", "new works"));
  if (plaN) pend.push(n(plaN, "fixed placard", "fixed placards"));
  if (bioN) pend.push(n(bioN, "changed bio", "changed bios"));
  const known = newN != null && plaN != null && bioN != null;
  const nothing = known && !pend.length;
  const newTxt = !known ? ""
    : (nothing ? "Nothing pending. " : "Waiting to go: " + pend.join(", ") + ". ");
  return setSec("public", "Public server",
    "Everything your gallery has that the public site doesn't — new paintings, " +
    "placards you've corrected since publishing them, and rewritten artist bios — " +
    "goes over in one push. <b>Push to public</b> on an artist page sends just the " +
    "works you select, and is the way to send a better image of a painting that's " +
    "already up. " + repoPill(st),
    '<div class="publishpanel">' +
    '<div class="exportbox"><div class="bf-actions">' +
    '<button type="button" class="cta-btn" id="export-new"' + (nothing ? " disabled" : "") + ">" +
    "Export everything pending" + (pend.length ? " (" + pend.join(" · ") + ")" : "") +
    "</button>" +
    '<span class="formmsg" id="export-msg"></span></div>' +
    '<p class="tiny">' + esc(newTxt) + esc(lastTxt) +
    " A large first export can take a few minutes.</p></div>" +
    '<form class="dlform repoform" id="repoform">' +
    "<label>Content repo folder</label>" +
    '<input id="repo-path" value="' + esc(path) + '"' + (pinned ? " disabled" : "") +
    ' placeholder="/path/to/gallery-content">' +
    (pinned
      ? '<p class="tiny">Set by the <code>GALLERY_PUBLISH_REPO</code> environment variable.</p>'
      : '<button type="submit" class="toolbtn">Save path</button>') +
    '<p class="tiny">Remote: <code>' + esc(remote) + "</code> · " + esc(String(worksN)) +
    " work(s) published</p>" +
    '<p class="formmsg" id="repo-msg"></p></form></div>');
}

function wirePublishPanel() {
  const ex = $("#export-new");
  if (ex) ex.addEventListener("click", async () => {
    const msg = $("#export-msg"); msg.className = "formmsg";
    const orig = ex.textContent;
    ex.disabled = true; ex.textContent = "Exporting…";
    msg.textContent = "Rendering and pushing — this can take a while.";
    try {
      const r = await api("/api/publish/new", { method: "POST" });
      toast(r.message || ("Exported " + r.published + " work(s)."));
      settingsView();                       // re-render with fresh counts
    } catch (e) {
      msg.className = "formmsg err"; msg.textContent = e.message;
      ex.disabled = false; ex.textContent = orig;
    }
  });
  const form = $("#repoform");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#repo-msg"); msg.className = "formmsg";
    try {
      const st = await api("/api/publish/config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_path: $("#repo-path").value }),
      });
      msg.className = "formmsg ok";
      msg.textContent = st.is_git ? "Saved — repo connected." :
        "Saved, but that folder isn’t a git repo yet. Clone your content repo there.";
    } catch (err) { msg.className = "formmsg err"; msg.textContent = err.message; }
  });
}

// Public box: pull the latest published works + bios into the gallery.
function pullPanelHtml(st) {
  const holds = [];
  if (st && st.works != null) holds.push(st.works + (st.works === 1 ? " work" : " works"));
  if (st && st.artists != null) holds.push(st.artists + (st.artists === 1 ? " bio" : " bios"));
  return setSec("pull", "Pull new artwork",
    "Fetch the latest works and artist bios your local gallery pushed, and import " +
    "them here. " + repoPill(st) + (holds.length ? " · " + holds.join(" · ") + " in the repo" : ""),
    '<div class="pullpanel"><div class="bf-actions">' +
    '<button type="button" class="cta-btn" id="pull-btn">Pull new artwork</button>' +
    '<span class="formmsg" id="pull-msg"></span></div></div>');
}

function wirePullPanel() {
  const btn = $("#pull-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const msg = $("#pull-msg"); msg.className = "formmsg";
    btn.disabled = true; const orig = btn.textContent; btn.textContent = "Pulling…";
    try {
      const r = await api("/api/pull", { method: "POST" });
      const b = r.bios || { added: 0, updated: 0 };
      const bioN = (b.added || 0) + (b.updated || 0);
      msg.className = "formmsg ok";
      msg.textContent = "Works: added " + r.added + ", updated " + r.updated + ", " +
        r.unchanged + " unchanged. Bios: added " + (b.added || 0) +
        ", updated " + (b.updated || 0) + ".";
      toast("Pull complete: +" + r.added + " new, " + r.updated + " updated" +
            (bioN ? ", " + bioN + " bio" + (bioN === 1 ? "" : "s") : "") + ".");
    } catch (e) { msg.className = "formmsg err"; msg.textContent = e.message; }
    finally { btn.disabled = false; btn.textContent = orig; }
  });
}

/* ---------- Auto-fill (AI) configuration ---------- */

function aiKeyStateHtml(cfg) {
  if (!cfg.has_key) return "No key set yet.";
  return "A key is set" + (cfg.key_hint ? " (" + esc(cfg.key_hint) + ")" : "") +
    (cfg.key_from_env ? ", from the environment" : "") + ".";
}

function aiPanelHtml(cfg) {
  const opts = (cfg.known_models || []).map((mm) => '<option value="' + esc(mm) + '">').join("");
  return setSec("ai", "Auto-fill",
    "The placard editor's <b>Auto fill</b> button researches a painting and fills " +
    "in its details. It calls an OpenAI-compatible chat API (<code>" + esc(cfg.endpoint || "") + "</code>). " +
    "Date, medium and genre may draw on Wikipedia; the description is required to come from a " +
    "primary source.",
    '<form class="dlform aiform" id="aiform">' +
    "<label>Model</label>" +
    '<input id="ai-model" list="ai-models" autocomplete="off" placeholder="' + esc(cfg.default_model || "arya") + '">' +
    '<datalist id="ai-models">' + opts + "</datalist>" +
    "<label>API key</label>" +
    '<input id="ai-key" type="password" autocomplete="off" placeholder="' +
    (cfg.has_key ? "leave blank to keep current" : "paste your API key") + '">' +
    '<p class="tiny aikeystate">' + aiKeyStateHtml(cfg) + "</p>" +
    '<button type="submit" class="cta-btn">Save</button>' +
    '<p class="formmsg" id="ai-msg"></p></form>');
}

function wireAiPanel(cfg) {
  const model = $("#ai-model");
  if (model) model.value = cfg.model || "";
  const form = $("#aiform");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#ai-msg"); msg.className = "formmsg";
    const body = { model: $("#ai-model").value };
    const key = $("#ai-key").value.trim();
    if (key) body.api_key = key;
    try {
      const r = await api("/api/ai/config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      $("#ai-key").value = "";
      $("#ai-key").placeholder = r.has_key ? "leave blank to keep current" : "paste your API key";
      const st = $(".aikeystate"); if (st) st.innerHTML = aiKeyStateHtml(r);
      msg.className = "formmsg ok"; msg.textContent = "Saved.";
    } catch (err) { msg.className = "formmsg err"; msg.textContent = err.message; }
  });
}

/* ---------- built-in source configuration (owner oversight) ---------- */

function builtinSourcesHtml(configs) {
  const cards = configs.map((c) => {
    const eps = c.endpoints.map((e) =>
      '<div class="bsrc-ep"><span>' + esc(e.label) + "</span> <code>" + esc(e.url) + "</code></div>").join("");
    const fields = c.params.map((p) => {
      if (p.type === "bool") {
        return '<div class="bsrc-field bsrc-bool"><label class="optrow">' +
          '<input type="checkbox" data-key="' + esc(p.key) + '"' + (p.value ? " checked" : "") + ">" +
          "<span>" + esc(p.label) + "</span></label>" +
          (p.help ? '<p class="bsrc-help">' + esc(p.help) + "</p>" : "") + "</div>";
      }
      let ctrl;
      if (p.type === "int") {
        ctrl = '<input type="number" data-key="' + esc(p.key) + '" value="' + esc(String(p.value)) + '"' +
          (p.min != null ? ' min="' + p.min + '"' : "") + (p.max != null ? ' max="' + p.max + '"' : "") + ">";
      } else if (p.type === "select") {
        ctrl = '<select data-key="' + esc(p.key) + '">' + (p.options || []).map((o) =>
          "<option" + (o === p.value ? " selected" : "") + ">" + esc(o) + "</option>").join("") + "</select>";
      } else {
        ctrl = '<input type="text" data-key="' + esc(p.key) + '" value="' + esc(String(p.value)) + '">';
      }
      return '<div class="bsrc-field"><label>' + esc(p.label) + "</label>" + ctrl +
        (p.help ? '<p class="bsrc-help">' + esc(p.help) + "</p>" : "") + "</div>";
    }).join("");
    return '<div class="bsrc" data-id="' + esc(c.id) + '">' +
      "<h3>" + esc(c.label) + "</h3>" +
      (eps ? '<div class="bsrc-eps">' + eps + "</div>" : "") +
      '<div class="bsrc-fields">' + fields + "</div>" +
      '<div class="bf-actions"><button type="button" class="toolbtn" data-save="' + esc(c.id) + '">Save</button>' +
      '<button type="button" class="linkbtn" data-reset="' + esc(c.id) + '">Reset to defaults</button>' +
      '<span class="formmsg" data-msg="' + esc(c.id) + '"></span></div></div>';
  }).join("");
  return setSec("builtin", "Built-in sources",
    "How each bundled museum source searches and filters. Endpoints are fixed; the " +
    "knobs below are yours to tune and are saved as overrides.",
    '<div class="bsrc-grid">' + cards + "</div>");
}

function applyBuiltinValues(card, cfg) {
  const byKey = {};
  cfg.params.forEach((p) => { byKey[p.key] = p; });
  card.querySelectorAll("[data-key]").forEach((el) => {
    const p = byKey[el.dataset.key];
    if (!p) return;
    if (el.type === "checkbox") el.checked = !!p.value;
    else el.value = String(p.value);
  });
}

function wireBuiltinSources() {
  document.querySelectorAll(".bsrc [data-save]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".bsrc"), id = btn.getAttribute("data-save");
      const values = {};
      card.querySelectorAll("[data-key]").forEach((el) => {
        values[el.dataset.key] = el.type === "checkbox" ? el.checked : el.value;
      });
      const msg = card.querySelector("[data-msg]");
      msg.className = "formmsg";
      try {
        const r = await api("/api/sources/builtin/" + encodeURIComponent(id), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values: values }),
        });
        applyBuiltinValues(card, r.source);
        msg.className = "formmsg ok"; msg.textContent = "Saved.";
      } catch (e) { msg.className = "formmsg err"; msg.textContent = e.message; }
    });
  });
  document.querySelectorAll(".bsrc [data-reset]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".bsrc"), id = btn.getAttribute("data-reset");
      const msg = card.querySelector("[data-msg]");
      msg.className = "formmsg";
      try {
        const r = await api("/api/sources/builtin/" + encodeURIComponent(id) + "/reset", { method: "POST" });
        applyBuiltinValues(card, r.source);
        msg.className = "formmsg ok"; msg.textContent = "Reset to defaults.";
      } catch (e) { msg.className = "formmsg err"; msg.textContent = e.message; }
    });
  });
}

/* ---------- display options ---------- */

/* The hero row: says which painting is on the front page and why, and offers the
   only way back to the rotation once one is pinned. */
function heroRowHtml(feat) {
  if (!feat || !feat.work) {
    return '<div class="siterow"><label>Home hero</label>' +
      '<span class="tiny">Nothing to show yet — add some artwork.</span></div>';
  }
  const w = feat.work;
  const who = [w.title, w.artist].filter(Boolean).join(" · ");
  const state = feat.pinned
    ? "Pinned: <b>" + esc(who) + "</b>"
    : "Rotating daily — today it’s <b>" + esc(who) + "</b>";
  return '<div class="siterow"><label>Home hero</label>' +
    '<span class="tiny herostate">' + state + "</span>" +
    (feat.pinned
      ? '<button type="button" class="linkbtn" id="hero-unpin">Unpin</button>'
      : "") +
    '<span class="formmsg" id="hero-msg"></span></div>' +
    '<p class="optnote">Pin any painting from a grid: hit <b>Select</b>, choose one, ' +
    "then <b>Pin to hero</b>. Unpinned, the hero moves through the works that have a " +
    "description, one a day. Pinned per server, like the title.</p>";
}

function displayPanelHtml(feat) {
  return setSec("display", "Display",
    "How this gallery names and presents itself.",
    '<div class="displaypanel">' +
    heroRowHtml(feat) +
    '<div class="wmwrap"><div class="wmfields">' +
    '<div class="siterow"><label for="opt-eyebrow">Top line</label>' +
    '<input id="opt-eyebrow" type="text" maxlength="40" value="' + esc(siteEyebrow()) +
    '" placeholder="e.g. your name — optional"></div>' +
    '<div class="siterow"><label for="opt-title">Second line</label>' +
    '<input id="opt-title" type="text" maxlength="80" value="' + esc(siteTitle()) + '">' +
    "</div>" +
    '<div class="siterow"><label for="opt-short">Small title</label>' +
    '<input id="opt-short" type="text" maxlength="40" value="' + esc(siteShort()) +
    '" placeholder="e.g. MWA — optional, phones only"></div>' +
    '<div class="siterow"><span class="wmspacer"></span>' +
    '<button type="button" class="cta-btn" id="opt-title-save">Save</button>' +
    '<span class="formmsg" id="opt-title-msg"></span></div></div>' +
    // Live samples of the real header. Two fields with prose underneath didn't say
    // "these are two separate lines" loudly enough — this does, and the phone
    // sample does the same job for the small title.
    '<div class="wmpreview"><span class="wmplabel">Your header</span>' +
    '<span class="brand"><span class="brand-eyebrow" id="wm-eb"></span>' +
    '<span class="brand-name" id="wm-nm"></span></span>' +
    '<span class="wmplabel">On a phone</span>' +
    '<span class="brand"><span class="brand-eyebrow" id="wm-eb2"></span>' +
    '<span class="brand-name" id="wm-sh"></span></span></div></div>' +
    '<p class="optnote">The two-tier wordmark in the top-left. Put one line in each box — ' +
    "the top line is optional, and leaving it blank lets the second stand alone. The small " +
    "title replaces the second line on a phone, where a long name eats the whole width; " +
    "leave it blank to use the full one. The browser tab always shows the full title. " +
    "Set per server, so your public site can carry a different name from your local one.</p>" +
    '<label class="optrow" style="margin-top:24px"><input type="checkbox" id="opt-placards">' +
    "<span>Show placards in the viewer</span></label>" +
    '<p class="optnote">A museum-style label — piece name, artist, date and description — ' +
    "shown over each painting in fullscreen. Toggle any time with the <kbd>p</kbd> key while " +
    "viewing a work.</p></div>");
}

function wireDisplayPanel() {
  const pc = document.getElementById("opt-placards");
  if (pc) { pc.checked = placardsOn(); pc.addEventListener("change", () => setPlacards(pc.checked)); }

  const unpin = document.getElementById("hero-unpin");
  if (unpin) unpin.addEventListener("click", async () => {
    const msg = document.getElementById("hero-msg"); msg.className = "formmsg";
    unpin.disabled = true;
    try {
      await api("/api/featured", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ work_id: null }),
      });
      settingsView();                    // re-render: today's rotation is now the answer
      toast("Unpinned — the hero rotates daily again.");
    } catch (e) {
      msg.className = "formmsg err"; msg.textContent = e.message;
      unpin.disabled = false;
    }
  });

  /* Show the wordmark as it will actually render, live, while you type — same
     markup and classes as the real header, so what you see is what you get. */
  const eb = document.getElementById("opt-eyebrow"), ti = document.getElementById("opt-title");
  const sh = document.getElementById("opt-short");
  const pvEb = document.getElementById("wm-eb"), pvNm = document.getElementById("wm-nm");
  const pvEb2 = document.getElementById("wm-eb2"), pvSh = document.getElementById("wm-sh");
  if (eb && ti && sh && pvEb && pvNm && pvEb2 && pvSh) {
    const sync = () => {
      const v = eb.value.trim(), full = ti.value.trim() || "The Gallery";
      [pvEb, pvEb2].forEach((e) => { e.textContent = v; e.hidden = !v; });
      pvNm.textContent = full;
      pvSh.textContent = sh.value.trim() || full;   // same fallback as the header
    };
    [eb, ti, sh].forEach((el) => el.addEventListener("input", sync));
    sync();
  }

  const save = document.getElementById("opt-title-save");
  if (save) save.addEventListener("click", async () => {
    const inp = document.getElementById("opt-title");
    const ebi = document.getElementById("opt-eyebrow");
    const shi = document.getElementById("opt-short");
    const msg = document.getElementById("opt-title-msg"); msg.className = "formmsg";
    try {
      const r = await api("/api/site", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: inp.value, eyebrow: ebi.value, short: shi.value }),
      });
      SESSION.site_title = r.site_title;
      SESSION.site_eyebrow = r.site_eyebrow;
      SESSION.site_short = r.site_short;
      inp.value = r.site_title;
      ebi.value = r.site_eyebrow;
      shi.value = r.site_short;
      applyTitle();
      renderFoot();
      msg.className = "formmsg ok"; msg.textContent = "Saved.";
    } catch (e) { msg.className = "formmsg err"; msg.textContent = e.message; }
  });
}

/* ---------- users ---------- */

function usersPanelHtml() {
  return setSec("people", "People",
    "Owners run the museum · Curators build collections · Visitors can only browse.",
    '<div class="usersgrid"><div id="userlist"></div>' +
    '<form class="dlform userform" id="adduser">' +
    '<p class="aside-label" style="margin-bottom:14px">Add a person</p>' +
    "<label>Username</label><input id=\"nu-user\" autocomplete=\"off\">" +
    "<label>Password</label><input id=\"nu-pass\" type=\"password\" autocomplete=\"new-password\">" +
    "<label>Role</label><select id=\"nu-role\">" +
    '<option value="visitor">Visitor</option><option value="curator">Curator</option>' +
    '<option value="owner">Owner</option></select>' +
    '<button type="submit" class="cta-btn">Add user</button>' +
    '<p class="formmsg" id="nu-msg"></p></form></div>' +
    inviteBoxHtml());
}

/* Invite a Curator by emailing them a one-time link (no self-registration).
   Works on both the private and public boxes. */
function inviteBoxHtml() {
  return (
    '<div class="invitebox">' +
    '<p class="aside-label" style="margin-bottom:6px">Invite a curator</p>' +
    '<p class="optnote" style="margin-bottom:14px">They set their own username and password ' +
    "from the link. It's single-use and expires in 14 days.</p>" +
    '<form class="dlform inviteform" id="invcreate">' +
    "<label>Email</label>" +
    '<input id="inv-email" type="email" autocomplete="off" placeholder="name@example.com">' +
    '<button type="submit" class="cta-btn">Create invite link</button>' +
    '<p class="formmsg" id="inv-msg"></p></form>' +
    '<div id="invitelist"></div></div>'
  );
}

function mailtoFor(iv) {
  const subject = "You're invited to The Gallery";
  const body = "You've been invited to join The Gallery as a Curator.\n\n" +
    "Open this link to create your account:\n" + iv.url + "\n\n(The link expires in 14 days.)";
  return "mailto:" + iv.email + "?subject=" + encodeURIComponent(subject) +
    "&body=" + encodeURIComponent(body);
}

function renderInvites(list) {
  const box = $("#invitelist");
  if (!box) return;
  if (!list.length) { box.innerHTML = '<p class="tiny invnone">No pending invites.</p>'; return; }
  box.innerHTML = list.map((iv) =>
    '<div class="invrow"><div class="invmeta"><span class="uname">' + esc(iv.email) + "</span>" +
    '<span class="tiny">' + esc(iv.role) + " · invited " + esc((iv.created || "").split(" ")[0]) + "</span>" +
    '<input class="invlink" readonly value="' + esc(iv.url) + '"></div>' +
    '<div class="invact">' +
    '<button class="linkbtn" data-copy="' + esc(iv.url) + '">copy</button>' +
    '<a class="linkbtn" href="' + esc(mailtoFor(iv)) + '">email</a>' +
    '<button class="danger" data-revoke="' + esc(iv.token) + '">revoke</button>' +
    "</div></div>").join("");
  box.querySelectorAll("[data-copy]").forEach((b) =>
    b.addEventListener("click", () => {
      const done = () => toast("Invite link copied.");
      if (navigator.clipboard) navigator.clipboard.writeText(b.dataset.copy).then(done).catch(() => {});
    }));
  box.querySelectorAll("[data-revoke]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Revoke this invite? The link will stop working.")) return;
      try {
        await api("/api/invites/" + encodeURIComponent(b.dataset.revoke), { method: "DELETE" });
        reloadInvites();
      } catch (e) { alert(e.message); }
    }));
}

async function reloadInvites() {
  try { renderInvites((await api("/api/invites")).invites); } catch (e) {}
}

function wireInvites() {
  const form = $("#invcreate");
  if (form) form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#inv-msg"); msg.className = "formmsg";
    try {
      await api("/api/invites", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: $("#inv-email").value }),
      });
      $("#inv-email").value = "";
      msg.className = "formmsg ok"; msg.textContent = "Invite created — copy or email the link below.";
      reloadInvites();
    } catch (err) { msg.className = "formmsg err"; msg.textContent = err.message; }
  });
  reloadInvites();
}

function roleOptions(current) {
  return ["owner", "curator", "visitor"].map((r) =>
    '<option value="' + r + '"' + (current === r ? " selected" : "") + ">" +
    r.charAt(0).toUpperCase() + r.slice(1) + "</option>").join("");
}

function renderUsers(list) {
  const box = $("#userlist");
  const me = SESSION.user ? SESSION.user.username.toLowerCase() : "";
  box.innerHTML = list.map((u) => {
    const self = (u.username || "").toLowerCase() === me;
    // Your own role isn't yours to change, so it reads as a pill rather than a
    // control you can't use.
    const roleCtl = self
      ? '<span class="role-badge ' + esc(u.role) + '">' + esc(u.role) + "</span>"
      : '<select class="urole" data-user="' + esc(u.username) + '">' +
        roleOptions(u.role) + "</select>";
    return (
      '<div class="urow"><div class="umeta"><span class="uname">' + esc(u.username) +
      (self ? ' <span class="tiny">(you)</span>' : "") + "</span>" +
      '<span class="tiny">since ' + esc((u.created || "").split(" ")[0]) + "</span></div>" +
      '<div class="uact">' + roleCtl +
      '<button class="linkbtn" data-pw="' + esc(u.username) + '">reset password</button>' +
      (self ? "" : '<button class="danger" data-del="' + esc(u.username) + '">delete</button>') +
      "</div></div>"
    );
  }).join("");

  box.querySelectorAll(".urole").forEach((s) =>
    s.addEventListener("change", async () => {
      try {
        await api("/api/users/" + encodeURIComponent(s.dataset.user) + "/role", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: s.value }),
        });
        toast(s.dataset.user + " is now a " + s.value + ".");
      } catch (e) { alert(e.message); reloadUsers(); }
    }));
  box.querySelectorAll("[data-pw]").forEach((b) =>
    b.addEventListener("click", async () => {
      const pw = prompt("New password for " + b.dataset.pw + " (min 6 characters):");
      if (pw == null) return;
      try {
        await api("/api/users/" + encodeURIComponent(b.dataset.pw) + "/password", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: pw }),
        });
        toast("Password updated.");
      } catch (e) { alert(e.message); }
    }));
  box.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Delete the account “" + b.dataset.del + "”?\n\nTheir collections stay in the gallery.")) return;
      try {
        await api("/api/users/" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
        reloadUsers();
      } catch (e) { alert(e.message); }
    }));
}

async function reloadUsers() {
  try {
    const d = await api("/api/users");
    renderUsers(d.users);
  } catch (e) {}
}

function wireAddUser() {
  $("#adduser").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#nu-msg");
    msg.className = "formmsg";
    try {
      await api("/api/users", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("#nu-user").value, password: $("#nu-pass").value, role: $("#nu-role").value,
        }),
      });
      msg.className = "formmsg ok";
      msg.textContent = "Added.";
      $("#nu-user").value = ""; $("#nu-pass").value = "";
      reloadUsers();
    } catch (err) {
      msg.className = "formmsg err";
      msg.textContent = err.message;
    }
  });
}

/* ---------- custom sources ---------- */

function renderSourceList(sources) {
  const box = $("#srclist");
  if (!sources.length) {
    box.innerHTML = '<div class="emptybox">None yet. Add one on the right — or load a preset to start.</div>';
    return;
  }
  box.innerHTML = sources.map((s) =>
    '<div class="srcrow"><div class="srcmeta"><span class="nm">' + esc(s.label) +
    '</span><span class="id">' + esc(s.id) + "</span></div>" +
    '<div class="srcact"><button class="linkbtn" data-edit="' + esc(s.id) + '">edit</button>' +
    '<button class="danger" data-del="' + esc(s.id) + '">delete</button></div></div>'
  ).join("");
  box.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Delete custom source '" + b.dataset.del + "'?")) return;
      await api("/api/custom_sources/" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
      sourcesCache = null;
      settingsView();
    }));
  box.querySelectorAll("[data-edit]").forEach((b) =>
    b.addEventListener("click", () => {
      const s = sources.find((x) => x.id === b.dataset.edit);
      if (s) fillSourceForm(s);
    }));
}

function sourceFormHtml(presets) {
  const presetOpts = presets.map((p, i) =>
    '<option value="' + i + '">' + esc(p.label) + "</option>").join("");
  const fld = (k) =>
    '<label class="fm">' + k + '<input id="cs-f-' + k + '" autocomplete="off"></label>';
  return (
    '<div class="pagehead" style="margin-bottom:12px"><p class="sub">Add / edit a source</p></div>' +
    '<form class="srcform" id="srcform">' +
    (presetOpts
      ? '<label>Load a preset <span class="tiny">(starting point — paste your own API key)</span>' +
        '<div class="row2"><select id="cs-preset"><option value="">—</option>' + presetOpts +
        '</select><button type="button" id="cs-loadpreset" class="toolbtn">Load</button></div></label>'
      : "") +
    '<div class="row2"><label>Label<input id="cs-label" autocomplete="off" placeholder="Harvard Art Museums"></label>' +
    '<label>Id <span class="tiny">(auto)</span><input id="cs-id" autocomplete="off" placeholder="harvard"></label></div>' +
    "<label>Search URL <span class=\"tiny\">use {query} and, for paging, {page}</span>" +
    '<textarea id="cs-url" rows="3" placeholder="https://api.museum.org/search?q={query}&amp;page={page}"></textarea></label>' +
    '<div class="row2"><label>Items path <span class="tiny">to the results array</span>' +
    '<input id="cs-items" autocomplete="off" placeholder="records"></label>' +
    '<label>Placeholder<input id="cs-ph" autocomplete="off" placeholder="Artist name"></label></div>' +
    '<label>Hint<input id="cs-hint" autocomplete="off"></label>' +
    '<div class="fmgrid"><div class="fmhdr">Field mappings <span class="tiny">dotted paths into each item, e.g. people.0.name</span></div>' +
    fieldKeys.map(fld).join("") + "</div>" +
    '<div class="row3"><label class="chk"><input type="checkbox" id="cs-af" checked> Filter by artist name</label>' +
    '<label>Page start<input id="cs-ps" type="number" value="1" style="width:70px"></label>' +
    '<label>Max pages<input id="cs-mp" type="number" value="10" style="width:70px"></label></div>' +
    '<div class="testbox"><div class="row2"><input id="cs-testq" placeholder="Test with an artist, e.g. Rembrandt">' +
    '<button type="button" id="cs-test" class="toolbtn">Test</button></div>' +
    '<div id="cs-testout" class="testout"></div></div>' +
    '<div class="bf-actions"><button type="submit" class="cta-btn">Save source</button>' +
    '<button type="button" id="cs-clear" class="linkbtn">clear</button>' +
    '<span id="cs-msg" class="formmsg"></span></div></form>'
  );
}

function readSourceForm() {
  const fields = {};
  fieldKeys.forEach((k) => { fields[k] = $("#cs-f-" + k).value.trim(); });
  return {
    id: $("#cs-id").value.trim(),
    label: $("#cs-label").value.trim(),
    hint: $("#cs-hint").value.trim(),
    placeholder: $("#cs-ph").value.trim(),
    search_url: $("#cs-url").value.trim(),
    items_path: $("#cs-items").value.trim(),
    fields: fields,
    artist_filter: $("#cs-af").checked,
    page_start: parseInt($("#cs-ps").value, 10) || 0,
    max_pages: parseInt($("#cs-mp").value, 10) || 10,
  };
}

function fillSourceForm(s) {
  $("#cs-id").value = s.id || "";
  $("#cs-label").value = s.label || "";
  $("#cs-hint").value = s.hint || "";
  $("#cs-ph").value = s.placeholder || "";
  $("#cs-url").value = s.search_url || "";
  $("#cs-items").value = s.items_path || "";
  const f = s.fields || {};
  fieldKeys.forEach((k) => { $("#cs-f-" + k).value = f[k] || ""; });
  $("#cs-af").checked = s.artist_filter !== false;
  $("#cs-ps").value = s.page_start != null ? s.page_start : 1;
  $("#cs-mp").value = s.max_pages != null ? s.max_pages : 10;
  $("#cs-testout").innerHTML = "";
  $("#cs-msg").textContent = "";
  window.scrollTo(0, 0);
}

function wireSourceForm(presets) {
  const loadBtn = $("#cs-loadpreset");
  if (loadBtn) loadBtn.addEventListener("click", () => {
    const i = $("#cs-preset").value;
    if (i === "") return;
    const p = presets[parseInt(i, 10)];
    if (p) {
      fillSourceForm(p.def);
      const msg = $("#cs-msg");
      msg.className = "formmsg";
      msg.textContent = p.note || "";
    }
  });

  // auto-fill id from label until the user types their own id
  let idEdited = false;
  $("#cs-id").addEventListener("input", () => { idEdited = true; });
  $("#cs-label").addEventListener("input", () => {
    if (!idEdited)
      $("#cs-id").value = $("#cs-label").value.trim().toLowerCase()
        .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  });

  $("#cs-clear").addEventListener("click", () => {
    $("#srcform").reset();
    $("#cs-testout").innerHTML = "";
    $("#cs-msg").textContent = "";
    idEdited = false;
  });

  $("#cs-test").addEventListener("click", async () => {
    const out = $("#cs-testout");
    const q = $("#cs-testq").value.trim();
    if (!q) { out.innerHTML = '<span class="err">Enter a test artist name.</span>'; return; }
    out.innerHTML = "Testing…";
    try {
      const r = await api("/api/custom_sources/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ def: readSourceForm(), query: q }),
      });
      out.innerHTML = renderTestResult(r);
    } catch (e) { out.innerHTML = '<span class="err">' + esc(e.message) + "</span>"; }
  });

  $("#srcform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#cs-msg");
    msg.className = "formmsg";
    try {
      await api("/api/custom_sources", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(readSourceForm()),
      });
      sourcesCache = null;
      msg.className = "formmsg ok";
      msg.textContent = "Saved. It's now available on the Add page.";
      const d = await api("/api/custom_sources");
      renderSourceList(d.sources || []);
    } catch (err) {
      msg.className = "formmsg err";
      msg.textContent = err.message;
    }
  });
}

function renderTestResult(r) {
  if (!r.ok) {
    return '<span class="err">' + esc(r.error || "Test failed.") + "</span>" +
      (r.url ? '<div class="turl">' + esc(r.url) + "</div>" : "");
  }
  const rows = (r.sample || []).map((s) =>
    '<div class="trow">' + (s.passes ? "✓" : s.image ? "·" : "✗") + " " +
    esc(s.title) + (s.artist ? ' <span class="ta">— ' + esc(s.artist) + "</span>" : "") + "</div>").join("");
  return (
    '<div class="tsum"><b>' + r.matched + "</b> of " + r.records +
    " records on page 1 would be saved · " + r.with_image + " have an image.</div>" +
    (rows ? '<div class="tsample">' + rows + "</div>" : "") +
    (r.matched === 0 ? '<div class="err">Nothing matched — check the image mapping, items path, or turn off the artist filter.</div>' : "")
  );
}

/* ============================== modal + toast ============================== */

/* Drag a modal around by its heading. The placard editor sits on top of the very
   painting you're reading, so it needs to get out of the way. Uses a transform so
   the backdrop's flex centring stays intact. */
function makeModalDraggable(m, handle) {
  const box = m.el.querySelector(".modal");
  if (!box || !handle) return;
  let ox = 0, oy = 0, sx = 0, sy = 0;
  const onMove = (e) => {
    box.style.transform =
      "translate(" + (ox + e.clientX - sx) + "px," + (oy + e.clientY - sy) + "px)";
  };
  const onUp = (e) => {
    ox += e.clientX - sx; oy += e.clientY - sy;
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  handle.classList.add("modal-drag");
  handle.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || e.target.closest("button, input, select, textarea, a")) return;
    sx = e.clientX; sy = e.clientY;
    e.preventDefault();                       // don't start a text selection mid-drag
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

/* Owner-facing detail of the last Auto fill call: the exact request and whatever
   came back, so a bad key/model/endpoint is diagnosable without leaving the
   editor. The API key is never part of the trace (see ai._chat). */
function traceHtml(trace) {
  if (!trace || !Object.keys(trace).length) return "";
  const req = trace.request || {};
  const head = [
    "POST " + (trace.endpoint || "?"),
    "model: " + (trace.model || "?") +
      (req.temperature != null ? "    temperature: " + req.temperature : "") +
      (req.max_tokens != null ? "    max_tokens: " + req.max_tokens : "") +
      (trace.timeout != null ? "    timeout: " + trace.timeout + "s" : ""),
    trace.status != null
      ? "HTTP " + trace.status + (trace.ms != null ? "  ·  " + trace.ms + " ms" : "")
      : (trace.ms != null ? "no response after " + trace.ms + " ms" : "no response"),
  ];
  if (trace.error) head.push("error: " + trace.error);
  const parts = ['<pre class="tr-block">' + esc(head.join("\n")) + "</pre>"];
  (req.messages || []).forEach((mm) => {
    parts.push('<div class="tr-h">request · ' + esc(mm.role) + "</div>" +
               '<pre class="tr-block">' + esc(mm.content) + "</pre>");
  });
  if (trace.response) {
    parts.push('<div class="tr-h">raw response</div>' +
               '<pre class="tr-block">' + esc(trace.response) + "</pre>");
  }
  if (trace.fields && Object.keys(trace.fields).length) {
    parts.push('<div class="tr-h">parsed fields</div>' +
               '<pre class="tr-block">' + esc(JSON.stringify(trace.fields, null, 1)) + "</pre>");
  }
  return '<details class="ew-trace"><summary>Request / response</summary>' +
         parts.join("") + "</details>";
}

function modal(html) {
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop";
  wrap.innerHTML = '<div class="modal">' + html + "</div>";
  // In true fullscreen only the fullscreen element renders, so parent there.
  (document.fullscreenElement || document.body).appendChild(wrap);
  function onKey(e) { if (e.key === "Escape") close(); }
  function close() { wrap.remove(); document.removeEventListener("keydown", onKey); }
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  document.addEventListener("keydown", onKey);
  return { el: wrap, close: close };
}

let toastTimer = null;
function toast(msg) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2400);
}

/* ============================== fullscreen viewer ============================== */

const viewer = $("#viewer"), vstage = $("#vstage"), vimg = $("#vimg"),
      vcap = $("#vcap"), vcount = $("#vcount");
const V = { list: [], i: 0, wasFullscreen: false };
let idleTimer = null, dragState = null, dragMoved = false;

function wake() {
  viewer.classList.remove("idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => viewer.classList.add("idle"), 2600);
}

function caption(w) {
  const bits = [w.artist, w.date || w.year, w.medium].filter(Boolean).join(" · ");
  const src = w.source_url
    ? ' &nbsp;<a href="' + esc(w.source_url) + '" target="_blank" rel="noopener">source ↗</a>'
    : "";
  // The viewer shows a screen-sized image; offer the true original one click away.
  const full = ' &nbsp;<a href="' + origSrc(w) + '" target="_blank" rel="noopener">full resolution ↗</a>';
  return '<div class="vtitle">' + esc(w.title) + '</div><div class="vmeta">' + esc(bits) + src + full + "</div>";
}

function setFit(fit) {
  viewer.classList.toggle("fit", fit);
  viewer.classList.toggle("actual", !fit);
}

/* ---------- placards (museum wall labels shown in the viewer) ---------- */
function placardsOn() { return localStorage.getItem("placards") === "1"; }
function setPlacards(on) { localStorage.setItem("placards", on ? "1" : "0"); }

/* Rich descriptions: the placard editor's format bar produces a small set of
   tags (b/i/u, <font face|size>, line-break divs). Everything else — including
   whatever gets pasted in — is stripped by this DOM-based allowlist, applied
   both before saving and before rendering. Legacy plain-text descriptions
   render with their line breaks intact. */
const RICH_TAGS = { B: 1, I: 1, U: 1, EM: 1, STRONG: 1, BR: 1, DIV: 1, P: 1, FONT: 1 };
const RICH_FONTS = { "Georgia": 1, "Arial": 1, "Courier New": 1 };
const RICH_SIZES = { "2": 1, "3": 1, "5": 1 };

function sanitizeRich(html) {
  // Parse into a <template>: its content is INERT — images don't load and
  // event handlers never fire while we scrub. A live div would execute
  // side effects (e.g. <img onerror=…>) during the innerHTML parse itself.
  const tpl = document.createElement("template");
  tpl.innerHTML = html || "";
  const root = tpl.content;
  (function scrub(node) {
    let c = node.firstChild;
    while (c) {
      const next = c.nextSibling;
      if (c.nodeType === 3) { c = next; continue; }
      if (c.nodeType !== 1 || !RICH_TAGS[c.tagName]) {
        const first = c.firstChild;          // unwrap: keep the children, drop the node
        while (c.firstChild) node.insertBefore(c.firstChild, c);
        node.removeChild(c);
        c = first || next;
        continue;
      }
      Array.from(c.attributes).forEach((a) => {
        const keep = c.tagName === "FONT" &&
          ((a.name === "face" && RICH_FONTS[a.value]) ||
           (a.name === "size" && RICH_SIZES[a.value]));
        if (!keep) c.removeAttribute(a.name);
      });
      scrub(c);
      c = next;
    }
  })(root);
  return tpl.innerHTML;
}

const RICH_RE = /<\s*(b|i|u|em|strong|br|div|p|font)[\s>/]/i;
function richDescHtml(d) {
  return RICH_RE.test(d) ? sanitizeRich(d) : esc(d).replace(/\n/g, "<br>");
}

/* Prose the AI just wrote, ready to drop into a rich editor.

   It hands back BOTH at once: <em> around work titles and a blank line between
   paragraphs. So this can't branch on one or the other — escaping the markup
   would print the tags at the reader, and treating it as markup alone would let
   HTML collapse the newlines and run every paragraph together. Breaks become
   <br> first, then the whole thing goes through the same allowlist a placard
   uses. Text with no markup is escaped first, so a stray angle bracket in prose
   stays a bracket instead of being read as a tag. */
function aiTextToRich(s) {
  s = (s || "").replace(/\r\n?/g, "\n");
  const body = RICH_RE.test(s) ? s : esc(s);
  return sanitizeRich(body.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>"));
}

/* Placards point at each other: an italicised title that names another painting in
   this museum becomes somewhere the reader can go. The server decides which titles
   those are — it's the only party that knows the whole library — and hands over a
   {title: work id} map; this only has to make them clickable.

   Runs last, on markup sanitizeRich has already cleaned, because that allowlist
   strips <a>: added any earlier, these links would scrub themselves away. The em
   is kept and the link goes inside it, so a cross-reference still reads as a title. */
function linkXrefs(html, xref) {
  if (!xref) return html;
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  tpl.content.querySelectorAll("em, i").forEach((el) => {
    const id = xref[el.textContent.trim()];
    if (!id || el.querySelector("a")) return;
    const a = document.createElement("a");
    a.className = "pl-xref";
    a.href = "#";
    a.dataset.work = id;
    a.title = "Go to this painting";
    while (el.firstChild) a.appendChild(el.firstChild);
    el.appendChild(a);
  });
  return tpl.innerHTML;
}

/* Following a cross-reference slots the painting in beside the one being read, so
   ← is the way back to it and → still carries on through the walk you were on.
   Reached from a tile's "i" instead, there's no walk to join: open on its own. */
async function jumpToWork(id) {
  try {
    const w = await api("/api/work/" + encodeURIComponent(id));
    if (!viewer.classList.contains("open")) return void openViewer([w], 0);
    const from = (V.list[V.i] || {}).title || "";
    V.list.splice(V.i + 1, 0, w);
    showWork(V.i + 1);
    if (from) viewerFlash("← back to " + from);
  } catch (e) {
    toast(e.message);
  }
}

/* One listener for every placard, wherever it is drawn: the viewer repaints its
   card on each work, and the "i" modal builds another. */
function wireXrefs(root, before) {
  root.querySelectorAll("a.pl-xref").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();       // the viewer treats a bare click as "next"
      if (before) before();
      jumpToWork(a.dataset.work);
    }));
}

/* ---------- shared rich-text editor (placard + artist bio) ---------- */

/* Classes, not ids: the bio form and the placard editor can both be on the page,
   and two #fmt-font would make the second one unwireable. */
function fmtBarHtml() {
  return (
    '<div class="fmtbar">' +
    '<button type="button" class="fmtbtn" data-cmd="bold" title="Bold"><b>B</b></button>' +
    '<button type="button" class="fmtbtn" data-cmd="italic" title="Italic"><i>I</i></button>' +
    '<button type="button" class="fmtbtn" data-cmd="underline" title="Underline"><u>U</u></button>' +
    '<button type="button" class="fmtbtn fmt-paste" title="Paste as plain text">' +
    '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<rect x="9" y="9" width="11" height="11" rx="2"/>' +
    '<path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3"/></svg></button>' +
    '<select class="fmt-font" title="Font"><option value="">Font</option>' +
    '<option value="Georgia">Serif</option><option value="Arial">Sans</option>' +
    '<option value="Courier New">Mono</option></select>' +
    '<select class="fmt-size" title="Size"><option value="">Size</option>' +
    '<option value="2">Small</option><option value="3">Normal</option>' +
    '<option value="5">Large</option></select>' +
    "</div>"
  );
}

/* Wire a format bar to its editor. `root` scopes the lookups, `ed` is the
   contenteditable, `onErr` reports a blocked clipboard. */
function wireFmtBar(root, ed, onErr) {
  try { document.execCommand("styleWithCSS", false, false); } catch (e) {}
  root.querySelectorAll(".fmtbtn[data-cmd]").forEach((b) => {
    b.addEventListener("mousedown", (e) => e.preventDefault());  // keep the text selection
    b.addEventListener("click", () => { ed.focus(); document.execCommand(b.dataset.cmd); });
  });

  /* Paste as plain text: insert the clipboard with its markup stripped, so text
     copied from a web page doesn't drag that page's fonts and colours in. */
  const pasteBtn = root.querySelector(".fmt-paste");
  pasteBtn.addEventListener("mousedown", (e) => e.preventDefault());
  pasteBtn.addEventListener("click", async () => {
    ed.focus();
    try {
      const text = await navigator.clipboard.readText();
      if (text) document.execCommand("insertText", false, text);
    } catch (err) {
      // Reading the clipboard needs a secure context + permission; fall back to
      // telling them the keyboard shortcut that does the same thing.
      if (onErr) onErr("Your browser blocked clipboard access — use Ctrl+Shift+V " +
                       "to paste without formatting.");
    }
  });
  const fontSel = root.querySelector(".fmt-font"), sizeSel = root.querySelector(".fmt-size");
  fontSel.addEventListener("change", () => {
    if (fontSel.value) { ed.focus(); document.execCommand("fontName", false, fontSel.value); fontSel.value = ""; }
  });
  sizeSel.addEventListener("change", () => {
    if (sizeSel.value) { ed.focus(); document.execCommand("fontSize", false, sizeSel.value); sizeSel.value = ""; }
  });
}

function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

/* House style: a work's own name is italicised wherever it appears in its placard
   text. Applied when the placard renders, so the description stays plain prose —
   nothing to maintain by hand. Walks text nodes rather than regexing the HTML, so
   tags and attributes are never touched, and skips anything already inside an
   <em>/<i> so we don't double up. */
function italicizeTitle(html, title) {
  const t = (title || "").trim();
  // Very short or generic names ("Untitled", "Two") would match ordinary prose.
  if (t.length < 4 || /^untitled$/i.test(t)) return html;
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  const re = new RegExp(escapeRe(t), "gi");
  (function walk(node) {
    let c = node.firstChild;
    while (c) {
      const next = c.nextSibling;
      if (c.nodeType === 3) {
        const s = c.nodeValue;
        re.lastIndex = 0;
        if (re.test(s)) {
          re.lastIndex = 0;
          const frag = document.createDocumentFragment();
          let last = 0, m;
          while ((m = re.exec(s))) {
            if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
            const em = document.createElement("em");
            em.textContent = m[0];                 // keep the text as written
            frag.appendChild(em);
            last = m.index + m[0].length;
          }
          if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
          node.replaceChild(frag, c);
        }
      } else if (c.nodeType === 1 && c.tagName !== "EM" && c.tagName !== "I") {
        walk(c);
      }
      c = next;
    }
  })(tpl.content);
  return tpl.innerHTML;
}

/* The wall label. Artist in small caps at the head, the work's own name in
   italic, then the facts, a rule, and the reading. */
function placardHtml(w) {
  const date = w.date || (w.year ? String(w.year) : "");
  const meta = [date, w.medium].filter(Boolean).join(" · ");
  const desc = w.description
    ? '<div class="pl-desc">' +
      linkXrefs(italicizeTitle(richDescHtml(w.description), w.title), w.xref) + "</div>"
    : (isOwner() ? '<div class="pl-desc pl-empty">No description yet.</div>' : "");
  // Owner only, matching the route that saves it — the design says owner/curator,
  // but /api/work/<id> is owner-gated and widening who may rewrite a placard is
  // not a call to make in passing.
  const edit = isOwner()
    ? '<button class="pl-edit" id="pl-edit" type="button">Edit placard</button>' : "";
  const src = w.source_url
    ? '<span class="pl-src"><a href="' + esc(w.source_url) +
      '" target="_blank" rel="noopener">Source ↗</a></span>'
    : "";
  const foot = (edit || src) ? '<div class="pl-foot">' + edit + src + "</div>" : "";
  const artist = w.artist
    ? '<a class="pl-artist" id="pl-artist" href="#/artist/' + encodeURIComponent(w.artist) + '">' + esc(w.artist) + "</a>"
    : '<div class="pl-artist">Unknown artist</div>';
  return '<div class="pl-card">' +
    '<button class="pl-close" id="pl-close" type="button" aria-label="Hide placard">✕</button>' +
    artist +
    '<div class="pl-title">' + esc(w.title) + "</div>" +
    (meta ? '<div class="pl-medium">' + esc(meta) + "</div>" : "") +
    '<div class="pl-rule"></div>' +
    desc + foot + "</div>";
}

/* The "i" on a tile: read a piece's placard without opening the viewer. Same card
   the viewer shows, so the reading is identical — its buttons just mean modal
   things here (× closes this, Edit hands off to the editor). Queries are scoped to
   the modal: the viewer's own hidden placard may still hold the same ids. */
function workInfoDialog(w) {
  const m = modal(placardHtml(w));
  m.el.querySelector(".modal").classList.add("modal-placard");
  const q = (s) => m.el.querySelector(s);
  const close = q("#pl-close");
  if (close) close.addEventListener("click", m.close);
  const edit = q("#pl-edit");
  if (edit) edit.addEventListener("click", () => { m.close(); editWorkDialog(w); });
  const artist = q("#pl-artist");
  if (artist) artist.addEventListener("click", (e) => {
    e.preventDefault();
    m.close();
    location.hash = artist.getAttribute("href");
  });
  wireXrefs(m.el, m.close);   // drop the card, then open the painting it named
}

/* Collapsed placards persist for the session only: the checkbox in Settings is
   the standing preference, this is "get out of the way for a minute". */
function placardCollapsed() { return sessionStorage.getItem("pl-collapsed") === "1"; }
function setPlacardCollapsed(on) {
  sessionStorage.setItem("pl-collapsed", on ? "1" : "0");
  syncPlacard();
}

function syncPlacard() {
  const on = placardsOn();
  const collapsed = placardCollapsed();
  viewer.classList.toggle("placards", on);
  viewer.classList.toggle("collapsed", on && collapsed);
  const el = document.getElementById("placard");
  if (!el) return;
  if (on && !collapsed && viewer.classList.contains("open") && V.list[V.i]) {
    el.innerHTML = placardHtml(V.list[V.i]);
    el.hidden = false;
    const eb = document.getElementById("pl-edit");
    if (eb) eb.addEventListener("click", () => editWorkDialog(V.list[V.i]));
    // ✕ folds the placard to its pill for now; the Settings checkbox and the
    // "p" key remain the way to turn placards off for good.
    const cb = document.getElementById("pl-close");
    if (cb) cb.addEventListener("click", () => setPlacardCollapsed(true));
    const pa = document.getElementById("pl-artist");
    if (pa) pa.addEventListener("click", (e) => {   // leave the viewer, then open the artist
      e.preventDefault();
      closeViewer();
      location.hash = pa.getAttribute("href");
    });
    wireXrefs(el);   // stay in the viewer; the painting joins this walk
  } else {
    el.hidden = true;
  }
}

/* The sibling works, so you can see where you are in a room without leaving it. */
function syncFilm() {
  const film = document.getElementById("vfilm");
  if (!film) return;
  if (V.list.length < 2) { film.innerHTML = ""; return; }
  film.innerHTML = V.list.map((w, i) =>
    '<img src="' + thumbSrc(w) + '" alt="" data-i="' + i + '"' +
    (i === V.i ? ' class="on"' : "") + ">").join("");
  film.querySelectorAll("img").forEach((im) =>
    im.addEventListener("click", (e) => { e.stopPropagation(); showWork(+im.dataset.i); }));
  const cur = film.querySelector("img.on");
  if (cur) cur.scrollIntoView({ block: "nearest", inline: "center" });
}

/* Owner-only: edit a work's placard details, saved to its sidecar.
   "Auto fill" (Settings → Auto-fill configures the model + key) asks the AI to
   research this painting and populate the fields; the owner reviews and saves. */
function editWorkDialog(w) {
  const m = modal(
    "<h2>Edit placard</h2>" +
    '<form class="authform" id="ewform">' +
    '<div class="ew-autobar">' +
    '<button type="button" class="toolbtn" id="ew-auto">Auto fill</button>' +
    '<span class="tiny ew-autohint">Researches this painting and fills the fields below — review before saving.</span>' +
    '<span class="formmsg" id="ew-auto-msg"></span></div>' +
    '<label class="ew-hintrow">Which painting? ' +
    '<span class="tiny">optional · sent with Auto fill only — use it when the artist ' +
    "has several works sharing this title</span>" +
    '<textarea id="ew-hint" rows="2" placeholder="e.g. the 1887 self-portrait in a ' +
    'grey felt hat, held by the Art Institute of Chicago"></textarea></label>' +
    '<div id="ew-trace-box"></div>' +
    "<label>Title<input id=\"ew-title\" autocomplete=\"off\"></label>" +
    "<label>Artist<input id=\"ew-artist\" autocomplete=\"off\"></label>" +
    "<label>Date <span class=\"tiny\">optional</span><input id=\"ew-date\" autocomplete=\"off\"></label>" +
    "<label>Medium <span class=\"tiny\">optional</span><input id=\"ew-medium\" autocomplete=\"off\"></label>" +
    '<div class="row3">' +
    "<label>Style <span class=\"tiny\">movement</span><input id=\"ew-style\" autocomplete=\"off\"></label>" +
    "<label>Genre <span class=\"tiny\">subject</span><input id=\"ew-genre\" autocomplete=\"off\"></label>" +
    "<label>School <span class=\"tiny\">regional</span><input id=\"ew-school\" autocomplete=\"off\"></label>" +
    "</div>" +
    "<label>Description</label>" +
    fmtBarHtml() +
    '<div class="richtext" id="ew-desc" contenteditable="true"></div>' +
    '<div class="bf-actions"><button type="submit" class="cta-btn">Save</button>' +
    '<button type="button" class="linkbtn" id="ew-cancel">cancel</button>' +
    '<span class="formmsg err" id="ew-msg"></span></div></form>');
  m.el.querySelector(".modal").classList.add("modal-wide");
  const q = (id) => m.el.querySelector(id);
  const ed = q("#ew-desc");
  makeModalDraggable(m, m.el.querySelector("h2"));
  const showTrace = (t) => { q("#ew-trace-box").innerHTML = traceHtml(t); };

  wireFmtBar(m.el, ed, (msg) => { q("#ew-msg").textContent = msg; });

  /* ---- current values ---- */
  q("#ew-title").value = w.title || "";
  q("#ew-artist").value = w.artist || "";
  q("#ew-date").value = w.date || (w.year ? String(w.year) : "");
  q("#ew-medium").value = w.medium || "";
  q("#ew-style").value = w.style || "";
  q("#ew-genre").value = w.genre || "";
  q("#ew-school").value = w.school || "";
  ed.innerHTML = richDescHtml(w.description || "");   // legacy plain text gets its \n as <br>

  const flash = (el) => { el.classList.add("justfilled"); setTimeout(() => el.classList.remove("justfilled"), 900); };

  /* ---- Auto fill: research the work and populate the form (owner reviews, then Saves) ---- */
  q("#ew-auto").addEventListener("click", async () => {
    const btn = q("#ew-auto"), msg = q("#ew-auto-msg"), label = btn.textContent;
    btn.disabled = true; btn.textContent = "Researching…";
    msg.className = "formmsg"; msg.textContent = "";
    try {
      // Omitted when blank, so the request stays exactly as it was without a hint.
      const hint = q("#ew-hint").value.trim();
      const r = await api("/api/work/" + encodeURIComponent(w.id) + "/autofill", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(hint ? { hint: hint } : {}),
      });
      const f = r.fields || {};
      const set = (id, v) => { if (v) { const el = q(id); el.value = v; flash(el); } };
      set("#ew-title", f.title); set("#ew-artist", f.artist); set("#ew-date", f.date);
      set("#ew-medium", f.medium); set("#ew-style", f.style);
      set("#ew-genre", f.genre); set("#ew-school", f.school);
      if (f.description) { ed.innerHTML = aiTextToRich(f.description); flash(ed); }
      showTrace(r.trace);
      const names = Object.keys(f);
      msg.className = "formmsg ok";
      msg.textContent = names.length
        ? "Filled " + names.join(", ") + ". Review, then Save."
        : "Nothing found for this one.";
    } catch (e) {
      msg.className = "formmsg err"; msg.textContent = e.message;
      showTrace(e.body && e.body.trace);   // the failing call is the one worth reading
    } finally { btn.disabled = false; btn.textContent = label; }
  });

  q("#ew-cancel").addEventListener("click", () => m.close());
  q("#ewform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      title: q("#ew-title").value, artist: q("#ew-artist").value,
      date: q("#ew-date").value, medium: q("#ew-medium").value,
      style: q("#ew-style").value, genre: q("#ew-genre").value,
      school: q("#ew-school").value,
      description: ed.textContent.trim() ? sanitizeRich(ed.innerHTML) : "",
    };
    try {
      const r = await api("/api/work/" + encodeURIComponent(w.id), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      m.close();
      if (r.work) { V.list[V.i] = r.work; vcap.innerHTML = caption(r.work); syncPlacard(); }
      viewerFlash("✓ Saved");
    } catch (err) { q("#ew-msg").textContent = err.message; }
  });
}

function showWork(i) {
  const n = V.list.length;
  V.i = ((i % n) + n) % n;
  const w = V.list[V.i];
  viewer.classList.add("loading");
  setFit(true);
  vimg.src = viewSrc(w);
  vcap.innerHTML = caption(w);
  syncPlacard();
  syncFilm();
  vcount.textContent = n > 1 ? (V.i + 1) + " / " + n : "";
  const va = document.getElementById("vartist");
  if (va) va.textContent = w.artist || "";
  if (n > 1) {
    [V.i + 1, V.i - 1].forEach((k) => {
      const pw = V.list[((k % n) + n) % n];
      new Image().src = viewSrc(pw);
    });
  }
}
vimg.addEventListener("load", () => viewer.classList.remove("loading"));

function openViewer(list, i) {
  if (!list || !list.length) return;
  V.list = list;
  viewer.classList.add("open");
  document.body.style.overflow = "hidden";
  showWork(i);
  wake();
  if (viewer.requestFullscreen) {
    viewer.requestFullscreen().then(() => { V.wasFullscreen = true; }).catch(() => {});
  }
}

function closeViewer() {
  if (!viewer.classList.contains("open")) return;
  viewer.classList.remove("open");
  document.body.style.overflow = "";
  vimg.removeAttribute("src");
  V.wasFullscreen = false;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && V.wasFullscreen) {
    V.wasFullscreen = false;
    closeViewer();
  }
});

$("#vprev").addEventListener("click", (e) => { e.stopPropagation(); showWork(V.i - 1); });
$("#vnext").addEventListener("click", (e) => { e.stopPropagation(); showWork(V.i + 1); });
$("#vclose").addEventListener("click", (e) => { e.stopPropagation(); closeViewer(); });
$("#plpill").addEventListener("click", (e) => { e.stopPropagation(); setPlacardCollapsed(false); });

document.addEventListener("keydown", (e) => {
  if (!viewer.classList.contains("open")) return;
  if (document.querySelector(".modal-backdrop")) return;  // a chooser/dialog is up — it owns the keys
  if (e.key === "ArrowRight") showWork(V.i + 1);
  else if (e.key === "ArrowLeft") showWork(V.i - 1);
  else if (e.key === "Escape") closeViewer();
  else if (e.key === "c" || e.key === "C") collectHotkey();
  else if (e.key === "p" || e.key === "P") {
    // Turning placards on should actually show one, even if the last one was
    // folded away to its pill.
    const on = !placardsOn();
    setPlacards(on);
    if (on) sessionStorage.setItem("pl-collapsed", "0");
    syncPlacard();
  }
  else if (e.key === "f" || e.key === "F") {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else if (viewer.requestFullscreen)
      viewer.requestFullscreen().then(() => { V.wasFullscreen = true; }).catch(() => {});
  }
});

/* Brief confirmation drawn INSIDE the viewer, so it shows over the painting and
   even in true fullscreen (where page-level toasts aren't rendered). */
let vflashTimer = null;
function viewerFlash(msg) {
  let f = document.getElementById("vflash");
  if (!f) { f = document.createElement("div"); f.id = "vflash"; viewer.appendChild(f); }
  f.textContent = msg;
  f.classList.add("show");
  clearTimeout(vflashTimer);
  vflashTimer = setTimeout(() => f.classList.remove("show"), 2000);
}

/* ---- hotkey "c": add the painting on screen to a collection ---- */
function collectHotkey() {
  if (!canCurate()) { toast("Only curators and owners can build collections."); return; }
  const work = V.list[V.i];
  if (work) addWorkToCollection(work);
}

async function addWorkToCollection(work) {
  let mine;
  try { mine = (await api("/api/collections")).collections.filter((c) => c.can_edit); }
  catch (e) { toast(e.message); return; }

  const addOne = async (cid) => {
    try {
      const r = await api("/api/collection/" + encodeURIComponent(cid) + "/works", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: [work.id] }),
      });
      viewerFlash("✓ Added to “" + r.collection.title + "”");
    } catch (e) { viewerFlash("⚠ " + e.message); }
  };

  if (!mine.length) newCollectionDialog((col) => addOne(col.id));  // none yet: make one, then add
  else if (mine.length === 1) addOne(mine[0].id);                 // exactly one: straight in
  else openCollectPicker(mine, work, addOne);                     // several: keyboard picker
}

/* Keyboard-navigable collection chooser: ↑/↓ move, Enter adds, Esc cancels.
   Uses capture-phase keys so it beats the viewer's own arrow/Escape handler. */
function openCollectPicker(collections, work, addOne) {
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop";
  wrap.innerHTML =
    '<div class="modal collect-picker"><h2>Add to collection</h2>' +
    '<p class="rp-sub">“' + esc(work.title || "This work") +
    '” — ↑ / ↓ to choose, Enter to add, Esc to cancel.</p>' +
    '<div class="addmenu-list">' +
    collections.map((c, i) =>
      '<button class="addmenu-item cp-row' + (i === 0 ? " active" : "") + '">' +
      esc(c.title) + ' <span class="tiny">' + c.count + "</span></button>").join("") +
    "</div></div>";
  (document.fullscreenElement || document.body).appendChild(wrap);

  const rows = Array.from(wrap.querySelectorAll(".cp-row"));
  let idx = 0;
  const paint = () => rows.forEach((r, i) => {
    r.classList.toggle("active", i === idx);
    if (i === idx) r.scrollIntoView({ block: "nearest" });
  });
  const close = () => { document.removeEventListener("keydown", onKey, true); wrap.remove(); };
  const choose = (i) => { close(); addOne(collections[i].id); };

  function onKey(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      idx = e.key === "ArrowDown" ? Math.min(rows.length - 1, idx + 1) : Math.max(0, idx - 1);
      paint(); e.preventDefault(); e.stopPropagation();
    } else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); choose(idx); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  }
  document.addEventListener("keydown", onKey, true);
  rows.forEach((r, i) => {
    r.addEventListener("mouseenter", () => { idx = i; paint(); });
    r.addEventListener("click", () => choose(i));
  });
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
}

viewer.addEventListener("pointermove", wake);

/* click the painting: toggle between fit-to-screen and 1:1 pixels. Bound to the
   stage, not the image, so a click while zoomed in still zooms back out even
   though panning has the pointer captured on the stage (which would otherwise
   swallow the image's click). */
vstage.addEventListener("click", (e) => {
  if (dragMoved) { dragMoved = false; return; }
  if (viewer.classList.contains("fit")) {
    if (e.target !== vimg) return;   // in fit view only the painting zooms in, not the letterbox
    const r = vimg.getBoundingClientRect();
    const s = Math.min(r.width / vimg.naturalWidth, r.height / vimg.naturalHeight);
    const dw = vimg.naturalWidth * s, dh = vimg.naturalHeight * s;
    const ox = r.left + (r.width - dw) / 2, oy = r.top + (r.height - dh) / 2;
    const fx = Math.max(0, Math.min(1, (e.clientX - ox) / dw));
    const fy = Math.max(0, Math.min(1, (e.clientY - oy) / dh));
    setFit(false);
    requestAnimationFrame(() => {
      vstage.scrollLeft = fx * vimg.clientWidth - vstage.clientWidth / 2;
      vstage.scrollTop = fy * vimg.clientHeight - vstage.clientHeight / 2;
    });
  } else {
    setFit(true);   // zoomed in → any click zooms back out
  }
});

/* drag to pan at 1:1 (mouse; touch scrolls natively) */
vstage.addEventListener("pointerdown", (e) => {
  if (e.pointerType !== "mouse" || !viewer.classList.contains("actual")) return;
  dragState = { x: e.clientX, y: e.clientY, l: vstage.scrollLeft, t: vstage.scrollTop };
  dragMoved = false;
  vstage.classList.add("dragging");
  vstage.setPointerCapture(e.pointerId);
});
vstage.addEventListener("pointermove", (e) => {
  if (!dragState) return;
  const dx = e.clientX - dragState.x, dy = e.clientY - dragState.y;
  if (Math.abs(dx) + Math.abs(dy) > 6) dragMoved = true;
  vstage.scrollLeft = dragState.l - dx;
  vstage.scrollTop = dragState.t - dy;
});
["pointerup", "pointercancel"].forEach((ev) =>
  vstage.addEventListener(ev, () => { dragState = null; vstage.classList.remove("dragging"); }));

/* ============================== boot ============================== */
boot();
