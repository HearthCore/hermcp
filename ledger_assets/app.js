(function(){
"use strict";

/* ---------------------------------------------------------------------------
 * Pure helpers. They touch nothing but the values they are given, so
 * tests/test_ledger_js.mjs can run this file in node (no document) and use them
 * directly: globalThis.__LEDGER_HELPERS__.
 * ------------------------------------------------------------------------- */

var DISMISS_KEY = "hermcp.ledger.dismissed.v1";

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

// What the badges count: approvals (never dismissable) + failures the user has not
// dismissed. Superseded runs are already out of att.failed_cron_runs.
function attentionCount(att, dismissed){
  att = att || {};
  var cron = splitFailures(att.failed_cron_runs, dismissed).active.length;
  var chat = splitFailures(att.failed_chat_runs, dismissed).active.length;
  var approvals = (att.approvals && att.approvals.count) || 0;
  return {approvals: approvals, cron: cron, chat: chat, total: approvals + cron + chat};
}

var HELPERS = {
  DISMISS_KEY: DISMISS_KEY, idOf: idOf, store: store, loadDismissed: loadDismissed,
  saveDismissed: saveDismissed, pruneDismissed: pruneDismissed, splitFailures: splitFailures,
  attentionCount: attentionCount
};
if(typeof globalThis !== "undefined") globalThis.__LEDGER_HELPERS__ = HELPERS;
if(typeof document === "undefined") return;          // node: helpers only, no rendering

/* ---------------------------------------------------------------------------
 * the page
 * ------------------------------------------------------------------------- */

var POLL = __POLL__;
var root = document.getElementById("root");
var jobsOpen = false, timer = null;
var storage = store(window);
var dismissed = loadDismissed(storage);
var showHidden = false, showSuperseded = false;

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
function pad(n){return n<10?"0"+n:String(n);}
function clock(iso){if(!iso)return "—";var d=new Date(iso);if(isNaN(+d))return "—";
  return pad(d.getHours())+":"+pad(d.getMinutes())+":"+pad(d.getSeconds());}
function dayClock(iso){if(!iso)return "—";var d=new Date(iso);if(isNaN(+d))return "—";
  var t=new Date();return (d.toDateString()===t.toDateString()?"":pad(d.getDate())+"."+pad(d.getMonth()+1)+". ")+clock(iso);}
function ago(iso){if(!iso)return "";var d=new Date(iso);if(isNaN(+d))return "";
  var s=Math.max(0,Math.round((Date.now()-d)/1000));
  if(s<60)return "vor "+s+" s"; if(s<3600)return "vor "+Math.round(s/60)+" min";
  if(s<86400)return "vor "+Math.round(s/3600)+" h"; return "vor "+Math.round(s/86400)+" d";}
function dur(v){if(v===null||v===undefined)return "—"; if(v<1)return Math.round(v*1000)+" ms";
  if(v<60)return v.toFixed(1).replace(".",",")+" s"; return Math.floor(v/60)+" min "+Math.round(v%60)+" s";}
function tone(v){var s=String(v||"").toLowerCase();
  if(s==="aktiv"||s==="completed"||s==="ok"||s==="connected"||s==="running")return "ok";
  if(s==="claimed"||s==="started"||s==="queued")return "warn";
  if(s==="error"||s==="failed"||s==="disconnected")return "bad"; return "muted";}
function pill(v){return el("span","pill "+(tone(v)==="muted"?"":tone(v)),v||"—");}
function link(url,label){var a=el("a",null,label||"öffnen");
  if(!url){return el("span","mono","—");} a.href=url; a.target="_blank"; a.rel="noopener noreferrer"; return a;}
function table(headers,rows){var w=el("div","tw"),t=el("table");var th=el("thead"),tr=el("tr");
  headers.forEach(function(x){tr.appendChild(el("th",null,x));});th.appendChild(tr);t.appendChild(th);
  var tb=el("tbody");rows.forEach(function(r){tb.appendChild(r);});t.appendChild(tb);w.appendChild(t);return w;}
function row(cells){var tr=el("tr");cells.forEach(function(c){
  if(c&&c.nodeType){var td=el("td");td.appendChild(c);tr.appendChild(td);}else{tr.appendChild(el("td",null,c));}});return tr;}

var lastPayload = null, jobs = null;

function persist(){
  if(!storage) return;
  if(!saveDismissed(storage, dismissed)) storage = null;   // quota/security error: session-only
}
function dismiss(id){
  if(!id) return;
  dismissed[id] = 1;
  persist();
  if(lastPayload) render(lastPayload);
}
function resetDismissed(){
  dismissed = {};
  persist();
  if(lastPayload) render(lastPayload);
}

function failureItem(r, kind){
  var it = el("div","it");
  it.appendChild(document.createTextNode(
    dayClock(kind === "cron" ? r.started_at : r.at) + " · " +
    (kind === "cron" ? (r.job_name || r.job_id || "?") : (r.profile || "?")) + " · " +
    (kind === "cron" ? (r.error || "Fehler") : (r.summary || "ohne Meldung")) +
    (kind !== "cron" && r.session ? " · von " + r.session : "") + " "));
  if(r.url) it.appendChild(link(r.url,"Session"));
  it.appendChild(button("dismiss","Ausblenden",function(){ dismiss(idOf(r)); }));
  return it;
}

function hiddenLine(cronHidden, chatHidden, supersededCount){
  var n = cronHidden + chatHidden;
  var wrap = el("div","hiddenline");
  wrap.appendChild(el("span","sub", n + " ausgeblendet" +
    (supersededCount ? " · " + supersededCount + " überholt" : "")));
  wrap.appendChild(button("dismiss", showHidden ? "wieder einblenden" : "anzeigen",
    function(){ showHidden = !showHidden; if(lastPayload) render(lastPayload); }));
  if(n) wrap.appendChild(button("dismiss","Zurücksetzen",function(){ resetDismissed(); }));
  return wrap;
}

function render(d){
  lastPayload = d;
  // prune: ids that left the 24 h window are dropped from storage
  var alive = [], att0 = d.attention || {};
  (att0.failed_cron_runs || []).forEach(function(r){ alive.push(idOf(r)); });
  (att0.failed_chat_runs || []).forEach(function(r){ alive.push(idOf(r)); });
  (att0.superseded_cron_runs || []).forEach(function(r){ alive.push(idOf(r)); });
  var pruned = pruneDismissed(dismissed, alive);
  if(Object.keys(pruned).length !== Object.keys(dismissed).length){ dismissed = pruned; persist(); }

  root.textContent = "";
  var gw = d.gateway || {}, att = d.attention || {}, sec = d.sections || {},
      sessions = (sec.sessions && sec.sessions.items) || [], cron = sec.cron || {}, runs = cron.runs || [],
      ap = sec.approvals || {count:0,items:[]}, links = d.links || {};

  var counts = attentionCount(att, dismissed);
  var cronSplit = splitFailures(att.failed_cron_runs, dismissed);
  var chatSplit = splitFailures(att.failed_chat_runs, dismissed);
  var superseded = att.superseded_cron_runs || [];

  var head = el("div","head");
  head.appendChild(el("h1",null,"hermcp Ledger"));
  var verdict = String(gw.verdict || gw.health || "").toLowerCase();
  var p = el("span","pill "+(verdict==="ok"?"ok":verdict==="degraded"?"warn":verdict?"bad":""));
  p.appendChild(el("span","dot "+(verdict==="ok"?"ok":verdict==="degraded"?"warn":verdict?"bad":"")));
  p.appendChild(document.createTextNode(gw.state?(gw.state+" · "+(gw.health||gw.verdict||"?")):"unbekannt"));
  head.appendChild(p); head.appendChild(el("span","spacer"));
  head.appendChild(el("span","sub","aktualisiert "+clock(d.generated_at)+" · Polling alle "+
    (d.poll_seconds||POLL)+" s"));
  root.appendChild(head);

  var errs = Object.keys(d.errors||{});
  if(errs.length){var e=el("div","err");e.appendChild(el("strong",null,"Teilweise nicht lesbar: "));
    e.appendChild(document.createTextNode(errs.map(function(k){return k+": "+d.errors[k];}).join(" · ")));
    root.appendChild(e);}
  if(!storage){
    var hint = el("div","err");
    hint.appendChild(el("strong",null,"Browser-Speicher nicht verfügbar: "));
    hint.appendChild(document.createTextNode("Ausblenden gilt nur für diese Ansicht und ist nach dem Neuladen weg."));
    root.appendChild(hint);
  }

  var k = el("div","kpis");
  [["aktive Sessions",att.active_sessions||0,""],["Sessions gelistet",sessions.length,""],
   ["offene Freigaben",counts.approvals,counts.approvals?"bad":""],
   ["Fehlschläge 24 h",counts.cron + counts.chat,(counts.cron+counts.chat)?"bad":""],
   ["Profile erreichbar",(d.profiles||[]).length,""],
   ["Plattformen auffällig",(gw.platforms||[]).filter(function(x){
      return x.stale||x.needs_attention||String(x.state)!=="connected";}).length,"warn"]
  ].forEach(function(t){var n=el("div","kpi"+(t[2]?" "+t[2]:""));n.appendChild(el("b",null,t[1]));
    n.appendChild(el("span",null,t[0]));k.appendChild(n);});
  root.appendChild(k);

  var hiddenAny = cronSplit.hidden.length + chatSplit.hidden.length;
  if(counts.total || hiddenAny || superseded.length){
    var a = el("div","alert");
    a.appendChild(el("h2",null,"Braucht Aufmerksamkeit"));
    if(counts.cron + counts.chat){
      var bar = el("div","hiddenline");
      bar.appendChild(button("dismiss all","Alle ausblenden",function(){
        cronSplit.active.concat(chatSplit.active).forEach(function(r){ dismissed[idOf(r)] = 1; });
        persist(); render(d);
      }));
      a.appendChild(bar);
    }
    // approvals are actionable: no dismiss control here, on purpose
    if(counts.approvals){
      a.appendChild(el("h4",null,"Offene Freigaben ("+counts.approvals+")"));
      ap.items.forEach(function(x){a.appendChild(el("div","it",(x.profile||"?")+" · "+
        (x.command||"Anfrage ohne Befehlsangabe")+(x.rationale?" — "+x.rationale:"")));});
    }
    if(cronSplit.active.length){
      a.appendChild(el("h4",null,"Fehlgeschlagene Cron-Läufe, 24 h ("+cronSplit.active.length+")"));
      cronSplit.active.slice(0,8).forEach(function(r){ a.appendChild(failureItem(r,"cron")); });
    }
    if(chatSplit.active.length){
      a.appendChild(el("h4",null,"Fehlgeschlagene Agent-Läufe, 24 h ("+chatSplit.active.length+")"));
      chatSplit.active.slice(0,8).forEach(function(r){ a.appendChild(failureItem(r,"chat")); });
    }
    if(hiddenAny || superseded.length){
      a.appendChild(hiddenLine(cronSplit.hidden.length, chatSplit.hidden.length, superseded.length));
    }
    if(showHidden && hiddenAny){
      var hg = el("div","hidden");
      cronSplit.hidden.forEach(function(r){ hg.appendChild(failureItem(r,"cron")); });
      chatSplit.hidden.forEach(function(r){ hg.appendChild(failureItem(r,"chat")); });
      a.appendChild(hg);
    }
    if(superseded.length){
      var tog = el("div","superseded");
      tog.appendChild(button("dismiss", showSuperseded ? "Überholt ausblenden" : "Überholt anzeigen",
        function(){ showSuperseded = !showSuperseded; render(d); }));
      if(showSuperseded){
        superseded.slice(0,10).forEach(function(r){
          var it = el("div","it");
          it.appendChild(document.createTextNode(dayClock(r.started_at)+" · "+(r.job_name||r.job_id||"?")+
            " · "+(r.error||"Fehler")+" · wieder ok seit "+dayClock(r.ok_after)+" "));
          if(r.url) it.appendChild(link(r.url,"Session"));
          tog.appendChild(it);
        });
      }
      a.appendChild(tog);
    }
    if(ap.hidden)a.appendChild(el("div","it",ap.hidden+" weitere Freigaben in anderen Profilen"));
    root.appendChild(a);
  } else {
    root.appendChild(el("div","empty","Nichts braucht gerade Aufmerksamkeit."));
  }

  var gcard=el("section","card"),ghd=el("div","hd");
  ghd.appendChild(el("h3",null,"Gateway und Plattformen"));
  ghd.appendChild(el("span","sub",gw.code_version?"v"+gw.code_version:""));gcard.appendChild(ghd);
  var chips=el("div","chips");
  function chip(dotCls,text,title){var c=el("span","chip");c.appendChild(el("span","dot "+(dotCls||"")));
    c.appendChild(document.createTextNode(text));if(title)c.title=title;return c;}
  chips.appendChild(chip("ok","gateway · "+(gw.state||"?")+" · pid "+(gw.pid||"?")));
  chips.appendChild(chip("","Agenten aktiv: "+(gw.active_agents===null||gw.active_agents===undefined?"?":gw.active_agents)));
  (gw.platforms||[]).forEach(function(x){
    chips.appendChild(chip(x.stale?"bad":x.needs_attention?"warn":(tone(x.state)==="ok"?"ok":tone(x.state)==="bad"?"bad":""),
      x.name+" · "+(x.state||"?"),x.error||x.updated_at||""));});
  (gw.stale_platforms||[]).forEach(function(x){chips.appendChild(chip("",x.name+" · verwaist",x.reason||""));});
  gcard.appendChild(chips);root.appendChild(gcard);

  var cl = d.clients || {}, groups = cl.clients || [];
  var clcard=el("section","card"),clhd=el("div","hd");
  clhd.appendChild(el("h3",null,"Clients und Sessions"));
  clhd.appendChild(el("span","sub",groups.length+" Clients"+(d.kanban_url?" · ":"")));
  if(d.kanban_url) clhd.lastChild.appendChild(link(d.kanban_url,"Kanban-Board"));
  clcard.appendChild(clhd);
  if(groups.length){
    var rowsC=[];
    groups.forEach(function(g){ g.sessions.forEach(function(s,i){
      rowsC.push(row([i===0?g.client:"",
        el("span",null,(s.kind||"—")+(s.role?" · "+s.role:"")),
        el("span",null,s.purpose||"—"),
        el("span","pill "+(s.state==="active"?"ok":""),s.state==="active"?"aktiv":"ruhig"),
        el("span","num",ago(s.last_seen)||"—"),
        el("span","num",s.open_runs.length?(s.open_runs.length+" offen ("+
          s.open_runs.map(function(r){return r.profile;}).join(", ")+")"):"—"),
        el("span","mono",s.client_session)]));
    }); });
    clcard.appendChild(table(["Client","Art","Auftrag","Status","zuletzt","Runs","Handle"],rowsC));
  } else clcard.appendChild(el("div","empty","Keine Sessions registriert."));
  clcard.appendChild(el("div","legend","aktiv = in den letzten "+(cl.active_window_minutes||15)+
    " Minuten gesehen oder mit offenen Runs · "+(cl.unclaimed_count?(cl.unclaimed_count+
    " offene Runs ohne Session · "):"")+(cl.handed_over_hidden?(cl.handed_over_hidden+
    " übernommene Sessions ausgeblendet · "):"")+"Karten sind Beschriftungen, keine Berechtigungen."));
  root.appendChild(clcard);

  var scard=el("section","card"),shd=el("div","hd");
  shd.appendChild(el("h3",null,"Sessions und Runs"));
  shd.appendChild(el("span","sub",sessions.length+" Einträge · "+(d.profiles||[]).length+" Profile"));
  scard.appendChild(shd);
  if(sessions.length){
    scard.appendChild(table(["Profil","Plattform","Status","zuletzt","Session-ID","Modell","Desktop"],
      sessions.map(function(s){return row([s.profile||"—",el("span","mono",s.source||"—"),pill(s.status),
        el("span","num",ago(s.last_active)||"—"),el("span","mono",s.session_id||"—"),
        el("span","mono",s.model||"—"),link(s.url)]);})));
  } else scard.appendChild(el("div","empty","Keine Sessions gelesen."));
  scard.appendChild(el("div","legend","Status: aktiv = in den letzten 10 Minuten benutzt und nicht beendet · "+
    "offen = unberührt, nicht beendet · beendet. "+(links.base_url?
    ("Link: "+links.pattern.replace("{base}",links.base_url)+" ("+links.source+")."):links.note)));
  root.appendChild(scard);

  var ccard=el("section","card"),chd=el("div","hd");
  chd.appendChild(el("h3",null,"Cron-Läufe (letzte "+(cron.window_hours||24)+" h)"));
  chd.appendChild(el("span","sub",(cron.count||0)+" Läufe · "+(cron.failed_count||0)+" Fehlschläge"+
    (cron.truncated?" · auf 100 gekürzt":"")));ccard.appendChild(chd);
  if(runs.length){
    ccard.appendChild(table(["Job","Status","Start","Dauer","Zustellung","Session","Desktop"],
      runs.slice(0,60).map(function(r){return row([r.job_name||r.job_id||"—",pill(r.status),
        el("span","num",dayClock(r.started_at)),el("span","num",dur(r.duration_s)),
        el("span","mono",r.delivery_outcome||"—"),el("span","mono",r.session_id||"—"),link(r.url)]);})));
  } else ccard.appendChild(el("div","empty","Keine Läufe im Fenster."));
  root.appendChild(ccard);

  var tog2=el("div"),lab=el("label","toggle"),cb=el("input");
  cb.type="checkbox";cb.checked=jobsOpen;cb.onchange=function(){jobsOpen=cb.checked;load();};
  lab.appendChild(cb);lab.appendChild(document.createTextNode("Cron-Jobs (Konfiguration) anzeigen"));
  tog2.appendChild(lab);root.appendChild(tog2);

  if(jobsOpen){var jc=el("section","card"),jh=el("div","hd");
    jh.appendChild(el("h3",null,"Cron-Jobs (Konfiguration)"));
    jh.appendChild(el("span","sub",jobs&&jobs.count!==undefined?(jobs.count+" Jobs"):"lade …"));
    jc.appendChild(jh);
    if(jobs&&jobs.errors&&Object.keys(jobs.errors).length){
      jc.appendChild(el("div","empty",Object.keys(jobs.errors).map(function(x){
        return x+": "+jobs.errors[x];}).join(" · ")));}
    if(jobs&&jobs.jobs&&jobs.jobs.length){
      jc.appendChild(table(["Profil","Job","Zeitplan","Status","nächster Lauf","letzter Lauf","Modell"],
        jobs.jobs.map(function(j){return row([j.profile||"—",j.name||j.id||"—",el("span","mono",j.schedule||"—"),
          pill(j.state),el("span","num",dayClock(j.next_run_at)),
          el("span","num",j.last_run_at?dayClock(j.last_run_at)+" · "+(j.last_status||"?"):"—"),
          el("span","mono",j.model||"—")]);})));
    } else if(jobs) jc.appendChild(el("div","empty","Keine Jobs."));
    root.appendChild(jc);}
}

function getJSON(url){return fetch(url,{credentials:"same-origin",headers:{"Accept":"application/json"}})
 .then(function(r){if(!r.ok)throw new Error(r.status+" "+r.statusText);return r.json();});}
function load(){
  var p=getJSON("/ledger/overview");
  if(jobsOpen) p=Promise.all([p,getJSON("/ledger/jobs").catch(function(e){
    return {jobs:[],count:0,errors:{"jobs":String(e)}};})]).then(function(r){jobs=r[1];return r[0];});
  p.then(render).catch(function(e){
    root.textContent="";
    var b=el("div","err");b.appendChild(el("strong",null,"Abruf fehlgeschlagen: "));
    b.appendChild(document.createTextNode(String(e)));root.appendChild(b);});
}
load();
timer=setInterval(function(){if(!document.hidden)load();},POLL*1000);
})();
