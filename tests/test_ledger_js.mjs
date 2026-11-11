// The Ledger page's dismiss logic, tested without a browser.
//
// app.js is loaded with a stub `window` (no `document`), which makes it export its
// pure helpers and skip rendering.
//
//   node --test tests/test_ledger_js.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import path from "node:path";

const SOURCE = readFileSync(path.join(import.meta.dirname, "..", "ledger_assets", "app.js"), "utf8");

function load({ localStorage } = {}) {
  const win = {};
  if (localStorage !== undefined) Object.defineProperty(win, "localStorage", { get: () => localStorage });
  const sandbox = { window: win, console };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(SOURCE.replace("__POLL__", "15"), sandbox);
  return sandbox.__LEDGER_HELPERS__;
}

// values come from a vm realm: normalize before a deep comparison
const j = (value) => JSON.parse(JSON.stringify(value));

function memoryStorage(initial) {
  const data = { ...(initial || {}) };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
}

const CRON = { run_id: "run_a", job_id: "j1", started_at: "2026-11-10T06:00:00+01:00" };
const CHAT = { run_id: "run_b", session_id: "run_b", at: "2026-11-10T05:00:00+01:00" };

test("ids come from the run id, not from anything personal", () => {
  const H = load();
  assert.equal(H.idOf(CRON), "run_a");
  assert.equal(H.idOf(CHAT), "run_b");
  assert.equal(H.idOf({ session_id: "s1" }), "s1");
  assert.equal(H.idOf({}), "");
  assert.equal(H.idOf(null), "");
});

test("dismiss state is per browser and survives a reload", () => {
  const storage = memoryStorage();
  const H = load({ localStorage: storage });
  assert.deepEqual(j(H.loadDismissed(storage)), {});
  assert.equal(H.saveDismissed(storage, { run_a: 1 }), true);
  assert.deepEqual(j(H.loadDismissed(storage)), { run_a: 1 });
  assert.equal(storage.data[H.DISMISS_KEY], JSON.stringify({ run_a: 1 }));
});

test("a broken or hostile stored value is ignored, not fatal", () => {
  const H = load();
  const broken = memoryStorage({ [H.DISMISS_KEY]: "{not json" });
  assert.deepEqual(j(H.loadDismissed(broken)), {});
  const array = memoryStorage({ [H.DISMISS_KEY]: '["run_a"]' });
  assert.deepEqual(j(H.loadDismissed(array)), {});
  const nullish = memoryStorage({ [H.DISMISS_KEY]: "null" });
  assert.deepEqual(j(H.loadDismissed(nullish)), {});
});

test("no localStorage: helpers degrade instead of throwing", () => {
  const H = load();                                     // window without localStorage
  assert.equal(H.store({}), null);                      // property access throws -> null
  assert.deepEqual(j(H.loadDismissed(null)), {});
  assert.equal(H.saveDismissed(null, { run_a: 1 }), false);

  const throwing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); },
                     removeItem() {} };
  assert.equal(H.store({ localStorage: throwing }), null);
});

test("prune keeps only ids that are still in the window", () => {
  const H = load();
  const kept = H.pruneDismissed({ run_a: 1, run_old: 1, run_b: 1 }, ["run_a", "run_b", "run_c"]);
  assert.deepEqual(j(Object.keys(kept).sort()), ["run_a", "run_b"]);
  assert.deepEqual(j(H.pruneDismissed({}, [])), {});
  assert.deepEqual(j(H.pruneDismissed({ x: 1 }, [])), {});
});

test("failures split into visible and hidden by id", () => {
  const H = load();
  const split = H.splitFailures([CRON, CHAT], { run_a: 1 });
  assert.deepEqual(j(split.active.map((r) => r.run_id)), ["run_b"]);
  assert.deepEqual(j(split.hidden.map((r) => r.run_id)), ["run_a"]);
  assert.deepEqual(j(H.splitFailures([], {})), { active: [], hidden: [] });
});

