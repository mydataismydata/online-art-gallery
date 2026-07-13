"use strict";

const $ = (sel) => document.querySelector(sel);
const app = $("#app");

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* ============================== session ============================== */

let SESSION = { user: null, needs_setup: false };
function role() { return SESSION.user ? SESSION.user.role : null; }
function isOwner() { return role() === "owner"; }
function canCurate() { return role() === "owner" || role() === "curator"; }

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.status + " " + r.statusText;
    try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) {}
    // A 401 while we thought we were signed in means the session lapsed — drop to login.
    if (r.status === 401 && SESSION.user) {
      SESSION.user = null;
      renderNav();
      loginView();
    }
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

function errbox(e) {
  app.innerHTML = '<div class="errbox">Something went wrong: ' + esc(e.message || e) + "</div>";
}

/* ============================== router ============================== */

let pollTimer = null;
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

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
  if (!SESSION.user) { nav.innerHTML = ""; ub.innerHTML = ""; return; }
  const links = [
    ["#/", "home", "Artists", false],
    ["#/browse/era", "browse", "Browse", false],
    ["#/collections", "collections", "Collections", false],
  ];
  if (isOwner()) {
    links.push(["#/settings", "settings", "Settings", false]);
    links.push(["#/add", "add", "Add artist", true]);
  }
  nav.innerHTML = links.map(([href, key, label, cta]) =>
    '<a href="' + href + '" data-nav="' + key + '"' + (cta ? ' class="cta"' : "") + ">" +
    esc(label) + "</a>").join("");
  const u = SESSION.user;
  ub.innerHTML =
    '<span class="who"><span class="uname">' + esc(u.username) + "</span>" +
    '<span class="role-badge ' + esc(u.role) + '">' + esc(u.role) + "</span></span>" +
    '<button id="logout" class="linkbtn">Log out</button>';
  $("#logout").addEventListener("click", doLogout);
}

async function doLogout() {
  try { await api("/api/logout", { method: "POST" }); } catch (e) {}
  SESSION = { user: null, needs_setup: false };
  renderNav();
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
  // Auth gates come before any normal routing: the whole site sits behind a login.
  if (SESSION.needs_setup) return setupView();
  if (!SESSION.user) return loginView();

  const segs = (location.hash.slice(1) || "/").split("/").filter(Boolean).map(decodeURIComponent);
  window.scrollTo(0, 0);
  if (segs.length === 0) return homeView();
  if (segs[0] === "login") return void goHome();          // already signed in
  if (segs[0] === "artist" && segs[1]) return artistView(segs[1]);
  if (segs[0] === "browse") return browseView(segs[1] || "era", segs[2] || null);
  if (segs[0] === "collections") return collectionsView();
  if (segs[0] === "collection" && segs[1]) return collectionView(segs[1]);
  if (segs[0] === "add") return isOwner() ? addView(segs[1] || "") : void goHome();
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
  renderNav();
  route();
}

/* ============================== auth views ============================== */

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
    "Welcome to The Gallery",
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
      SESSION = { user: r.user, needs_setup: false };
      renderNav();
      goHome();
    } catch (err) { msg.textContent = err.message; }
  });
  $("#su-user").focus();
}

function loginView() {
  authShell(
    "The Gallery",
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
      SESSION = { user: r.user, needs_setup: false };
      renderNav();
      goHome();
    } catch (err) { msg.textContent = err.message; }
  });
  $("#li-user").focus();
}

/* ============================== works grid + selection ============================== */

function workFigure(w, i, showArtist) {
  const meta = [showArtist ? w.artist : null, w.date || w.year, w.medium]
    .filter(Boolean).join(" · ");
  const dim = (w.width && w.height)
    ? '<span class="wdim">' + w.width + " × " + w.height + "</span>"
    : "";
  return (
    '<figure class="work" data-i="' + i + '" data-id="' + w.id + '">' +
      '<div class="wimg"><img src="/thumb/' + w.id + '" loading="lazy" alt="">' +
      '<span class="checkmark" aria-hidden="true"></span>' + dim + "</div>" +
      '<figcaption><div class="wt">' + esc(w.title) + "</div>" +
      (meta ? '<div class="wm">' + esc(meta) + "</div>" : "") +
      "</figcaption></figure>"
  );
}

