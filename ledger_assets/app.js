(function(){
"use strict";

/* ---------------------------------------------------------------------------
 * hermcp Ledger — one shell, five pages, one pop-up.
 *
 * Everything is rendered with createElement/textContent; there is no innerHTML
 * anywhere in this file, so a session title or a message body can never become
 * markup. The classification is NOT here either: the server sends buckets that
 * adapters/attention.py decided, and this file only draws them.
 *
 * The pure helpers at the top are exported as globalThis.__LEDGER_HELPERS__ so
 * tests/test_ledger_js.mjs can run them in node without a DOM.
 * ------------------------------------------------------------------------- */

var DISMISS_KEY = "hermcp.ledger.dismissed.v1";

var NAV = [
  {key: "attention", path: "/ledger", label: "~/attention", icon: "warn"},
  {key: "sessions", path: "/ledger/sessions", label: "~/sessions", icon: "chat"},
  {key: "runs", path: "/ledger/runs", label: "~/runs", icon: "play"},
  {key: "cron", path: "/ledger/cron", label: "~/cron", icon: "clock"},
  {key: "gateway", path: "/ledger/gateway", label: "~/gateway", icon: "server"}
];

var ICONS = {
  warn: "M7 2.2 1.6 11.4h10.8L7 2.2Z",
  chat: "M2 3.2h10v6.2H6.4L3.4 12V9.4H2V3.2Z",
  play: "M4 2.4 11.4 7 4 11.6V2.4Z",
  clock: "M7 1.8a5.2 5.2 0 1 0 0 10.4A5.2 5.2 0 0 0 7 1.8Zm0 2.4V7l2.6 1.6",
  server: "M2 2.6h10v3.4H2V2.6Zm0 5.4h10v3.4H2V8Zm1.6-4h.01M3.6 9.6h.01"
};

function idOf(run){
  if(!run) return "";
  return String(run.run_id || run.session_id || "");
}

// A storage we can actually use, or null. Private mode / disabled storage throws
// on the first write in some browsers, so probe instead of assuming.
function store(win){
  try{
    var ls = win.localStorage;
    var probe = "__hermcp_ledger_probe__";
    ls.setItem(probe, "1");
    ls.removeItem(probe);
    return ls;
  }catch(e){ return null; }
}

function loadDismissed(storage){
  if(!storage) return {};
  try{
    var raw = storage.getItem(DISMISS_KEY);
    if(!raw) return {};
    var parsed = JSON.parse(raw);
    if(!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed;
  }catch(e){ return {}; }
}

function saveDismissed(storage, ids){
  if(!storage) return false;
  try{ storage.setItem(DISMISS_KEY, JSON.stringify(ids)); return true; }
  catch(e){ return false; }
}

// Only keep ids that still exist in the current window — no unbounded growth.
function pruneDismissed(ids, alive){
  var out = {}, keep = {}, i;
  for(i = 0; i < (alive || []).length; i++) keep[String(alive[i])] = true;
  for(var k in ids){
    if(Object.prototype.hasOwnProperty.call(ids, k) && keep[k]) out[k] = ids[k];
  }
  return out;
}

function splitFailures(list, dismissed){
  var active = [], hidden = [];
  for(var i = 0; i < (list || []).length; i++){
    var row = list[i];
    (dismissed[idOf(row)] ? hidden : active).push(row);
  }
  return {active: active, hidden: hidden};
}

// What the badges count. The two numbers the header shows come from the server's
// digest (`attention.digest`), i.e. the same source hermes_status reports — the
// browser only subtracts what THIS browser dismissed, it never re-adds a row the
// server already settled.
function attentionCount(att, dismissed){
  att = att || {};
  var dg = att.digest || {};
  var cron = splitFailures(att.failed_cron_runs, dismissed).active.length;
  var chat = splitFailures(att.failed_chat_runs, dismissed).active.length;
  var approvals = (dg.approvals_waiting !== undefined && dg.approvals_waiting !== null)
    ? dg.approvals_waiting : ((att.approvals && att.approvals.count) || 0);
  var failed = (dg.failed_runs_24h !== undefined && dg.failed_runs_24h !== null)
    ? dg.failed_runs_24h
    : splitFailures(att.failed_cron_runs, {}).active.length + splitFailures(att.failed_chat_runs, {}).active.length;
  return {approvals: approvals, cron: cron, chat: chat, failed: failed,
          total: approvals + cron + chat};
}

// The label of the "rest" control for one server-side list window. The count comes
// from the server (`more_count`), never from a row count in the browser.
function moreLabel(meta){
  if(!meta || !meta.has_more || meta.expanded) return "";
  var n = meta.more_count;
  return "Mehr anzeigen" + (typeof n === "number" && n > 0 ? " (" + n + " weitere)" : "");
}

// -- routing / urls ---------------------------------------------------------

function pageOf(path){
  var p = String(path || "").replace(/\/+$/, "");
  if(/^\/ledger\/session\/[^/]+$/.test(p)) return {page: "session", id: decodeURIComponent(p.split("/").pop())};
  for(var i = 0; i < NAV.length; i++){
    if(NAV[i].path === p) return {page: NAV[i].key, id: ""};
  }
  return {page: "attention", id: ""};
}

function sessionPath(id){
  if(!id) return "";
  return "/ledger/session/" + encodeURIComponent(String(id));
}

// Query string for the pop-up endpoint. Only whitelisted params, always encoded.
function peekUrl(kind, id, opts){
  opts = opts || {};
  var q = ["kind=" + encodeURIComponent(kind || "session"), "id=" + encodeURIComponent(String(id || ""))];
  if(opts.profile) q.push("profile=" + encodeURIComponent(opts.profile));
  if(opts.job) q.push("job=" + encodeURIComponent(opts.job));
  return "/ledger/api/peek?" + q.join("&");
}

// Query string for a sub-page list. Empty values are dropped, keys are fixed.
var LIST_KEYS = ["status", "profile", "surface", "state", "job", "hours", "q", "offset", "limit"];
function listUrl(kind, filters){
  var q = ["kind=" + encodeURIComponent(kind)];
  filters = filters || {};
  for(var i = 0; i < LIST_KEYS.length; i++){
    var k = LIST_KEYS[i], v = filters[k];
    if(v === undefined || v === null || v === "") continue;
    q.push(k + "=" + encodeURIComponent(String(v)));
  }
  return "/ledger/api/list?" + q.join("&");
}

// -- formatting -------------------------------------------------------------

function pad(n){ return n < 10 ? "0" + n : String(n); }
function clock(iso){
  if(!iso) return "—";
  var d = new Date(iso);
  if(isNaN(+d)) return "—";
  return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}
function dayClock(iso){
  if(!iso) return "—";
  var d = new Date(iso);
  if(isNaN(+d)) return "—";
  var t = new Date();
  var sameDay = d.toDateString() === t.toDateString();
  return (sameDay ? "" : pad(d.getDate()) + "." + pad(d.getMonth() + 1) + ". ") + clock(iso);
}
function ago(iso, now){
  if(!iso) return "";
  var d = new Date(iso);
  if(isNaN(+d)) return "";
  var s = Math.max(0, Math.round(((now || Date.now()) - d) / 1000));
  if(s < 60) return "vor " + s + " s";
  if(s < 3600) return "vor " + Math.round(s / 60) + " min";
  if(s < 86400) return "vor " + Math.round(s / 3600) + " h";
  return "vor " + Math.round(s / 86400) + " d";
}
function dur(v){
  if(v === null || v === undefined) return "—";
  if(v < 1) return Math.round(v * 1000) + " ms";
  if(v < 60) return v.toFixed(1).replace(".", ",") + " s";
  if(v < 3600) return Math.floor(v / 60) + " min " + Math.round(v % 60) + " s";
  return Math.floor(v / 3600) + " h " + Math.round((v % 3600) / 60) + " min";
}
function tone(v){
  var s = String(v || "").toLowerCase();
  if(s === "aktiv" || s === "completed" || s === "ok" || s === "connected" || s === "running" ||
     s === "approved" || s === "applied") return "ok";
  if(s === "claimed" || s === "started" || s === "queued" || s === "waiting" ||
     s === "waiting_for_approval" || s === "offen") return "warn";
  if(s === "error" || s === "failed" || s === "lost" || s === "denied" || s === "disconnected") return "bad";
  return "muted";
}
var SYM = {ok: "●", bad: "✕", run: "◌", warn: "▲", muted: "○"};
function symbolFor(t){ return SYM[t] || "·"; }

function truncate(text, n){
  var s = String(text === undefined || text === null ? "" : text);
  if(!n || s.length <= n) return s;
  return s.slice(0, n) + "…";
}

function countLabel(n, one, many){
  return n + " " + (n === 1 ? one : many);
}

var HELPERS = {
  DISMISS_KEY: DISMISS_KEY, idOf: idOf, store: store, loadDismissed: loadDismissed,
  saveDismissed: saveDismissed, pruneDismissed: pruneDismissed, splitFailures: splitFailures,
  attentionCount: attentionCount, moreLabel: moreLabel, NAV: NAV, pageOf: pageOf,
  sessionPath: sessionPath, peekUrl: peekUrl, listUrl: listUrl, LIST_KEYS: LIST_KEYS,
  clock: clock, dayClock: dayClock, ago: ago, dur: dur, tone: tone, symbolFor: symbolFor,
  truncate: truncate, countLabel: countLabel
};
if(typeof globalThis !== "undefined") globalThis.__LEDGER_HELPERS__ = HELPERS;
if(typeof document === "undefined") return;          // node: helpers only, no rendering

/* ---------------------------------------------------------------------------
 * the page
 * ------------------------------------------------------------------------- */

var POLL = __POLL__;
var META = (typeof LEDGER_META === "object" && LEDGER_META) ? LEDGER_META : {};
var root = document.getElementById("root");
var navEl = document.getElementById("nav");
var footEl = document.getElementById("foot");
var storage = store(window);
var dismissed = loadDismissed(storage);
var showHidden = false;
// Which growing lists the reader opened ("Mehr anzeigen"). Sent to the server as ?more=…,
// so the rest is fetched there instead of being hidden in the browser.
var expanded = {};
var lastPayload = null;
var jobsOpen = false;
var jobs = null;
var route = pageOf(META.page ? "/ledger" + (META.page === "attention" ? "" : "/" + META.page) : location.pathname);
if(META.page === "session" && META.session_id) route = {page: "session", id: META.session_id};
var listState = {offset: 0, filters: {}};
var dialogState = {open: false, trigger: null};

// -- tiny DOM helpers -------------------------------------------------------

function el(tag, cls, text){
  var n = document.createElement(tag);
  if(cls) n.className = cls;
  if(text !== undefined && text !== null) n.textContent = String(text);
  return n;
}
function button(cls, label, onClick){
  var b = el("button", cls, label);
  b.type = "button";
  b.onclick = onClick;
  return b;
}
function icon(name){
  var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 14 14");
  svg.setAttribute("aria-hidden", "true");
  var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] || ICONS.warn);
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.appendChild(path);
  return svg;
}
function chip(text, cls, title){
  var c = el("span", "chip" + (cls ? " " + cls : ""), text);
  if(title) c.title = title;
  return c;
}
function symNode(t){ return el("span", "sym " + (t || "muted"), symbolFor(t)); }
function link(url, label, cls){
  var a = el("a", cls || null, label || "öffnen");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}
function extLink(url, label){
  var a = link(url, label + " ↗");
  return a;
}

// A whole row as one button: keyboard reachable, no inner markup from data.
function rowButton(columns, widths, onClick, title){
  var b = el("button", "lrow");
  b.type = "button";
  b.style.gridTemplateColumns = widths;
  if(title) b.title = title;
  columns.forEach(function(c){
    if(c && c.nodeType){ var w = el("span"); w.appendChild(c); b.appendChild(w); }
    else b.appendChild(el("span", null, c === undefined || c === null ? "—" : c));
  });
  b.onclick = onClick;
  return b;
}

// -- nav + footer -----------------------------------------------------------

function renderNav(){
  navEl.textContent = "";
  var brand = el("span", "brand");
  var b = el("b", null, "hermcp");
  brand.appendChild(b);
  brand.appendChild(document.createTextNode(" ledger"));
  var caret = el("span", "caret", "▌");
  brand.appendChild(caret);
  navEl.appendChild(brand);

  var links = el("div", "nav-links");
  NAV.forEach(function(item){
    var a = el("a", "nav-link" + (route.page === item.key || (route.page === "session" && item.key === "sessions") ? " active" : ""));
    a.href = item.path;
    a.appendChild(icon(item.icon));
    a.appendChild(document.createTextNode(item.label));
    if(route.page === item.key) a.setAttribute("aria-current", "page");
    links.appendChild(a);
  });
  navEl.appendChild(links);

  var right = el("div", "nav-right");
  if(META.view_key){
    var v = chip("view-as " + (META.view_name || META.view_key), "green", "HERMCP_LEDGER_VIEW_KEY=" + META.view_key);
    right.appendChild(v);
  } else {
    right.appendChild(chip("view-as page", "green", "Kein View-Key gesetzt: Ansicht des read-only Seiten-Principals"));
  }
  if(META.consumer) right.appendChild(chip("· " + META.consumer));
  right.appendChild(chip("READ-ONLY"));
  right.appendChild(chip("LAN"));
  navEl.appendChild(right);
}

function renderFoot(extra){
  footEl.textContent = "";
  footEl.appendChild(el("span", null, "hermcp ledger · read-only"));
  var settings = (META.settings || []);
  settings.forEach(function(s){
    var kv = el("span", "kv");
    kv.appendChild(document.createTextNode(s.name + " "));
    kv.appendChild(el("b", null, String(s.value)));
    footEl.appendChild(kv);
  });
  if(extra) footEl.appendChild(el("span", "kv", extra));
}

// -- shared bits ------------------------------------------------------------

function panel(number, title, cmd, opts){
  opts = opts || {};
  var p = el("section", "panel" + (opts.accent ? " accent" : ""));
  if(opts.id) p.id = opts.id;
  var head = el("div", "panel-head");
  head.appendChild(el("span", "panel-no", number));
  head.appendChild(el("span", "panel-title", "// " + title));
  if(cmd){
    var c = el("span", "panel-cmd");
    c.appendChild(el("span", "p", "$ "));
    c.appendChild(document.createTextNode(cmd));
    head.appendChild(c);
  }
  p.appendChild(head);
  var body = el("div", "panel-body" + (opts.flush ? " flush" : ""));
  p.appendChild(body);
  return {panel: p, body: body, head: head};
}

function errorBox(errors){
  var keys = Object.keys(errors || {});
  if(!keys.length) return null;
  var box = el("div", "notice");
  box.appendChild(el("strong", null, "Teilweise nicht lesbar: "));
  box.appendChild(document.createTextNode(keys.map(function(k){ return k + ": " + errors[k]; }).join(" · ")));
  return box;
}

function moreButton(meta, name){
  var label = moreLabel(meta);
  if(!label) return null;
  return button("btn dim", label, function(){
    expanded[name] = true;
    load();
  });
}

function withMore(node, meta, name){
  var b = moreButton(meta, name);
  if(b){ var wrap = el("div", "panel-foot"); wrap.appendChild(b); node.appendChild(wrap); }
  return node;
}

// -- pop-up ----------------------------------------------------------------

function openPeek(kind, id, opts, trigger){
  opts = opts || {};
  var dlg = document.getElementById("peek");
  if(!dlg){
    dlg = el("dialog", "peek");
    dlg.id = "peek";
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    dlg.setAttribute("aria-labelledby", "peek-title");
    document.body.appendChild(dlg);
    dlg.addEventListener("click", function(ev){
      if(ev.target === dlg) closePeek();          // backdrop click
    });
    dlg.addEventListener("cancel", function(ev){ ev.preventDefault(); closePeek(); });
    dlg.addEventListener("keydown", trapFocus);
  }
  dialogState = {open: true, trigger: trigger || null, kind: kind, id: id};
  dlg.textContent = "";
  var head = el("div", "peek-head");
  head.appendChild(symNode("muted"));
  var title = el("h2", null, "lade …");
  title.id = "peek-title";
  head.appendChild(title);
  var close = button("peek-close", "✕", closePeek);
  close.setAttribute("aria-label", "Dialog schließen");
  head.appendChild(close);
  dlg.appendChild(head);
  var body = el("div", "peek-body");
  body.appendChild(el("div", "empty", "lade …"));
  dlg.appendChild(body);
  var foot = el("div", "peek-foot");
  dlg.appendChild(foot);

  if(typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "open");

  getJSON(peekUrl(kind, id, opts)).then(function(d){
    renderPeek(dlg, d);
  }).catch(function(e){
    renderPeekError(dlg, "Der Eintrag konnte nicht gelesen werden (" + String(e) + "). " +
      "Möglicherweise wurde die Session gelöscht.");
  });
}

function closePeek(){
  var dlg = document.getElementById("peek");
  if(!dlg) return;
  if(typeof dlg.close === "function" && dlg.open) dlg.close();
  else dlg.removeAttribute("open");
  var trigger = dialogState.trigger;
  dialogState = {open: false, trigger: null};
  if(trigger && typeof trigger.focus === "function") trigger.focus();
}

function trapFocus(ev){
  if(ev.key !== "Tab") return;
  var dlg = ev.currentTarget;
  var nodes = dlg.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
  if(!nodes.length) return;
  var first = nodes[0], last = nodes[nodes.length - 1];
  if(ev.shiftKey && document.activeElement === first){ ev.preventDefault(); last.focus(); }
  else if(!ev.shiftKey && document.activeElement === last){ ev.preventDefault(); first.focus(); }
}

function renderPeekError(dlg, message){
  var body = dlg.querySelector(".peek-body");
  if(!body) return;
  body.textContent = "";
  var box = el("div", "notice bad", message);
  body.appendChild(box);
  var close = dlg.querySelector(".peek-close");
  if(close) close.focus();
}

function renderPeek(dlg, d){
  d = d || {};
  var head = dlg.querySelector(".peek-head");
  var body = dlg.querySelector(".peek-body");
  var foot = dlg.querySelector(".peek-foot");
  if(!head || !body || !foot) return;
  head.textContent = "";
  head.appendChild(symNode(d.tone || "muted"));
  var title = el("h2", null, d.title || d.id || "Eintrag");
  title.id = "peek-title";
  head.appendChild(title);
  head.appendChild(chip(d.type_label || "EINTRAG", d.tone === "bad" ? "red" : d.tone === "warn" ? "amber" : "cyan"));
  var close = button("peek-close", "✕", closePeek);
  close.setAttribute("aria-label", "Dialog schließen");
  head.appendChild(close);

  body.textContent = "";
  if(d.ok === false){
    body.appendChild(el("div", "notice bad", d.error || "Eintrag nicht lesbar."));
    close.focus();
    return;
  }

  var facts = d.facts || {};
  var grid = el("div", "peek-facts");
  [["Modell", facts.model], ["Profil", facts.profile], ["Oberfläche", facts.surface],
   ["Letzte Aktivität", facts.last_active ? dayClock(facts.last_active) : null]].forEach(function(pair){
    var f = el("div", "f");
    f.appendChild(el("div", "k", pair[0]));
    f.appendChild(el("div", "v", pair[1] || "—"));
    grid.appendChild(f);
  });
  body.appendChild(grid);

  var actions = d.actions || [];
  var wrap = el("div", "peek-actions");
  var label = el("div", "legend", "Letzte Aktionen");
  wrap.appendChild(label);
  if(!actions.length) wrap.appendChild(el("div", "empty", "Keine Aktionen im Verlauf."));
  actions.forEach(function(a){
    var line = el("div", "row-line");
    line.appendChild(el("span", "sym " + (a.role === "user" ? "ok" : a.role === "assistant" ? "run" : "muted"),
      a.role === "user" ? "›" : a.role === "assistant" ? "‹" : "⚙"));
    line.appendChild(el("span", "who", a.tool ? a.tool : (a.role || "?")));
    line.appendChild(el("span", "what", a.text || "—"));
    line.appendChild(el("span", "when", clock(a.at)));
    wrap.appendChild(line);
  });
  if(d.live){
    var live = el("div", "live");
    live.appendChild(el("span", "blink", "●"));
    live.appendChild(document.createTextNode("Session läuft gerade"));
    wrap.appendChild(live);
  }
  body.appendChild(wrap);

  foot.textContent = "";
  var links = d.links || {};
  if(links.session){
    var full = el("a", "btn", "⤢ Vollansicht der Session");
    full.href = links.session;
    foot.appendChild(full);
  }
  if(links.desktop) foot.appendChild(extLink(links.desktop, "In Desktop-App"));
  if(links.web) foot.appendChild(extLink(links.web, "In Web-App"));
  foot.appendChild(el("span", "peek-note", "read-only"));
  close.focus();
}

// -- page: attention (start) ------------------------------------------------

function kpi(label, value, cls, sub){
  var n = el("div", "kpi" + (cls ? " " + cls : ""));
  n.appendChild(el("span", "label", label));
  var v = el("div", "value", value);
  if(sub){ v.appendChild(el("small", null, sub)); }
  n.appendChild(v);
  return n;
}

function attentionRow(kind, row, onClick){
  var t = row.tone || tone(row.state || row.status);
  var line = el("div", "row-line");
  line.appendChild(symNode(t));
  var b = el("button", "linkish", "");
  b.style.cssText = "flex:1;min-width:0;text-align:left;border:0;background:none;color:inherit;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
  b.textContent = row.label || row.summary || row.action || row.job_name || row.id || "—";
  b.onclick = onClick;
  line.appendChild(b);
  line.appendChild(el("span", "when", dayClock(row.at || row.started_at || row.created_at)));
  return line;
}

function renderAttention(d){
  lastPayload = d;
  var alive = [], att0 = d.attention || {};
  (att0.failed_cron_runs || []).forEach(function(r){ alive.push(idOf(r)); });
  (att0.failed_chat_runs || []).forEach(function(r){ alive.push(idOf(r)); });
  var pruned = pruneDismissed(dismissed, alive);
  if(Object.keys(pruned).length !== Object.keys(dismissed).length){ dismissed = pruned; persist(); }

  root.textContent = "";
  var gw = d.gateway || {}, att = d.attention || {}, sec = d.sections || {},
      sessions = (sec.sessions && sec.sessions.items) || [], cron = sec.cron || {},
      runs = cron.runs || [], ap = sec.approvals || {count: 0, items: []}, links = d.links || {};

  var counts = attentionCount(att, dismissed);
  var cronSplit = splitFailures(att.failed_cron_runs, dismissed);
  var chatSplit = splitFailures(att.failed_chat_runs, dismissed);
  var waiting = att.waiting || ((att.approvals && att.approvals.items) || []);
  var interrupted = att.interrupted || [];
  var cronMeta = cron.list || {}, sessionMeta = (sec.sessions && sec.sessions.list) || {};
  var failedCronMeta = att.failed_cron_list || {}, failedChatMeta = att.failed_chat_list || {},
      interruptedMeta = att.interrupted_list || {};

  // title line
  var head = el("div", "page-head");
  var h1 = el("h1", "page-title", "Ledger");
  h1.appendChild(el("span", "dot", "."));
  head.appendChild(h1);
  var verdict = String(gw.verdict || gw.health || "").toLowerCase();
  var vcls = verdict === "ok" ? "green" : verdict === "degraded" ? "amber" : verdict ? "red" : "";
  var vchip = chip((gw.state || "unbekannt") + " · " + (gw.health || gw.verdict || "?"), vcls);
  vchip.insertBefore(el("span", "sym " + (verdict === "ok" ? "ok" : verdict === "degraded" ? "warn" : verdict ? "bad" : "muted"),
    verdict === "ok" ? "●" : verdict === "degraded" ? "▲" : verdict ? "✕" : "○"), vchip.firstChild);
  head.appendChild(vchip);
  head.appendChild(el("span", "spacer"));
  head.appendChild(el("span", "sub", "aktualisiert " + clock(d.generated_at) + " · Polling alle " +
    (d.poll_seconds || POLL) + " s"));
  root.appendChild(head);

  var err = errorBox(d.errors);
  if(err) root.appendChild(err);
  if(!storage){
    var hint = el("div", "notice");
    hint.appendChild(el("strong", null, "Browser-Speicher nicht verfügbar: "));
    hint.appendChild(document.createTextNode("Ausblenden gilt nur für diese Ansicht und ist nach dem Neuladen weg."));
    root.appendChild(hint);
  }

  // KPI strip: five cells, exactly the counters hermes_status reports
  var platforms = gw.platforms || [];
  var badPlatforms = platforms.filter(function(x){ return x.stale || x.needs_attention || String(x.state) !== "connected"; });
  var kpis = el("div", "kpis");
  kpis.id = "panel-kpis";
  kpis.appendChild(kpi("braucht Aufmerksamkeit", counts.total, counts.total ? "bad" : "ok"));
  kpis.appendChild(kpi("Freigaben wartend", counts.approvals, counts.approvals ? "warn" : ""));
  kpis.appendChild(kpi("Fehler 24 h", counts.failed, counts.failed ? "bad" : ""));
  kpis.appendChild(kpi("Plattformen verbunden", (platforms.length - badPlatforms.length) + "/" + platforms.length,
    badPlatforms.length ? "warn" : "ok"));
  kpis.appendChild(kpi("Agenten aktiv", gw.active_agents === null || gw.active_agents === undefined ? "?" : gw.active_agents,
    "", att.active_sessions ? att.active_sessions + " Sessions" : ""));
  root.appendChild(kpis);

  // top row: attention (2/3) + gateway (1/3)
  var top = el("div", "grid-top");

  var a = panel("01", "braucht Aufmerksamkeit", "inbox_attention", {accent: true, id: "panel-attention"});
  var aBody = a.body;
  var hiddenAny = cronSplit.hidden.length + chatSplit.hidden.length;
  if(counts.total || hiddenAny || cronSplit.active.length || chatSplit.active.length){
    if(counts.cron + counts.chat){
      var bar = el("div", "panel-foot");
      bar.style.borderTop = "0";
      bar.appendChild(button("btn dim", "Clear errors", function(){
        cronSplit.active.concat(chatSplit.active).forEach(function(r){ dismissed[idOf(r)] = 1; });
        persist(); render(lastPayload);
      }));
      bar.appendChild(el("span", "legend", "nur in diesem Browser"));
      aBody.appendChild(bar);
    }
    if(waiting.length){
      aBody.appendChild(el("div", "legend", "Offene Freigaben (" + waiting.length + ")"));
      waiting.forEach(function(x){
        var line = el("div", "row-line");
        line.appendChild(symNode("warn"));
        line.appendChild(el("span", "who", x.profile || "?"));
        var b = el("button", "linkish", x.command || "Anfrage ohne Befehlsangabe");
        b.style.cssText = "flex:1;min-width:0;text-align:left;border:0;background:none;color:inherit;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
        b.onclick = function(ev){
          openPeek("approval", x.approval_key, {profile: x.profile}, ev.currentTarget);
        };
        line.appendChild(b);
        line.appendChild(el("span", "when", x.created_at ? "seit " + dayClock(x.created_at) : ""));
        aBody.appendChild(line);
      });
    }
    if(cronSplit.active.length){
      aBody.appendChild(el("div", "legend", "Fehlgeschlagene Cron-Läufe, 24 h (" +
        (failedCronMeta.total !== undefined ? failedCronMeta.total : cronSplit.active.length) + ")"));
      cronSplit.active.forEach(function(r){
        var line = attentionRow("cron", {tone: "bad", label: (r.job_name || r.job_id || "?") + " · " + (r.error || "Fehler"),
          at: r.started_at}, function(ev){ openPeek("cron", r.run_id, {profile: r.profile, job: r.job_id}, ev.currentTarget); });
        aBody.appendChild(line);
      });
    }
    if(chatSplit.active.length){
      aBody.appendChild(el("div", "legend", "Fehlgeschlagene Agent-Läufe, 24 h (" +
        (failedChatMeta.total !== undefined ? failedChatMeta.total : chatSplit.active.length) + ")"));
      chatSplit.active.forEach(function(r){
        var line = attentionRow("run", {tone: "bad", label: (r.profile || "?") + " · " + (r.summary || "ohne Meldung"),
          at: r.at}, function(ev){ openPeek("run", r.run_id, {profile: r.profile}, ev.currentTarget); });
        aBody.appendChild(line);
      });
      var mb = moreButton(failedChatMeta, "runs");
      if(mb){ var mw = el("div", "panel-foot"); mw.appendChild(mb); aBody.appendChild(mw); }
    }
    if(hiddenAny){
      var hl = el("div", "panel-foot");
      hl.appendChild(el("span", null, hiddenAny + " ausgeblendet "));
      hl.appendChild(button("btn dim", showHidden ? "wieder einblenden" : "anzeigen", function(){
        showHidden = !showHidden; render(lastPayload);
      }));
      hl.appendChild(button("btn dim", "Zurücksetzen", function(){ dismissed = {}; persist(); render(lastPayload); }));
      aBody.appendChild(hl);
    }
    if(showHidden && hiddenAny){
      cronSplit.hidden.forEach(function(r){
        aBody.appendChild(attentionRow("cron", {tone: "muted", label: (r.job_name || r.job_id) + " · " + (r.error || "Fehler"),
          at: r.started_at}, function(ev){ openPeek("cron", r.run_id, {profile: r.profile, job: r.job_id}, ev.currentTarget); }));
      });
      chatSplit.hidden.forEach(function(r){
        aBody.appendChild(attentionRow("run", {tone: "muted", label: (r.profile || "?") + " · " + (r.summary || "ohne Meldung"),
          at: r.at}, function(ev){ openPeek("run", r.run_id, {profile: r.profile}, ev.currentTarget); }));
      });
    }
    if(ap.hidden) aBody.appendChild(el("div", "legend", ap.hidden + " weitere Freigaben in anderen Profilen"));
    if(att.note) aBody.appendChild(el("div", "legend", att.note));
  } else {
    aBody.appendChild(el("div", "empty", "Nichts braucht gerade Aufmerksamkeit."));
  }
  if(interrupted.length){
    aBody.appendChild(el("div", "legend", "Durch Neustart unterbrochen (" +
      (att.interrupted_count !== undefined ? att.interrupted_count : interrupted.length) + ")"));
    interrupted.forEach(function(r){
      aBody.appendChild(attentionRow("run", {tone: "warn", label: (r.profile || "?") + " · " +
        (r.summary || "durch einen Neustart unterbrochen"), at: r.at},
        function(ev){ openPeek("run", r.run_id, {profile: r.profile}, ev.currentTarget); }));
    });
    var ib = moreButton(interruptedMeta, "runs");
    if(ib){ var iw = el("div", "panel-foot"); iw.appendChild(ib); aBody.appendChild(iw); }
  }
  top.appendChild(a.panel);

  var g = panel("02", "gateway", "status_gateway", {id: "panel-gateway"});
  var gBody = g.body;
  var gwrap = el("div", "chips");
  gwrap.style.display = "flex";
  gwrap.style.flexWrap = "wrap";
  gwrap.style.gap = "6px";
  function gchip(t, text, title){
    var c = chip(text, t === "ok" ? "green" : t === "bad" ? "red" : t === "warn" ? "amber" : "", title);
    c.insertBefore(el("span", "sym " + (t || "muted"), symbolFor(t || "muted")), c.firstChild);
    return c;
  }
  gwrap.appendChild(gchip("ok", "gateway · " + (gw.state || "?") + " · pid " + (gw.pid || "?")));
  gwrap.appendChild(gchip("", "Agenten " + (gw.active_agents === null || gw.active_agents === undefined ? "?" : gw.active_agents)));
  gwrap.appendChild(gchip("", "Profile " + (d.profiles || []).length));
  platforms.forEach(function(x){
    var t = x.stale ? "bad" : x.needs_attention ? "warn" : tone(x.state) === "ok" ? "ok" : tone(x.state) === "bad" ? "bad" : "";
    gwrap.appendChild(gchip(t, x.name + " · " + (x.state || "?"), x.error || x.updated_at || ""));
  });
  (gw.stale_platforms || []).forEach(function(x){ gwrap.appendChild(gchip("muted", x.name + " · verwaist", x.reason || "")); });
  gBody.appendChild(gwrap);
  gBody.appendChild(el("div", "legend", gw.code_version ? "Code v" + gw.code_version : ""));
  top.appendChild(g.panel);
  root.appendChild(top);

  // bottom row: sessions (03), runs (04), cron (05)
  var bottom = el("div", "grid-bottom");

  var s = panel("03", "sessions", "sessions_list", {id: "panel-sessions"});
  var sBody = s.body;
  if(sessions.length){
    var head3 = el("div", "lhead");
    head3.style.gridTemplateColumns = "1fr 92px 84px 74px";
    ["Profil / Session", "Plattform", "Status", "zuletzt"].forEach(function(t){ head3.appendChild(el("span", null, t)); });
    sBody.appendChild(head3);
    sessions.forEach(function(x){
      var label = (x.profile || "?") + " · " + (x.session_id || "—");
      var b = rowButton([
        label,
        x.source || "—",
        "",
        ago(x.last_active) || "—"
      ], "1fr 92px 84px 74px", function(ev){
        openPeek("session", x.session_id, {profile: x.profile}, ev.currentTarget);
      }, x.session_id || "");
      var st = b.children[2];
      st.textContent = "";
      st.appendChild(el("span", "sym " + tone(x.status), symbolFor(tone(x.status))));
      st.appendChild(document.createTextNode(" " + (x.status || "—")));
      sBody.appendChild(b);
    });
    var smb = moreButton(sessionMeta, "sessions");
    if(smb){ var smw = el("div", "panel-foot"); smw.appendChild(smb); sBody.appendChild(smw); }
  } else sBody.appendChild(el("div", "empty", "Keine Sessions gelesen."));
  bottom.appendChild(s.panel);

  var r = panel("04", "runs", "inbox_list", {id: "panel-runs"});
  var rBody = r.body;
  var runRows = [];
  cronSplit.active.forEach(function(x){ runRows.push({kind: "cron", row: x, tone: "bad",
    label: (x.job_name || x.job_id || "?") + " · " + (x.error || "Fehler"), at: x.started_at, id: x.run_id, profile: x.profile, job: x.job_id}); });
  chatSplit.active.forEach(function(x){ runRows.push({kind: "run", row: x, tone: "bad",
    label: (x.profile || "?") + " · " + (x.summary || "ohne Meldung"), at: x.at, id: x.run_id, profile: x.profile}); });
  interrupted.forEach(function(x){ runRows.push({kind: "run", row: x, tone: "warn",
    label: (x.profile || "?") + " · unterbrochen", at: x.at, id: x.run_id, profile: x.profile}); });
  runRows.sort(function(a1, b1){ return String(b1.at || "").localeCompare(String(a1.at || "")); });
  if(runRows.length){
    var head4 = el("div", "lhead");
    head4.style.gridTemplateColumns = "1fr 88px 74px";
    ["Lauf", "Art", "Start"].forEach(function(t){ head4.appendChild(el("span", null, t)); });
    rBody.appendChild(head4);
    runRows.forEach(function(item){
      var b = rowButton([item.label, item.kind === "cron" ? "cron" : "run", dayClock(item.at)],
        "1fr 88px 74px", function(ev){
          openPeek(item.kind, item.id, {profile: item.profile, job: item.job}, ev.currentTarget);
        }, item.label);
      b.title = item.label;
      rBody.appendChild(b);
    });
  } else rBody.appendChild(el("div", "empty", "Keine offenen Läufe."));
  bottom.appendChild(r.panel);

  var c = panel("05", "cron", "cron_runs", {id: "panel-cron"});
  var cBody = c.body;
  if(runs.length){
    var head5 = el("div", "lhead");
    head5.style.gridTemplateColumns = "1fr 78px 74px";
    ["Job", "Status", "Start"].forEach(function(t){ head5.appendChild(el("span", null, t)); });
    cBody.appendChild(head5);
    runs.forEach(function(x){
      var t = tone(x.status);
      var b = rowButton([x.job_name || x.job_id || "—", "", dayClock(x.started_at)],
        "1fr 78px 74px", function(ev){
          openPeek("cron", x.run_id, {profile: x.profile, job: x.job_id}, ev.currentTarget);
        }, x.error || x.status || "");
      var st = b.children[1];
      st.textContent = "";
      st.appendChild(el("span", "sym " + t, symbolFor(t)));
      st.appendChild(document.createTextNode(" " + (x.status || "—")));
      cBody.appendChild(b);
    });
    var cmb = moreButton(cronMeta, "cron");
    if(cmb){ var cmw = el("div", "panel-foot"); cmw.appendChild(cmb); cBody.appendChild(cmw); }
  } else cBody.appendChild(el("div", "empty", "Keine Läufe im Fenster."));
  bottom.appendChild(c.panel);
  root.appendChild(bottom);

  // cron job configuration stays behind a toggle (it is configuration, not status)
  var tog = el("div", "panel");
  tog.id = "panel-jobs-toggle";
  var lab = el("label", "toggle");
  lab.style.cssText = "display:inline-flex;align-items:center;gap:8px;padding:7px 12px;font-size:11px;color:var(--dim);cursor:pointer";
  var cb = el("input");
  cb.type = "checkbox";
  cb.checked = jobsOpen;
  cb.onchange = function(){ jobsOpen = cb.checked; load(); };
  lab.appendChild(cb);
  lab.appendChild(document.createTextNode("Cron-Jobs (Konfiguration) anzeigen"));
  tog.appendChild(lab);
  root.appendChild(tog);

  if(jobsOpen){
    var j = panel("06", "cron-jobs", "cron_list", {id: "panel-jobs", flush: true});
    if(jobs && jobs.errors && Object.keys(jobs.errors).length){
      j.body.appendChild(el("div", "empty", Object.keys(jobs.errors).map(function(x){ return x + ": " + jobs.errors[x]; }).join(" · ")));
    }
    if(jobs && jobs.jobs && jobs.jobs.length){
      var hj = el("div", "lhead");
      hj.style.gridTemplateColumns = "1fr 120px 96px 96px";
      ["Job", "Zeitplan", "Status", "nächster Lauf"].forEach(function(t){ hj.appendChild(el("span", null, t)); });
      j.body.appendChild(hj);
      jobs.jobs.forEach(function(x){
        var row = el("div", "lrow");
        row.style.gridTemplateColumns = "1fr 120px 96px 96px";
        row.style.cursor = "default";
        row.appendChild(el("span", null, (x.profile ? x.profile + " · " : "") + (x.name || x.id || "—")));
        row.appendChild(el("span", "mono-dim", x.schedule || "—"));
        row.appendChild(el("span", null, x.state || "—"));
        row.appendChild(el("span", "num", dayClock(x.next_run_at)));
        j.body.appendChild(row);
      });
    } else if(jobs) j.body.appendChild(el("div", "empty", "Keine Jobs."));
    root.appendChild(j.panel);
  }
}

// -- page: sessions ---------------------------------------------------------

function filterBar(kind, options, state, onChange){
  var bar = el("div", "filters");
  function field(label, node){
    var l = el("label");
    l.appendChild(document.createTextNode(label));
    l.appendChild(node);
    bar.appendChild(l);
    return node;
  }
  var statuses = options.status || [];
  if(statuses.length){
    var sel = el("select");
    var all = el("option", null, "alle");
    all.value = "";
    sel.appendChild(all);
    statuses.forEach(function(s){
      var o = el("option", null, s.label || s);
      o.value = s.value || s;
      if((state.filters.status || "") === o.value) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = function(){ onChange("status", sel.value); };
    field("Status", sel);
  }
  var profiles = options.profiles || [];
  if(profiles.length){
    var psel = el("select");
    var pall = el("option", null, "alle Profile");
    pall.value = "";
    psel.appendChild(pall);
    profiles.forEach(function(p){
      var o = el("option", null, p);
      o.value = p;
      if(state.filters.profile === p) o.selected = true;
      psel.appendChild(o);
    });
    psel.onchange = function(){ onChange("profile", psel.value); };
    field("Profil", psel);
  }
  if(options.surfaces){
    var ssel = el("select");
    var sall = el("option", null, "alle Oberflächen");
    sall.value = "";
    ssel.appendChild(sall);
    options.surfaces.forEach(function(s){
      var o = el("option", null, s);
      o.value = s;
      if(state.filters.surface === s) o.selected = true;
      ssel.appendChild(o);
    });
    ssel.onchange = function(){ onChange("surface", ssel.value); };
    field("Oberfläche", ssel);
  }
  var hsel = el("select");
  [["1", "1 h"], ["24", "24 h"], ["168", "7 Tage"], ["720", "30 Tage"]].forEach(function(pair){
    var o = el("option", null, pair[1]);
    o.value = pair[0];
    if(String(state.filters.hours || "") === pair[0]) o.selected = true;
    hsel.appendChild(o);
  });
  hsel.onchange = function(){ onChange("hours", hsel.value); };
  field("Zeitraum", hsel);

  var input = el("input");
  input.type = "search";
  input.placeholder = "Freitext …";
  input.value = state.filters.q || "";
  var timer = null;
  input.oninput = function(){
    if(timer) clearTimeout(timer);
    timer = setTimeout(function(){ onChange("q", input.value); }, 250);
  };
  field("Suche", input);

  var reset = button("btn dim", "zurücksetzen", function(){ onChange("__reset", ""); });
  bar.appendChild(reset);
  return bar;
}

function pager(state, total, onPage){
  var wrap = el("div", "pager");
  var from = total ? state.offset + 1 : 0;
  var to = Math.min(total, state.offset + (state.limit || 25));
  wrap.appendChild(el("span", null, from + "–" + to + " von " + total));
  var prev = button("btn dim", "← zurück", function(){ onPage(Math.max(0, state.offset - (state.limit || 25))); });
  prev.disabled = state.offset <= 0;
  var next = button("btn dim", "weiter →", function(){ onPage(state.offset + (state.limit || 25)); });
  next.disabled = to >= total;
  wrap.appendChild(prev);
  wrap.appendChild(next);
  return wrap;
}

function listPage(title, subtitle, kind, columns, widths, drawRow, opts){
  opts = opts || {};
  root.textContent = "";
  var head = el("div", "page-head");
  var h1 = el("h1", "page-title", title);
  h1.appendChild(el("span", "dot", "."));
  head.appendChild(h1);
  head.appendChild(el("span", "sub", subtitle));
  head.appendChild(el("span", "spacer"));
  head.appendChild(el("span", "sub", "read-only · LAN"));
  root.appendChild(head);

  var holder = el("div");
  root.appendChild(holder);

  function loadPage(){
    holder.textContent = "";
    holder.appendChild(el("div", "empty", "lade …"));
    getJSON(listUrl(kind, listState.filters)).then(function(d){
      holder.textContent = "";
      var err = errorBox(d.errors);
      if(err) holder.appendChild(err);
      // the profile list comes from the payload, so a filter never invents a profile name
      opts.options = opts.options || {};
      if(Array.isArray(d.profiles) && d.profiles.length) opts.options.profiles = d.profiles;
      holder.appendChild(filterBar(kind, opts.options || {}, listState, function(key, value){
        if(key === "__reset"){ listState.filters = {}; listState.offset = 0; }
        else {
          if(value) listState.filters[key] = value; else delete listState.filters[key];
          listState.offset = 0;
        }
        loadPage();
      }));
      var p = panel("01", title.toLowerCase(), "api/list?kind=" + kind, {flush: true});
      if(d.items && d.items.length){
        var h = el("div", "lhead");
        h.style.gridTemplateColumns = widths;
        columns.forEach(function(c){ h.appendChild(el("span", null, c)); });
        p.body.appendChild(h);
        d.items.forEach(function(item){ p.body.appendChild(drawRow(item)); });
      } else {
        p.body.appendChild(el("div", "empty", "Keine Einträge für diesen Filter."));
      }
      listState.total_is_bounded = !!d.total_is_bounded;
      p.panel.appendChild(pager(listState, d.total || 0, function(offset){
        listState.offset = offset;
        loadPage();
      }));
      holder.appendChild(p.panel);
      if(d.capped) holder.appendChild(el("div", "legend", "Die Abfrage ist an ihrer Obergrenze angekommen — enger filtern (Profil, Zeitraum, Freitext) zeigt ältere Einträge."));
    }).catch(function(e){
      holder.textContent = "";
      var box = el("div", "notice bad", "Abruf fehlgeschlagen: " + String(e));
      holder.appendChild(box);
    });
  }
  loadPage();
}

function renderSessions(){
  listPage("Sessions", "alle Sessions, nach Aktivität", "sessions",
    ["Session", "Profil", "Oberfläche", "Status", "Nachrichten", "zuletzt"],
    "1fr 110px 110px 92px 92px 92px",
    function(item){
      return rowButton([
        item.title || item.session_id || "—",
        item.profile || "—",
        item.surface || "—",
        item.status || "—",
        item.message_count === null || item.message_count === undefined ? "—" : String(item.message_count),
        ago(item.last_active) || "—"
      ], "1fr 110px 110px 92px 92px 92px", function(ev){
        openPeek("session", item.session_id, {profile: item.profile}, ev.currentTarget);
      }, item.title || item.session_id);
    },
    {options: {status: ["aktiv", "offen", "beendet"]}});
}

function renderRuns(){
  listPage("Runs", "Fehlschläge, Unterbrechungen und was sie erledigt hat", "runs",
    ["Lauf", "Profil", "Zustand", "Vermerk", "Zeit"],
    "1fr 100px 130px 1fr 96px",
    function(item){
      var b = rowButton([
        item.summary || item.id || "—",
        item.profile || "—",
        "",
        item.settled_text || item.event_kind || "—",
        dayClock(item.at)
      ], "1fr 100px 130px 1fr 96px", function(ev){
        openPeek("run", item.id, {profile: item.profile}, ev.currentTarget);
      }, item.summary || item.id);
      var st = b.children[2];
      st.textContent = "";
      st.appendChild(el("span", "sym " + (item.tone || "muted"), symbolFor(item.tone || "muted")));
      st.appendChild(document.createTextNode(" " + (item.state || "—")));
      return b;
    },
    {options: {status: [{value: "attention", label: "offen"}, {value: "interrupted", label: "unterbrochen"},
                        {value: "settled", label: "erledigt"}]}});
}

function renderCron(){
  listPage("Cron", "Läufe aus dem Cron-Ledger", "cron",
    ["Job", "Profil", "Status", "Start", "Dauer", "Zustellung"],
    "1fr 100px 100px 96px 84px 110px",
    function(item){
      var b = rowButton([
        item.job_name || item.job_id || "—",
        item.profile || "—",
        "",
        dayClock(item.started_at),
        dur(item.duration_s),
        item.delivery_outcome || "—"
      ], "1fr 100px 100px 96px 84px 110px", function(ev){
        openPeek("cron", item.id, {profile: item.profile, job: item.job_id}, ev.currentTarget);
      }, item.error || item.job_name || item.id);
      var st = b.children[2];
      st.textContent = "";
      st.appendChild(el("span", "sym " + (item.tone || "muted"), symbolFor(item.tone || "muted")));
      st.appendChild(document.createTextNode(" " + (item.status || "—")));
      return b;
    },
    {options: {status: ["completed", "failed", "error", "cancelled", "missed"]}});
}

// -- page: gateway ----------------------------------------------------------

function renderGateway(){
  root.textContent = "";
  var head = el("div", "page-head");
  var h1 = el("h1", "page-title", "Gateway");
  h1.appendChild(el("span", "dot", "."));
  head.appendChild(h1);
  head.appendChild(el("span", "sub", "Plattformen, Versionen, Profile, Keys"));
  root.appendChild(head);
  var holder = el("div");
  root.appendChild(holder);
  holder.appendChild(el("div", "empty", "lade …"));
  getJSON("/ledger/api/gateway").then(function(d){
    holder.textContent = "";
    var err = errorBox(d.errors);
    if(err) holder.appendChild(err);
    var gw = d.gateway || {};

    var top = el("div", "grid-top");
    var p1 = panel("01", "plattformen", "status_gateway", {flush: true});
    var plats = gw.platforms || [];
    if(plats.length){
      var h = el("div", "lhead");
      h.style.gridTemplateColumns = "1fr 120px 1fr 110px";
      ["Plattform", "Status", "Fehler", "aktualisiert"].forEach(function(t){ h.appendChild(el("span", null, t)); });
      p1.body.appendChild(h);
      plats.forEach(function(x){
        var row = el("div", "lrow");
        row.style.gridTemplateColumns = "1fr 120px 1fr 110px";
        row.style.cursor = "default";
        row.appendChild(el("span", null, x.name || "—"));
        var st = el("span");
        var t = x.stale ? "bad" : x.needs_attention ? "warn" : tone(x.state);
        st.appendChild(el("span", "sym " + t, symbolFor(t)));
        st.appendChild(document.createTextNode(" " + (x.state || "?")));
        row.appendChild(st);
        row.appendChild(el("span", "mono-dim", x.error || "—"));
        row.appendChild(el("span", "num", dayClock(x.updated_at)));
        p1.body.appendChild(row);
      });
      (gw.stale_platforms || []).forEach(function(x){
        var row = el("div", "lrow");
        row.style.gridTemplateColumns = "1fr 120px 1fr 110px";
        row.style.cursor = "default";
        row.appendChild(el("span", null, x.name + " (verwaist)"));
        row.appendChild(el("span", "mono-dim", "—"));
        row.appendChild(el("span", "mono-dim", x.reason || "—"));
        row.appendChild(el("span", null, "—"));
        p1.body.appendChild(row);
      });
    } else p1.body.appendChild(el("div", "empty", "Keine Plattformmeldungen."));
    top.appendChild(p1.panel);

    var p2 = panel("02", "versionen", "hermes_health", {});
    var dl = el("dl", "meta-list");
    [["Gateway", gw.state || "—"], ["Health", gw.health || "—"], ["PID", gw.pid || "—"],
     ["Code", gw.code_version || "—"], ["Hermes", gw.hermes_version || "—"],
     ["Agenten aktiv", gw.active_agents === null || gw.active_agents === undefined ? "?" : gw.active_agents],
     ["Stand", dayClock(gw.state_updated_at)]].forEach(function(pair){
      dl.appendChild(el("dt", null, pair[0]));
      dl.appendChild(el("dd", null, String(pair[1])));
    });
    p2.body.appendChild(dl);
    top.appendChild(p2.panel);
    holder.appendChild(top);

    var p3 = panel("03", "profile", "profiles_list", {flush: true});
    var h3 = el("div", "lhead");
    h3.style.gridTemplateColumns = "1fr 120px";
    ["Profil", "erreichbar"].forEach(function(t){ h3.appendChild(el("span", null, t)); });
    p3.body.appendChild(h3);
    (d.profiles || []).forEach(function(p){
      var row = el("div", "lrow");
      row.style.gridTemplateColumns = "1fr 120px";
      row.style.cursor = "default";
      row.appendChild(el("span", null, p.name || "—"));
      var st = el("span");
      st.appendChild(el("span", "sym " + (p.reachable ? "ok" : "muted"), p.reachable ? "●" : "○"));
      st.appendChild(document.createTextNode(" " + (p.reachable ? "ja" : "nein")));
      row.appendChild(st);
      p3.body.appendChild(row);
    });
    holder.appendChild(p3.panel);

    var p4 = panel("04", "keys", "keys_list", {flush: true});
    if((d.keys || []).length){
      var h4 = el("div", "lhead");
      h4.style.gridTemplateColumns = "1fr 1fr 110px 96px";
      ["Name", "Scopes", "zuletzt", "Status"].forEach(function(t){ h4.appendChild(el("span", null, t)); });
      p4.body.appendChild(h4);
      d.keys.forEach(function(k){
        var row = el("div", "lrow");
        row.style.gridTemplateColumns = "1fr 1fr 110px 96px";
        row.style.cursor = "default";
        row.appendChild(el("span", null, k.name || k.id || "—"));
        var sc = k.scopes || {};
        var parts = [];
        if(sc.access) parts.push(sc.access);
        if(sc.profiles && sc.profiles.length) parts.push(sc.profiles.join(","));
        if(sc.toolsets && sc.toolsets.length) parts.push(sc.toolsets.length + " Toolsets");
        row.appendChild(el("span", "mono-dim", parts.join(" · ") || "—"));
        row.appendChild(el("span", "num", dayClock(k.last_used)));
        row.appendChild(el("span", "mono-dim", k.disabled ? "deaktiviert" : "aktiv"));
        p4.body.appendChild(row);
      });
    } else {
      p4.body.appendChild(el("div", "empty", d.errors && d.errors.keys ? "Keys nicht lesbar (read-only-Sicht)." : "Keine Keys."));
    }
    holder.appendChild(p4.panel);
  }).catch(function(e){
    holder.textContent = "";
    holder.appendChild(el("div", "notice bad", "Abruf fehlgeschlagen: " + String(e)));
  });
}

// -- page: session ----------------------------------------------------------

function renderSession(){
  var sid = route.id;
  root.textContent = "";
  var holder = el("div");
  root.appendChild(holder);
  holder.appendChild(el("div", "empty", "lade …"));
  getJSON("/ledger/api/session/" + encodeURIComponent(sid)).then(function(d){
    holder.textContent = "";
    var crumbs = el("div", "crumbs");
    var back = el("a", null, "~/sessions");
    back.href = "/ledger/sessions";
    crumbs.appendChild(back);
    crumbs.appendChild(el("span", "sep", "/"));
    crumbs.appendChild(el("span", null, d.ok === false ? sid : (d.session && d.session.id) || sid));
    holder.appendChild(crumbs);

    if(d.ok === false){
      holder.appendChild(el("div", "notice bad", d.error || "Session nicht gefunden."));
      var b = el("a", "btn", "← zurück");
      b.href = "/ledger/sessions";
      holder.appendChild(b);
      return;
    }
    var s = d.session || {}, st = d.stats || {};
    var head = el("div", "page-head");
    var h1 = el("h1", "page-title", s.title || s.id);
    head.appendChild(h1);
    var t = tone(s.state);
    var c = chip(s.state || "?", t === "ok" ? "green" : t === "bad" ? "red" : t === "warn" ? "amber" : "");
    c.insertBefore(el("span", "sym " + t, symbolFor(t)), c.firstChild);
    head.appendChild(c);
    head.appendChild(el("span", "spacer"));
    var backBtn = el("a", "btn dim", "← zurück");
    backBtn.href = "/ledger/sessions";
    head.appendChild(backBtn);
    var links = d.links || {};
    if(links.desktop) head.appendChild(extLink(links.desktop, "In Desktop-App"));
    if(links.web) head.appendChild(extLink(links.web, "In Web-App"));
    holder.appendChild(head);

    var strip = el("div", "stat-strip");
    [["Zustand", st.state || "—"], ["Nachrichten", st.messages === null || st.messages === undefined ? "—" : st.messages],
     ["Tool-Aufrufe", st.tool_calls === null || st.tool_calls === undefined ? "—" : st.tool_calls],
     ["Laufzeit", st.runtime_s === null || st.runtime_s === undefined ? "—" : dur(st.runtime_s)],
     ["offene Freigaben", st.open_approvals === null || st.open_approvals === undefined ? 0 : st.open_approvals]
    ].forEach(function(pair){
      var cell = el("div", "cell");
      cell.appendChild(el("div", "k", pair[0]));
      cell.appendChild(el("div", "v", String(pair[1])));
      strip.appendChild(cell);
    });
    holder.appendChild(strip);

    var err = errorBox(d.errors);
    if(err) holder.appendChild(err);

    var grid = el("div", "session-grid");
    var left = panel("01", "verlauf", "sessions_messages", {flush: true});
    var msgs = d.messages || [];
    if(!msgs.length) left.body.appendChild(el("div", "empty", "Keine Nachrichten gelesen."));
    msgs.forEach(function(m){
      var row = el("div", "msg " + (m.role || ""));
      row.appendChild(el("div", "t", clock(m.at)));
      var body = el("div", "body");
      var role = el("div", "role", (m.role === "user" ? "› user" : m.role === "assistant" ? "‹ agent" :
        m.role === "tool" ? "⚙ tool" : m.role || "?") + (m.tool ? " · " + m.tool : ""));
      body.appendChild(role);
      if(m.truncated){
        var det = el("details");
        var sum = el("summary", null, truncate(m.text, 220));
        det.appendChild(sum);
        det.appendChild(el("div", "text", m.text));
        body.appendChild(det);
      } else {
        body.appendChild(el("div", "text", m.text || "—"));
      }
      if(m.calls && m.calls.length){
        body.appendChild(el("div", "legend", "→ " + m.calls.join(", ")));
      }
      row.appendChild(body);
      left.body.appendChild(row);
    });
    grid.appendChild(left.panel);

    var rightCol = el("div");
    rightCol.style.cssText = "display:flex;flex-direction:column;gap:12px;min-width:0";
    var meta = panel("02", "metadaten", "sessions_get", {});
    var dl = el("dl", "meta-list");
    [["Session", s.id], ["Profil", s.profile], ["Oberfläche", s.surface], ["Modell", s.model],
     ["gestartet", dayClock(s.started_at)], ["letzte Aktivität", dayClock(s.last_active)],
     ["Runs", (d.runs || []).length]].forEach(function(pair){
      dl.appendChild(el("dt", null, pair[0]));
      dl.appendChild(el("dd", null, pair[1] || "—"));
    });
    meta.body.appendChild(dl);
    rightCol.appendChild(meta.panel);

    var usage = panel("03", "tool-nutzung", "sessions_messages", {});
    if((d.tool_usage || []).length){
      var bars = el("div", "bars");
      d.tool_usage.forEach(function(u){
        var row = el("div", "bar-row");
        row.appendChild(el("span", "name", u.name));
        var track = el("div", "track");
        var fill = el("div", "fill");
        fill.style.width = Math.max(2, Math.min(100, u.pct || 0)) + "%";
        track.appendChild(fill);
        row.appendChild(track);
        row.appendChild(el("span", "n", String(u.count)));
        bars.appendChild(row);
      });
      usage.body.appendChild(bars);
    } else usage.body.appendChild(el("div", "empty", "Keine Tool-Aufrufe im Verlauf."));
    rightCol.appendChild(usage.panel);

    var sig = panel("04", "signale", "inbox_attention", {});
    (d.signals || []).forEach(function(sg){
      var line = el("div", "row-line");
      line.appendChild(symNode(sg.tone));
      line.appendChild(el("span", "what", sg.text));
      sig.body.appendChild(line);
    });
    rightCol.appendChild(sig.panel);
    grid.appendChild(rightCol);
    holder.appendChild(grid);
  }).catch(function(e){
    holder.textContent = "";
    holder.appendChild(el("div", "notice bad", "Abruf fehlgeschlagen: " + String(e)));
  });
}

// -- data loading -----------------------------------------------------------

var lastOverview = null;

function persist(){
  if(!storage) return;
  if(!saveDismissed(storage, dismissed)) storage = null;   // quota/security error: session-only
}

function getJSON(url){
  return fetch(url, {credentials: "same-origin", headers: {"Accept": "application/json"}})
    .then(function(r){
      if(!r.ok){
        var err = new Error(r.status + " " + r.statusText);
        err.status = r.status;
        throw err;
      }
      return r.json();
    });
}

function loadOverview(){
  var names = Object.keys(expanded).filter(function(n){ return expanded[n]; });
  var p = getJSON("/ledger/overview" + (names.length ? "?more=" + encodeURIComponent(names.join(",")) : ""));
  if(jobsOpen){
    p = Promise.all([p, getJSON("/ledger/jobs").catch(function(e){
      return {jobs: [], count: 0, errors: {"jobs": String(e)}};
    })]).then(function(r){ jobs = r[1]; return r[0]; });
  }
  return p;
}

function load(){
  loadOverview().then(function(d){
    lastOverview = d;
    render(d);
  }).catch(function(e){
    root.textContent = "";
    var b = el("div", "notice bad");
    b.appendChild(el("strong", null, "Abruf fehlgeschlagen: "));
    b.appendChild(document.createTextNode(String(e)));
    root.appendChild(b);
  });
}

function render(d){
  if(route.page === "attention") renderAttention(d);
}

// -- boot -------------------------------------------------------------------

renderNav();
renderFoot();

if(META.view_error){
  root.textContent = "";
  var ve = el("div", "notice bad");
  ve.appendChild(el("strong", null, "Ledger-Sicht nicht verfügbar: "));
  ve.appendChild(document.createTextNode(META.view_error));
  ve.appendChild(el("div", "sub", "Es wird nichts angezeigt, statt auf eine breitere Sicht zurückzufallen. " +
    "Prüfe HERMCP_LEDGER_VIEW_KEY (Key-ID, nicht das Token) und ob der Schlüssel aktiv ist."));
  root.appendChild(ve);
} else if(route.page === "sessions"){
  renderSessions();
} else if(route.page === "runs"){
  renderRuns();
} else if(route.page === "cron"){
  renderCron();
} else if(route.page === "gateway"){
  renderGateway();
} else if(route.page === "session"){
  renderSession();
} else {
  load();
  var timer = setInterval(function(){ if(!document.hidden) load(); }, POLL * 1000);
}
})();