test("badges count approvals plus undismissed failures only", () => {
  const H = load();
  const att = {
    approvals: { count: 2 },
    failed_cron_runs: [CRON, { run_id: "run_c" }],
    failed_chat_runs: [CHAT],
  };
  assert.deepEqual(j(H.attentionCount(att, {})), { approvals: 2, cron: 2, chat: 1, failed: 3, total: 5 });
  assert.deepEqual(j(H.attentionCount(att, { run_a: 1, run_b: 1 })), { approvals: 2, cron: 1, chat: 0, failed: 3, total: 3 });
  // approvals are actionable: dismissing failures never removes them from the count
  assert.equal(H.attentionCount(att, { run_a: 1, run_c: 1, run_b: 1 }).total, 2);
  assert.deepEqual(j(H.attentionCount({}, {})), { approvals: 0, cron: 0, chat: 0, failed: 0, total: 0 });
});

test("the header numbers come from the server digest, not from the browser", () => {
  const H = load();
  // hermes_status says 4 open failures; this browser dismissed one of the two listed rows.
  // The page must show the server's number (4), never re-add a settled row or double-count.
  const att = {
    digest: { failed_runs_24h: 4, approvals_waiting: 1 },
    approvals: { count: 9 },                       // must be ignored in favour of the digest
    failed_cron_runs: [CRON],
    failed_chat_runs: [CHAT],
    settled: [{ run_id: "run_settled", settled_reason: "superseded_by_completed" }],
  };
  const c = H.attentionCount(att, { run_a: 1 });
  assert.equal(c.failed, 4);
  assert.equal(c.approvals, 1);
  assert.equal(c.total, 1 + 0 + 1);                // approvals + visible cron (one dismissed) + chat
});

test("the rest control names how many rows the server is holding back", () => {
  const H = load();
  assert.equal(H.moreLabel({ has_more: true, more_count: 15, expanded: false }), "Mehr anzeigen (15 weitere)");
  assert.equal(H.moreLabel({ has_more: true, more_count: 1, expanded: false }), "Mehr anzeigen (1 weitere)");
  // nothing held back, or already expanded: no control
  assert.equal(H.moreLabel({ has_more: false, more_count: 0 }), "");
  assert.equal(H.moreLabel({ has_more: true, more_count: 4, expanded: true }), "");
  assert.equal(H.moreLabel(null), "");
  assert.equal(H.moreLabel({}), "");
});

test("superseded runs are not part of the failure list the badge sees", () => {
  const H = load();
  // the attention payload carries the open failure lists only
  const att = { approvals: { count: 0 }, failed_cron_runs: [CRON], failed_chat_runs: [] };
  assert.equal(H.attentionCount(att, {}).total, 1);
  assert.equal(H.attentionCount(att, { run_a: 1 }).total, 0);
});

// ---------------------------------------------------------------------------
// routing, pop-up URLs and the list query
// ---------------------------------------------------------------------------

test("the route is read from the path, with a safe fallback", () => {
  const H = load();
  assert.deepEqual(j(H.pageOf("/ledger")), { page: "attention", id: "" });
  assert.deepEqual(j(H.pageOf("/ledger/")), { page: "attention", id: "" });
  assert.deepEqual(j(H.pageOf("/ledger/sessions")), { page: "sessions", id: "" });
  assert.deepEqual(j(H.pageOf("/ledger/runs")), { page: "runs", id: "" });
  assert.deepEqual(j(H.pageOf("/ledger/cron")), { page: "cron", id: "" });
  assert.deepEqual(j(H.pageOf("/ledger/gateway")), { page: "gateway", id: "" });
  assert.deepEqual(j(H.pageOf("/ledger/session/local-abc")), { page: "session", id: "local-abc" });
  // an unknown path never becomes a page of its own
  assert.equal(H.pageOf("/ledger/whatever").page, "attention");
  assert.equal(H.pageOf("").page, "attention");
});

test("the session path encodes the id and refuses an empty one", () => {
  const H = load();
  assert.equal(H.sessionPath("local-abc"), "/ledger/session/local-abc");
  assert.equal(H.sessionPath("a b"), "/ledger/session/a%20b");
  assert.equal(H.sessionPath(""), "");
});

test("the peek url carries kind, id and only the given options", () => {
  const H = load();
  assert.equal(H.peekUrl("session", "local-1"),
    "/ledger/api/peek?kind=session&id=local-1");
  assert.equal(H.peekUrl("cron", "r1", { profile: "default", job: "j1" }),
    "/ledger/api/peek?kind=cron&id=r1&profile=default&job=j1");
  // a missing kind falls back, values are always encoded
  assert.ok(H.peekUrl("", "a&b").startsWith("/ledger/api/peek?kind=session&id="));
  assert.ok(H.peekUrl("run", "a&b").includes("a%26b"));
});