/* The available Select-mode actions depend on role + page. On museum grids a
   curator can collect and an owner can also delete; on a collection they own an
   editor can remove. Visitors get no toolbar at all (the grid stays view-only). */
function browseCtx() {
  const actions = [];
  if (canCurate()) actions.push("collect");
  if (isOwner()) actions.push("delete");
  return { actions };
}
function collectionCtx(c) {
  return c.can_edit ? { actions: ["uncollect"], collectionId: c.id } : { actions: [] };
}

function worksSection(works, showArtist, ctx) {
  ctx = ctx || { actions: [] };
  const tools = ctx.actions.length
    ? '<div class="worktools"><button id="selbtn" class="toolbtn">Select</button><span id="selctl"></span></div>'
    : "";
  return (
    tools +
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

async function homeView() {
  setNav("home");
  try {
    const d = await api("/api/artists");
    if (!d.artists.length) {
      app.innerHTML =
        '<div class="emptybox"><div class="big">The gallery is empty.</div>' +
        (isOwner()
          ? 'Run <code>python import_samples.py</code> to bring in your starter paintings, ' +
            'copy images into the <code>library/</code> folder, or ' +
            '<a href="#/add">download an artist’s works</a>.'
          : "Ask an Owner to add some artworks.") +
        "</div>";
      return;
    }
    const cards = d.artists.map((a) => {
      const yr = a.year_min
        ? (a.year_max && a.year_max !== a.year_min ? a.year_min + "–" + a.year_max : a.year_min)
        : "";
      return (
        '<a class="artist-card" href="#/artist/' + encodeURIComponent(a.name) + '">' +
          '<div class="cover"><img src="/thumb/' + a.cover + '" loading="lazy" alt=""></div>' +
          '<div class="meta"><span class="name">' + esc(a.name) + "</span>" +
          '<span class="sub">' + a.count + (a.count === 1 ? " work" : " works") +
          (yr ? " · " + yr : "") + "</span></div></a>"
      );
    }).join("");
    const addCard = isOwner()
      ? '<a class="artist-card add-card" href="#/add">' +
        '<div class="cover"><span>+</span></div>' +
        '<div class="meta"><span class="name">Add artist</span>' +
        '<span class="sub">download new works</span></div></a>'
      : "";
    app.innerHTML =
      '<div class="pagehead"><h1>Artists</h1><p class="sub">' +
      d.artists.length + " painters · " + d.total_works + " works</p></div>" +
      '<div class="artist-grid">' + cards + addCard + "</div>";
  } catch (e) { errbox(e); }
}

async function artistView(name) {
  setNav("home");
  try {
    const [d, infoResp, relResp] = await Promise.all([
      api("/api/works?artist=" + encodeURIComponent(name)),
      api("/api/artist_info?name=" + encodeURIComponent(name)).catch(() => ({ info: null })),
      api("/api/artist/" + encodeURIComponent(name) + "/related").catch(() => ({ related: [] })),
    ]);
    const works = d.works;
    if (!works.length) {
      app.innerHTML = '<div class="emptybox">No works found for ' + esc(name) + ".</div>";
      return;
    }
    const years = works.map((w) => w.year).filter(Boolean);
    const span = years.length
      ? Math.min.apply(null, years) + (years.length > 1 ? "–" + Math.max.apply(null, years) : "")
      : "";
    const ownerBtns = isOwner()
      ? '<button class="linkbtn" id="rename-btn" title="Edit this artist’s name.">edit</button>' +
        '<button class="linkbtn" id="repoint-btn" title="Merge this artist into another artist already in your library — fixes the same painter appearing under different name spellings.">repoint to artist</button>'
      : "";
    const addMore = isOwner()
      ? ' <a class="inline-add" href="#/add/' + encodeURIComponent(name) + '">+ Add more from this artist</a>'
      : "";
    const bioToggle = (infoResp.info || isOwner())
      ? '<button class="disclosure" id="bio-toggle" aria-expanded="false">Bio<span class="caret">▾</span></button>'
      : "";
    app.innerHTML =
      '<div class="pagehead"><a class="back" href="#/">← All artists</a>' +
      '<div class="artist-title" id="artist-title"><h1>' + esc(name) + "</h1>" +
      ownerBtns + bioToggle + "</div>" +
      '<p class="sub">' + works.length + (works.length === 1 ? " work" : " works") +
      (span ? " · " + span : "") + addMore + "</p>" +
      '<div id="biobar" hidden></div></div>' +
      relatedDisclosureHtml(relResp.related) +
      worksSection(works, false, browseCtx());
    renderBio(name, infoResp.info);
    wireDisclosure("bio-toggle", "biobar");
    wireDisclosure("rel-toggle", "rel-strip");
    if (isOwner()) { wireRename(name); wireRepoint(name); }
    bindWorks(works, false, () => artistView(name), browseCtx());
    const g = document.getElementById("grid");
    if (g) g.classList.add("show-dims");   // dimension pills only on the artist page
  } catch (e) { errbox(e); }
}

/* ---------- disclosures (collapsible bio + related) ---------- */

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

function relatedDisclosureHtml(list) {
  if (!list || !list.length) return "";
  const cards = list.map((a) =>
    '<a class="rel-card" href="#/artist/' + encodeURIComponent(a.name) + '">' +
      '<div class="rel-cover"><img src="/thumb/' + a.cover + '" loading="lazy" alt=""></div>' +
      '<div class="rel-meta"><span class="rel-name">' + esc(a.name) + "</span>" +
      (a.why ? '<span class="rel-why">' + esc(a.why) + "</span>" : "") +
      "</div></a>"
  ).join("");
  return (
    '<div class="related">' +
    '<button class="disclosure rel-toggle" id="rel-toggle" aria-expanded="false">' +
    "Related artists<span class=\"caret\">▾</span></button>" +
    '<div class="rel-strip" id="rel-strip" hidden>' + cards + "</div></div>"
  );
}

/* ---------- artist bio (movements, dates, birthplace) ---------- */

function renderBio(name, info) {
  const box = $("#biobar");
  if (!box) return;
  if (!info) {
    box.innerHTML = isOwner()
      ? '<div class="bio empty">' +
        '<button id="bio-lookup" class="toolbtn">＋ Look up artist details</button>' +
        '<button id="bio-edit" class="linkbtn">edit manually</button>' +
        '<span id="bio-msg" class="bio-msg"></span></div>'
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
    ? '<div class="bioctl"><button id="bio-edit" class="linkbtn">edit</button>' +
      '<button id="bio-lookup" class="linkbtn">Re-fetch</button>' +
      '<span id="bio-msg" class="bio-msg"></span></div>'
    : "";
  box.innerHTML =
    '<div class="bio">' +
    (movements ? '<div class="movements">' + movements + "</div>" : "") +
    (facts.length ? '<div class="facts">' + facts.join("") + "</div>" : "") +
    (info.description ? '<div class="desc">' + esc(info.description) + "</div>" : "") +
    ctl + "</div>";
  if (isOwner()) wireBio(name, info);
}

function wireBio(name, info) {
  const lk = $("#bio-lookup");
  if (lk) lk.addEventListener("click", async () => {
    const msg = $("#bio-msg");
    msg.textContent = "Searching…";
    lk.disabled = true;
    try {
      const r = await api("/api/artist_info/lookup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!r.info) { msg.textContent = "No match found — try editing manually."; lk.disabled = false; return; }
      renderBio(name, r.info);
    } catch (e) { msg.textContent = e.message; lk.disabled = false; }
  });
  const ed = $("#bio-edit");
  if (ed) ed.addEventListener("click", () => renderBioForm(name, info || {}));
}

function renderBioForm(name, info) {
  const box = $("#biobar");
  const f = (id, label, val, extra) =>
    "<label>" + label + (extra || "") + '<input id="bf-' + id + '" value="' + esc(val || "") + '"></label>';
  box.innerHTML =
    '<form class="bioform" id="bioform">' +
    '<div class="bf-row">' + f("born", "Born", info.born) + f("died", "Died", info.died) + "</div>" +
    '<div class="bf-row">' + f("birthplace", "Birthplace", info.birthplace) +
      f("nationality", "Nationality", info.nationality) + "</div>" +
    f("mv", "Movements", (info.movements || []).join(", "), ' <span class="tiny">comma-separated</span>') +
    f("desc", "Note", info.description) +
    '<div class="bf-actions"><button type="submit" class="toolbtn">Save</button>' +
    '<button type="button" id="bf-cancel" class="linkbtn">cancel</button>' +
    '<span id="bio-msg" class="bio-msg"></span></div></form>';
  $("#bf-cancel").addEventListener("click", () => reloadBio(name));
  $("#bioform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      name: name,
      born: $("#bf-born").value, died: $("#bf-died").value,
      birthplace: $("#bf-birthplace").value, nationality: $("#bf-nationality").value,
      movements: $("#bf-mv").value, description: $("#bf-desc").value,
      wikidata_id: info.wikidata_id, wikipedia_url: info.wikipedia_url,
    };
    try {
      const r = await api("/api/artist_info/save", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      renderBio(name, r.info);
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
      const key = name.trim().toLowerCase();
      artists = (d.artists || []).filter((a) => a.name.trim().toLowerCase() !== key);
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
        const q = search.value.trim().toLowerCase();
        listEl.innerHTML = rowsHtml(q ? artists.filter((a) => a.name.toLowerCase().includes(q)) : artists);
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

const FACETS = [["era", "Era"], ["medium", "Medium"], ["style", "Style"]];

async function browseView(facet, value) {
  setNav("browse");
  if (!FACETS.some((f) => f[0] === facet)) facet = "era";
  try {
    const facets = await api("/api/facets");
    const tabs = FACETS.map((f) =>
      '<a href="#/browse/' + f[0] + '" class="' + (f[0] === facet ? "active" : "") + '">' + f[1] + "</a>"
    ).join("");
    const chips = (facets[facet] || []).map((v) =>
      '<a class="chip' + (value && v.value.toLowerCase() === value.toLowerCase() ? " active" : "") +
      '" href="#/browse/' + facet + "/" + encodeURIComponent(v.value) + '">' +
      esc(v.value) + ' <span class="n">' + v.count + "</span></a>"
    ).join("");
    let body = '<div class="emptybox">Pick a ' + facet + " above to see its works.</div>";
    app.innerHTML =
      '<div class="pagehead"><h1>Browse</h1></div>' +
      '<div class="facet-tabs">' + tabs + "</div>" +
      '<div class="chips">' + chips + "</div><div id='browse-body'>" + body + "</div>";
    if (value) {
      const d = await api("/api/works?" + facet + "=" + encodeURIComponent(value));
      const works = d.works;
      $("#browse-body").innerHTML = works.length
        ? '<div class="pagehead"><p class="sub">' + esc(value) + " · " +
          works.length + (works.length === 1 ? " work" : " works") + "</p></div>" +
          worksSection(works, true, browseCtx())
        : '<div class="emptybox">Nothing here yet.</div>';
      if (works.length) bindWorks(works, true, () => browseView(facet, value), browseCtx());
    }
  } catch (e) { errbox(e); }
}

/* ============================== collections ============================== */

function collectionCard(c) {
  const cover = c.cover
    ? '<img src="/thumb/' + c.cover + '" loading="lazy" alt="">'
    : '<span class="nocover">◫</span>';
  return (
    '<a class="artist-card collection-card" href="#/collection/' + encodeURIComponent(c.id) + '">' +
      '<div class="cover">' + cover + "</div>" +
      '<div class="meta"><span class="name">' + esc(c.title) + "</span>" +
      '<span class="sub">' + c.count + (c.count === 1 ? " work" : " works") +
      (c.owner_display ? " · " + esc(c.owner_display) : "") + "</span></div></a>"
  );
}

async function collectionsView() {
  setNav("collections");
  try {
    const d = await api("/api/collections");
    const cards = d.collections.map(collectionCard).join("");
    const newCard = canCurate()
      ? '<a class="artist-card add-card" id="newcol" href="#/collections">' +
        '<div class="cover"><span>+</span></div>' +
        '<div class="meta"><span class="name">New collection</span>' +
        '<span class="sub">curate your own</span></div></a>'
      : "";
    const count = d.collections.length;
    app.innerHTML =
      '<div class="pagehead"><h1>Collections</h1><p class="sub">' +
      count + (count === 1 ? " collection" : " collections") + "</p></div>" +
      (count || canCurate()
        ? '<div class="artist-grid">' + cards + newCard + "</div>"
        : '<div class="emptybox"><div class="big">No collections yet.</div>' +
          "Curators gather works into themed collections that everyone can browse.</div>");
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
      '<div class="pagehead"><a class="back" href="#/collections">← All collections</a>' +
      "<h1>" + esc(c.title) + "</h1>" +
      '<p class="sub">' + works.length + (works.length === 1 ? " work" : " works") +
      (c.owner_display ? " · curated by " + esc(c.owner_display) : "") + "</p>" +
      (c.description ? '<p class="col-desc">' + esc(c.description) + "</p>" : "") +
      ctl + "</div>";
    if (!works.length) {
      app.innerHTML = head + '<div class="emptybox">' +
        (editable
          ? "This collection is empty. Browse the museum, hit <b>Select</b>, then " +
            "<b>Add to collection</b> to gather works here."
          : "Nothing here yet.") + "</div>";
    } else {
      app.innerHTML = head + worksSection(works, true, collectionCtx(c));
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
    app.innerHTML =
      '<div class="pagehead"><h1>Add an artist</h1>' +
      '<p class="sub">Download every painting by an artist from a source into your library.</p></div>' +
      '<div class="addwrap">' +
      '<form class="dlform" id="dlform">' +
        "<label>Source</label><select id=\"f-source\">" + options + "</select>" +
        "<label>Artist</label><input id=\"f-query\" autocomplete=\"off\">" +
        '<div class="row2"><div><label>Max works <span style="text-transform:none">(optional)</span></label>' +
        '<input id="f-max" type="number" min="1" placeholder="all"></div>' +
        '<div id="f-px-wrap"><label>Max size, px <span style="text-transform:none">(optional)</span></label>' +
        '<input id="f-px" type="number" min="256" placeholder="native"></div></div>' +
        "<button id=\"f-go\">Start download</button>" +
        '<p class="hint" id="f-hint"></p><p class="warn" id="f-warn"></p>' +
        '<p class="formmsg" id="f-msg"></p>' +
      "</form>" +
      '<div><div class="pagehead" style="margin-bottom:14px"><p class="sub">Downloads</p></div>' +
      '<div id="jobs"></div></div></div>';

    const sel = $("#f-source"), hint = $("#f-hint"), warn = $("#f-warn"), q = $("#f-query");
    function syncSource() {
      const s = sources.find((x) => x.id === sel.value);
      hint.textContent = s.hint;
      warn.textContent = s.available ? "" : s.note;
      q.placeholder = s.placeholder;
      $("#f-px-wrap").style.display = s.supports_max_px ? "" : "none";
      $("#f-px").placeholder = s.max_px_default ? "default " + s.max_px_default : "native";
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
        await api("/api/downloads", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        msg.className = "formmsg ok";
        msg.textContent = "Queued. Progress appears on the right.";
        q.value = "";
        refreshJobs();
      } catch (err) {
        msg.className = "formmsg err";
        msg.textContent = err.message;
      } finally {
        $("#f-go").disabled = false;
      }
    });

    refreshJobs();
    pollTimer = setInterval(refreshJobs, 1500);
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
    return;
  }
  box.innerHTML = jobs.map((j) => {
    const counts = "matched " + j.found + " · saved " + j.saved +
      " · already had " + j.skipped + " · failed " + j.failed;
    const active = j.status === "running" || j.status === "queued";
    return (
      '<div class="job"><div class="head">' +
        '<span class="q">' + esc(j.query) + "</span>" +
        '<span class="src">' + esc(srcLabel(j.source)) + "</span>" +
        '<span class="badge ' + j.status + '">' + j.status + "</span></div>" +
        '<div class="counts">' + counts + (j.message ? " · " + esc(j.message) : "") + "</div>" +
        "<pre>" + esc((j.log || []).join("\n")) + "</pre>" +
        (active ? '<button class="cancel" data-id="' + j.id + '">Cancel</button>' : "") +
      "</div>"
    );
  }).join("");
  box.querySelectorAll("pre").forEach((p) => { p.scrollTop = p.scrollHeight; });
  box.querySelectorAll(".cancel").forEach((b) => {
    b.addEventListener("click", () =>
      api("/api/downloads/" + b.dataset.id + "/cancel", { method: "POST" }).then(refreshJobs));
  });
}

/* ============================== settings ============================== */

let fieldKeys = ["title", "artist", "date", "year", "medium", "style", "image", "id"];

async function settingsView() {
  setNav("settings");
  try {
    const [srcData, usersData] = await Promise.all([
      api("/api/custom_sources"),
      api("/api/users"),
    ]);
    if (srcData.field_keys) fieldKeys = srcData.field_keys;
    const presets = srcData.presets || [];
    app.innerHTML =
      '<div class="pagehead"><h1>Settings</h1></div>' +
      usersPanelHtml() +
      displayPanelHtml() +
      '<section class="settings-sources"><div class="pagehead" style="margin:32px 0 12px">' +
      '<h2 class="sec">Download sources</h2>' +
      '<p class="sub">Add JSON-API museum sources to scan for works. The built-in sources ' +
      "(Google Arts &amp; Culture, The Met, Art Institute of Chicago, Cleveland) are always available.</p></div>" +
      '<div class="setwrap">' +
      '<div id="srccol"><div class="pagehead" style="margin-bottom:12px"><p class="sub">Your custom sources</p></div>' +
      '<div id="srclist"></div></div>' +
      "<div>" + sourceFormHtml(presets) + "</div></div></section>";
    renderUsers(usersData.users);
    wireAddUser();
    const pc = document.getElementById("opt-placards");
    if (pc) { pc.checked = placardsOn(); pc.addEventListener("change", () => setPlacards(pc.checked)); }
    renderSourceList(srcData.sources || []);
    wireSourceForm(presets);
  } catch (e) { errbox(e); }
}

/* ---------- display options ---------- */

function displayPanelHtml() {
  return (
    '<section class="displaypanel"><div class="pagehead" style="margin-bottom:12px">' +
    '<h2 class="sec">Display</h2></div>' +
    '<label class="optrow"><input type="checkbox" id="opt-placards">' +
    "<span>Show placards in the viewer</span></label>" +
    '<p class="sub optnote">A museum-style label — piece name, artist, date and description — ' +
    "shown over each painting in fullscreen. Toggle any time with the <kbd>p</kbd> key while " +
    "viewing a work.</p></section>"
  );
}

/* ---------- users ---------- */

function usersPanelHtml() {
  return (
    '<section class="userspanel"><div class="pagehead" style="margin-bottom:12px">' +
    '<h2 class="sec">People</h2><p class="sub">Owners run the museum · Curators build collections · ' +
    "Visitors can only browse.</p></div>" +
    '<div class="usersgrid"><div id="userlist"></div>' +
    '<form class="dlform userform" id="adduser">' +
    '<div class="pagehead" style="margin-bottom:6px"><p class="sub">Add a person</p></div>' +
    "<label>Username</label><input id=\"nu-user\" autocomplete=\"off\">" +
    "<label>Password</label><input id=\"nu-pass\" type=\"password\" autocomplete=\"new-password\">" +
    "<label>Role</label><select id=\"nu-role\">" +
    '<option value="visitor">Visitor</option><option value="curator">Curator</option>' +
    '<option value="owner">Owner</option></select>' +
    "<button type=\"submit\">Add user</button>" +
    '<p class="formmsg" id="nu-msg"></p></form></div></section>'
  );
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
    return (
      '<div class="urow"><div class="umeta"><span class="uname">' + esc(u.username) +
      (self ? ' <span class="tiny">(you)</span>' : "") + "</span>" +
      '<span class="tiny">since ' + esc((u.created || "").split(" ")[0]) + "</span></div>" +
      '<div class="uact">' +
      '<select class="urole" data-user="' + esc(u.username) + '"' + (self ? " disabled" : "") + ">" +
      roleOptions(u.role) + "</select>" +
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
  return '<div class="vtitle">' + esc(w.title) + '</div><div class="vmeta">' + esc(bits) + src + "</div>";
}

function setFit(fit) {
  viewer.classList.toggle("fit", fit);
  viewer.classList.toggle("actual", !fit);
}

/* ---------- placards (museum wall labels shown in the viewer) ---------- */
function placardsOn() { return localStorage.getItem("placards") === "1"; }
function setPlacards(on) { localStorage.setItem("placards", on ? "1" : "0"); }

function placardHtml(w) {
  const date = w.date || (w.year ? String(w.year) : "");
  const desc = w.description
    ? '<div class="pl-desc">' + esc(w.description) + "</div>"
    : (isOwner() ? '<div class="pl-desc pl-empty">No description yet.</div>' : "");
  const edit = isOwner() ? '<button class="pl-edit" id="pl-edit" type="button">Edit</button>' : "";
  return '<div class="pl-card">' +
    '<div class="pl-artist">' + esc(w.artist || "Unknown artist") + "</div>" +
    '<div class="pl-title"><span class="pl-name">' + esc(w.title) + "</span>" +
    (date ? '<span class="pl-date">, ' + esc(date) + "</span>" : "") + "</div>" +
    (w.medium ? '<div class="pl-medium">' + esc(w.medium) + "</div>" : "") +
    desc + edit + "</div>";
}

function syncPlacard() {
  const on = placardsOn();
  viewer.classList.toggle("placards", on);
  const el = document.getElementById("placard");
  if (!el) return;
  if (on && viewer.classList.contains("open") && V.list[V.i]) {
    el.innerHTML = placardHtml(V.list[V.i]);
    el.hidden = false;
    const eb = document.getElementById("pl-edit");
    if (eb) eb.addEventListener("click", () => editWorkDialog(V.list[V.i]));
  } else {
    el.hidden = true;
  }
}

/* Owner-only: edit a work's placard details, saved to its sidecar.
   The research pane browses via the server's /research/page proxy — search
   engines can't be iframed directly and cross-origin selections are unreadable,
   so pages are re-served same-origin with a selection reporter injected
   (app/research.py). Selecting text on a page fills the armed form field. */
function editWorkDialog(w) {
  const m = modal(
    "<h2>Edit placard</h2>" +
    '<div class="ewwrap">' +
    '<form class="authform" id="ewform">' +
    "<label>Title<input id=\"ew-title\" autocomplete=\"off\"></label>" +
    "<label>Artist<input id=\"ew-artist\" autocomplete=\"off\"></label>" +
    "<label>Date <span class=\"tiny\">optional</span><input id=\"ew-date\" autocomplete=\"off\"></label>" +
    "<label>Medium <span class=\"tiny\">optional</span><input id=\"ew-medium\" autocomplete=\"off\"></label>" +
    "<label>Description<textarea id=\"ew-desc\" rows=\"8\"></textarea></label>" +
    '<div class="bf-actions"><button type="submit" class="cta-btn">Save</button>' +
    '<button type="button" class="linkbtn" id="ew-cancel">cancel</button>' +
    '<span class="formmsg err" id="ew-msg"></span></div></form>' +
    '<div class="ewresearch">' +
    '<div class="rs-bar"><input id="rs-q" autocomplete="off" placeholder="Search the web…">' +
    '<button type="button" class="toolbtn" id="rs-go">Search</button>' +
    '<button type="button" class="linkbtn" id="rs-wiki" title="Search Wikipedia instead">Wikipedia</button></div>' +
    '<div class="rs-targets" id="rs-targets"><span>Selections fill:</span></div>' +
    '<iframe id="rs-frame" title="Research"></iframe>' +
    '<p class="rs-tip tiny">Select text on the page and it lands in the armed field — ' +
    "Description appends, the others replace.</p>" +
    "</div></div>");
  m.el.querySelector(".modal").classList.add("modal-wide");
  const q = (id) => m.el.querySelector(id);

  /* ---- research pane ---- */
  const FIELDS = [["title", "#ew-title"], ["artist", "#ew-artist"], ["date", "#ew-date"],
                  ["medium", "#ew-medium"], ["description", "#ew-desc"]];
  let armed = "description";
  const targets = q("#rs-targets");
  FIELDS.forEach(([k]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "rs-pill" + (k === armed ? " armed" : "");
    b.textContent = k;
    b.addEventListener("click", () => {
      armed = k;
      targets.querySelectorAll(".rs-pill").forEach((p) => p.classList.toggle("armed", p === b));
    });
    targets.appendChild(b);
  });

  const frame = q("#rs-frame");
  const browseTo = (url) => { frame.src = "/research/page?url=" + encodeURIComponent(url); };
  const doSearch = (engine) => {
    const term = q("#rs-q").value.trim();
    if (term) browseTo(engine + encodeURIComponent(term));
  };
  const DDG = "https://html.duckduckgo.com/html/?q=";
  const WIKI = "https://en.wikipedia.org/w/index.php?search=";
  q("#rs-q").value = ('"' + (w.title || "") + '" ' + (w.artist || "")).trim();
  q("#rs-go").addEventListener("click", () => doSearch(DDG));
  q("#rs-wiki").addEventListener("click", () => doSearch(WIKI));
  q("#rs-q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); doSearch(DDG); }
  });
  doSearch(DDG);  // kick off the initial search for this work

  function onSelection(e) {
    if (e.origin !== location.origin || !e.data || e.data.type !== "placard-selection") return;
    if (!document.body.contains(m.el)) { window.removeEventListener("message", onSelection); return; }
    const t = (e.data.text || "").trim();
    if (!t) return;
    const el = q(FIELDS.find((f) => f[0] === armed)[1]);
    if (armed === "description" && el.value.trim()) el.value = el.value.replace(/\s+$/, "") + "\n\n" + t;
    else el.value = t;
    el.classList.add("justfilled");
    setTimeout(() => el.classList.remove("justfilled"), 700);
  }
  window.addEventListener("message", onSelection);
  const closeAll = () => { window.removeEventListener("message", onSelection); m.close(); };
  q("#ew-title").value = w.title || "";
  q("#ew-artist").value = w.artist || "";
  q("#ew-date").value = w.date || (w.year ? String(w.year) : "");
  q("#ew-medium").value = w.medium || "";
  q("#ew-desc").value = w.description || "";
  q("#ew-cancel").addEventListener("click", closeAll);
  q("#ewform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      title: q("#ew-title").value, artist: q("#ew-artist").value,
      date: q("#ew-date").value, medium: q("#ew-medium").value,
      description: q("#ew-desc").value,
    };
    try {
      const r = await api("/api/work/" + encodeURIComponent(w.id), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      closeAll();
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
  vimg.src = "/img/" + w.id;
  vcap.innerHTML = caption(w);
  syncPlacard();
  vcount.textContent = n > 1 ? (V.i + 1) + " / " + n : "";
  if (n > 1) {
    [V.i + 1, V.i - 1].forEach((k) => {
      const pw = V.list[((k % n) + n) % n];
      new Image().src = "/img/" + pw.id;
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

document.addEventListener("keydown", (e) => {
  if (!viewer.classList.contains("open")) return;
  if (document.querySelector(".modal-backdrop")) return;  // a chooser/dialog is up — it owns the keys
  if (e.key === "ArrowRight") showWork(V.i + 1);
  else if (e.key === "ArrowLeft") showWork(V.i - 1);
  else if (e.key === "Escape") closeViewer();
  else if (e.key === "c" || e.key === "C") collectHotkey();
  else if (e.key === "p" || e.key === "P") { setPlacards(!placardsOn()); syncPlacard(); }
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

/* click the painting: toggle between fit-to-screen and 1:1 pixels */
vimg.addEventListener("click", (e) => {
  if (dragMoved) { dragMoved = false; return; }
  if (viewer.classList.contains("fit")) {
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
    setFit(true);
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
