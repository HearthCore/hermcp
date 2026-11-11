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

test("settled failures and superseded cron runs form one done group", () => {
  const H = load();
  const items = H.doneItems({
    settled: [{ run_id: "run_s", at: "2026-11-11T10:00:00+01:00", profile: "default",
                summary: "Run failed", settled_text: "ersetzt durch einen Lauf, der abgeschlossen wurde",
                superseded_by: "run_after", url: "https://x/#/run_after" }],
    superseded_cron_runs: [{ run_id: "run_c", job_id: "j1", job_name: "relay",
                             started_at: "2026-11-11T09:00:00+01:00", error: "timeout",
                             ok_after: "2026-11-11T10:30:00+01:00" }],
  });
  assert.equal(items.length, 2);
  assert.equal(items[0].kind, "chat");
  assert.equal(items[0].superseded_by, "run_after");
  assert.ok(items[0].note.includes("abgeschlossen"));
  assert.equal(items[1].kind, "cron");
  assert.ok(items[1].note.includes("wieder ok"));
  assert.deepEqual(j(H.doneItems({})), []);
  assert.deepEqual(j(H.doneItems(null)), []);
});

test("superseded runs are not part of the failure list the badge sees", () => {
  const H = load();
  // the page splits two lists: att.failed_cron_runs (active) and att.superseded_cron_runs
  const att = { approvals: { count: 0 }, failed_cron_runs: [CRON], failed_chat_runs: [] };
  assert.equal(H.attentionCount(att, {}).total, 1);
  assert.equal(H.attentionCount(att, { run_a: 1 }).total, 0);
});