test("the list url drops empty filters and keeps the fixed key order", () => {
  const H = load();
  assert.equal(H.listUrl("sessions", {}), "/ledger/api/list?kind=sessions");
  assert.equal(H.listUrl("runs", { status: "settled", q: "", profile: "default", offset: 25 }),
    "/ledger/api/list?kind=runs&status=settled&profile=default&offset=25");
  // keys outside the whitelist never reach the query
  assert.equal(H.listUrl("cron", { evil: "x" }), "/ledger/api/list?kind=cron");
});

test("truncate cuts at the budget and marks it, and never invents text", () => {
  const H = load();
  assert.equal(H.truncate("abcdef", 4), "abcd…");
  assert.equal(H.truncate("abc", 10), "abc");
  assert.equal(H.truncate(null, 3), "");
  assert.equal(H.truncate(undefined, 3), "");
  assert.equal(H.truncate("abc"), "abc");
});

test("symbols and tones stay consistent between the pages", () => {
  const H = load();
  assert.equal(H.symbolFor("ok"), "●");
  assert.equal(H.symbolFor("bad"), "✕");
  assert.equal(H.symbolFor("run"), "◌");
  assert.equal(H.symbolFor("warn"), "▲");
  assert.equal(H.symbolFor("muted"), "○");
  assert.equal(H.tone("aktiv"), "ok");
  assert.equal(H.tone("waiting"), "warn");
  assert.equal(H.tone("failed"), "bad");
  assert.equal(H.tone("irgendwas"), "muted");
});

test("the nav is the fixed order the design asks for", () => {
  const H = load();
  assert.deepEqual(j(H.NAV.map((n) => n.key)), ["attention", "sessions", "runs", "cron", "gateway"]);
  assert.deepEqual(j(H.NAV.map((n) => n.label)),
    ["~/attention", "~/sessions", "~/runs", "~/cron", "~/gateway"]);
  H.NAV.forEach((n) => assert.ok(n.icon, n.key));
});

test("an empty attention area draws no panel at all", () => {
  const H = load();
  // nothing waiting, no open failure, nothing dismissed, no interruption
  assert.equal(H.attentionEmpty({ waiting: 0, cron: 0, chat: 0, hidden: 0, interrupted: 0 }), true);
  assert.equal(H.attentionEmpty({}), true);
  // every single reason keeps the panel (the "Clear errors" control must stay reachable)
  assert.equal(H.attentionEmpty({ waiting: 1 }), false);
  assert.equal(H.attentionEmpty({ cron: 1 }), false);
  assert.equal(H.attentionEmpty({ chat: 1 }), false);
  assert.equal(H.attentionEmpty({ hidden: 2 }), false);
  assert.equal(H.attentionEmpty({ interrupted: 1 }), false);
});

test("the Ask-Claude url and its hidden cases", () => {
  const H = load();
  // web wins, desktop is the fallback the server only sends when configured
  assert.equal(H.askUrl({ web: "https://claude.ai/new?q=hi", desktop: null }), "https://claude.ai/new?q=hi");
  assert.equal(H.askUrl({ web: null, desktop: "claude://x?q=hi" }), "claude://x?q=hi");
  assert.equal(H.askUrl(null), "");
  assert.equal(H.askUrl({}), "");
  // no URL: no button (never a dead link) — this path touches no DOM
  assert.deepEqual(j(H.askButtons({ web: null, desktop: null })), []);
  assert.deepEqual(j(H.askButtons(null)), []);
  assert.deepEqual(j(H.askButtons({})), []);
});

// ---------------------------------------------------------------------------
// URL state: read/build/merge + the peek token
// ---------------------------------------------------------------------------

test("the peek token round-trips and rejects anything else", () => {
  const H = load();
  assert.equal(H.buildPeek("session", "local-abc"), "session:local-abc");
  assert.equal(H.buildPeek("approval", "ap-1"), "approval:ap-1");
  assert.deepEqual(j(H.parsePeek("session:local-abc")), { kind: "session", id: "local-abc" });
  assert.deepEqual(j(H.parsePeek("cron:run_1:x")), { kind: "cron", id: "run_1:x" });  // ids may contain ':'
  // unknown kind, empty id, missing colon, path tricks: no peek at all
  ["secrets:abc", "session:", ":abc", "session", "", null,
   "session:../../etc/passwd"].forEach((v) => assert.equal(H.parsePeek(v), null, String(v)));
  assert.equal(H.buildPeek("nope", "x"), "");
});

test("the query is read with a whitelist and defaults", () => {
  const H = load();
  const st = H.readUrlState("?peek=session%3Alocal-1&status=aktiv&profile=default&offset=25&more=runs,cron&jobs=1");
  assert.deepEqual(j(st.peek), { kind: "session", id: "local-1" });
  assert.equal(st.filters.status, "aktiv");
  assert.equal(st.filters.profile, "default");
  assert.equal(st.offset, 25);
  assert.deepEqual(j(st.more), ["runs", "cron"]);
  assert.equal(st.jobs, true);
  // unknown keys, bad offsets and junk are ignored, never trusted
  const junk = H.readUrlState("?evil=1&offset=abc&more=../x&peek=bogus");
  assert.deepEqual(j(junk.filters), {});
  assert.equal(junk.offset, 0);
  assert.deepEqual(j(junk.more), []);
  assert.equal(junk.peek, null);
  assert.deepEqual(j(H.readUrlState("")), { filters: {}, offset: 0, more: [], peek: null, jobs: false });
});

test("state builds a stable, encoded query", () => {
  const H = load();
  assert.equal(H.buildSearch({ filters: {}, offset: 0, more: [], peek: null, jobs: false }), "");
  assert.equal(H.buildSearch({ peek: { kind: "session", id: "a:b" }, filters: {}, offset: 0, more: [], jobs: false }),
    "?peek=session%3Aa%3Ab");
  // an id outside the charset is not put into a URL at all
  assert.equal(H.buildSearch({ peek: { kind: "session", id: "a b" }, filters: {}, offset: 0, more: [], jobs: false }), "");
  const q = H.buildSearch({ filters: { status: "aktiv", q: "böse & <x>" }, offset: 50,
                            more: ["runs"], peek: null, jobs: true });
  assert.ok(q.indexOf("status=aktiv") > 0);
  assert.ok(q.indexOf("offset=50") > 0);
  assert.ok(q.indexOf("more=runs") > 0);
  assert.ok(q.indexOf("jobs=1") > 0);
  assert.ok(q.indexOf("%26") > 0 && q.indexOf("<") < 0);       // encoded, never raw markup
  // key order is fixed, so the same view is always the same URL
  const again = H.buildSearch(H.readUrlState(q));
  assert.equal(again, q);
});

test("mergeUrlState never mutates its input and clears values on null", () => {
  const H = load();
  const base = { filters: { status: "aktiv", profile: "default" }, offset: 25, more: ["runs"],
                 peek: { kind: "run", id: "r1" }, jobs: true };
  const out = H.mergeUrlState(base, { filters: { status: null, q: "abc" }, offset: 0, peek: null });
  assert.equal(out.filters.profile, "default");
  assert.equal(out.filters.status, undefined);
  assert.equal(out.filters.q, "abc");
  assert.equal(out.offset, 0);
  assert.equal(out.peek, null);
  assert.equal(out.jobs, true);
  // the base is untouched
  assert.equal(base.filters.status, "aktiv");
  assert.equal(base.peek.kind, "run");
  assert.deepEqual(j(base.more), ["runs"]);
});

test("a full roundtrip keeps the view identical", () => {
  const H = load();
  const view = { filters: { status: "settled", profile: "developer", hours: "168", q: "x y" },
                 offset: 75, more: ["sessions", "runs"], peek: { kind: "approval", id: "ap-1" }, jobs: true };
  const back = H.readUrlState(H.buildSearch(view));
  assert.equal(back.offset, 75);
  assert.equal(back.filters.status, "settled");
  assert.equal(back.filters.q, "x y");
  assert.deepEqual(j(back.more), ["sessions", "runs"]);
  assert.deepEqual(j(back.peek), { kind: "approval", id: "ap-1" });
  assert.equal(back.jobs, true);
  assert.equal(H.buildSearch(back), H.buildSearch(view));
});
