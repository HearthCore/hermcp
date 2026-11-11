"""Run watcher: turns the state of runs started via `chat_send` into inbox events.

Runs inside the hermcp service (no Hermes hook, no gateway change, no token: it writes to the
store directly). It polls `GET /v1/runs/{id}` of the Hermes api_server for the runs registered in the
`watches` table and emits `needs_approval`, `run_finished`, `run_failed`, `run_cancelled`, `run_lost`.

Activity: the run status carries `last_event` (run.started, tool.started, tool.completed, reasoning.available,
subagent.*, approval.request, ...) and `updated_at`. That is enough for a coarse "what is it doing", so no Hermes
hook is needed. The watcher keeps ONE value per run (`activity`, `activity_at` in the watches table), mapped from a
fixed vocabulary: never tool arguments, never output, never gateway text, and no tool names (the status does not
carry them; only the SSE stream does). It is not an event: the inbox is only written on needs_approval and terminal states.

Rules: output text never goes into an event; error text is cleaned and capped at 200 chars;
events are deduplicated by `dedupe_key`; gateway trouble (unreachable, 5xx) only backs off and
never produces `run_lost` -- that needs repeated 404s from a gateway that otherwise answers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from . import hapi
from .inbox_store import clean_text, get_inbox, run_approval_key

log = logging.getLogger("hermcp.watcher")

Fetch = Callable[..., Any]   # injectable HTTP fetch (tests pass a fake)

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
MAX_ERROR = 200
LOST_AFTER_404S = 3          # consecutive 404s ...
LOST_MIN_SPAN = 5.0          # ... spread over at least this many seconds
MAX_BACKOFF = 60.0
ACTIVITY_REFRESH = 10.0       # re-write an unchanged activity at most this often (seconds of gateway time)

# gateway `last_event` -> label; anything unknown is "working" (the raw string is never copied)
ACTIVITY = {"run.queued": "queued", "run.started": "started", "tool.started": "running a tool",
            "tool.completed": "between tools", "reasoning.available": "thinking",
            "subagent.start": "delegating to a subagent", "subagent.complete": "subagent finished",
            "approval.request": "waiting for approval", "run.stopping": "stopping",
            "run.steered": "steered (guidance injected)", "approval.responded": "approval answered"}
STATUS_ACTIVITY = {"queued": "queued", "waiting_for_approval": "waiting for approval", "stopping": "stopping",
                   "completed": "finished", "failed": "failed", "cancelled": "cancelled", "interrupted": "interrupted"}


def activity_label(status: str, last_event: Any) -> str:
    """Fixed-vocabulary activity for a run status. Terminal states, queued and approval win over last_event."""
    if status in STATUS_ACTIVITY:
        return STATUS_ACTIVITY[status]
    return ACTIVITY.get(last_event, "working") if isinstance(last_event, str) else "working"


class RunWatcher:
    def __init__(self, settings, fetch: Fetch | None = None, clock: Callable[[], float] = time.time):
        self.settings, self.clock = settings, clock
        self.fetch: Fetch = fetch or self._http_fetch
        self._gw_fail, self._gw_next = 0, 0.0
        self._next: dict[str, float] = {}
        self._nf: dict[str, tuple[int, float]] = {}      # run_id -> (consecutive 404s, first at)
        self._act: dict[str, tuple[str, float]] = {}     # run_id -> (activity label, gateway time last written)

    def _http_fetch(self, profile: str, run_id: str):
        return hapi.request_status(self.settings, "GET", f"/v1/runs/{run_id}", profile=profile, timeout=5.0)

    # -- one pass (synchronous; the loop runs it in a thread) ----------------------------
    def tick(self) -> int:
        now = self.clock()
        if now < self._gw_next:
            return 0
        box, emitted = get_inbox(self.settings), 0
        for w in box.active_watches(now):
            rid = w["run_id"]
            if self._next.get(rid, 0.0) > now:
                continue
            code, body = self.fetch(w["profile"], rid)
            if code is None or code >= 500:                       # gateway down/restarting: back off, no verdict
                self._gw_fail += 1
                delay = min(self.settings.inbox_watch_interval * 2 ** self._gw_fail, MAX_BACKOFF)
                self._gw_next = now + delay
                if self._gw_fail in (1, 5, 20):
                    log.warning("run watcher: gateway not answering (%s); backing off %.0fs", code or "unreachable", delay)
                break
            self._gw_fail = 0
            if code in (401, 403):                                # our credentials: slow retry, never a verdict
                self._next[rid] = now + MAX_BACKOFF
                continue
            if code == 404:
                n, first = self._nf.get(rid, (0, now))
                n += 1
                self._nf[rid] = (n, first)
                if n >= LOST_AFTER_404S and now - first >= LOST_MIN_SPAN:
                    emitted += self._emit(box, w, "run_lost", "Run is no longer known to the gateway "
                                          "(status expired or the gateway lost it).", "run_lost", "warning")
                    self._close_approvals(box, w, "expired", "the run was lost by the gateway")
                    self._finish(box, w, "lost")
                else:
                    self._next[rid] = now + self.settings.inbox_watch_interval * n
                continue
            if code != 200 or not isinstance(body, dict):
                self._next[rid] = now + 30.0
                continue
            self._nf.pop(rid, None)
            emitted += self._handle(box, w, body)
        if emitted:                     # a run just ended: settle the failures its session moved past
            box.settle_failures()
        return emitted

    # -- state machine -------------------------------------------------------------------
    def _handle(self, box, w: dict, st: dict) -> int:
        status = str(st.get("status") or "")
        sid = st.get("session_id") if isinstance(st.get("session_id"), str) else None
        box.update_watch(w["run_id"], last_status=status, session_id=sid)
        self._note_activity(box, w["run_id"], status, st)
        if status == "waiting_for_approval":
            ap = st.get("approval") if isinstance(st.get("approval"), dict) else {}
            action, why = str(ap.get("command") or ""), str(ap.get("description") or "")
            rid = ap.get("request_id") if isinstance(ap.get("request_id"), str) else None
            # One approval row, not an event: it stays visible until the run ends or the approval settles.
            key = run_approval_key(w["run_id"], rid, action, why)
            box.open_approval(
                approval_key=key, origin="watcher", run_id=w["run_id"], session_id=sid,
                profile=w["profile"], requester=w["key_name"], approval_id=rid,
                action=action or None, rationale=why or None, surface="run",
                summary="Run is waiting for an approval. Read the pending request on the run itself "
                        "(chat_run_status) before answering.")
            return 0
        if status == "completed":
            out = st.get("output")
            n = len(out) if isinstance(out, str) else 0
            e = self._emit(box, w, "run_finished", f"Run finished ({n} characters of output). "
                           "Read it with chat_run_status.", "run_finished", "info", session_id=sid)
            self._close_approvals(box, w, "expired", "the run finished without the approval being answered")
            self._finish(box, w, status)
            return e
        if status in ("failed", "interrupted", "cancelled"):
            err = clean_text(str(st.get("error") or ""))[:MAX_ERROR]
            # hermcp's own control note (chat_run_cancel, chat_send mode=interrupt): fixed text, never gateway text
            why = f" ({w['control']})" if w.get("control") else ""
            out = st.get("output")
            produced = len(out) if isinstance(out, str) else 0
            if status == "cancelled":
                e = self._emit(box, w, "run_cancelled", f"Run was cancelled{why}.", "run_cancelled", "info",
                               session_id=sid)
            elif status == "interrupted" and produced:
                # Its own class, not a real failure: the gateway only marks a run `interrupted` when it was
                # stopped by a gateway/process shutdown, and this one had already produced output. It stays
                # in the history (inbox_list) and out of the attention view.
                e = self._emit(box, w, "run_interrupted",
                               f"Run was interrupted by a restart{why} after producing {produced} characters of "
                               "output — read it with chat_run_status.", "run_interrupted", "warning",
                               session_id=sid)
            else:
                head = "Run was interrupted" if status == "interrupted" else "Run failed"
                if status == "interrupted":
                    head += " before it produced any output"
                # keep the whole summary within the documented error budget (prefix + 200 chars)
                err = err[:MAX_ERROR - max(0, len(head) - len("Run failed"))]
                e = self._emit(box, w, "run_failed", f"{head}: {err}" if err else f"{head}.", "run_failed", "error",
                               session_id=sid)
            self._close_approvals(box, w, "expired", f"the run ended ({status}) without the approval being answered")
            self._finish(box, w, status)
            return e
        return 0

    def _close_approvals(self, box, w: dict, state: str, note: str) -> None:
        """Teil 1: a run that is over cannot still be waiting for an approval — close its open rows."""
        for key in box.close_run_approvals(w["run_id"], state=state, note=note):
            log.info("run watcher: closed approval %s of run %s (%s)", key, w["run_id"], state)

    def _note_activity(self, box, run_id: str, status: str, st: dict) -> None:
        """One updated value per run. Writes only on change (or every ACTIVITY_REFRESH s of gateway time)."""
        label = activity_label(status, st.get("last_event"))
        at = st.get("updated_at") if isinstance(st.get("updated_at"), (int, float)) else None
        prev = self._act.get(run_id)
        if prev and prev[0] == label and (at is None or at - prev[1] < ACTIVITY_REFRESH):
            return
        box.set_activity(run_id, label, at)
        self._act[run_id] = (label, at if at is not None else 0.0)

    def _emit(self, box, w: dict, kind: str, summary: str, key: str, severity: str, **extra) -> int:
        if extra.get("session_id") is None:
            extra["session_id"] = w.get("session_id")
        ev = box.push_system(kind=kind, summary=summary, run_id=w["run_id"], profile=w["profile"],
                             requester=w["key_name"], dedupe_key=f"{w['run_id']}:{key}", severity=severity, **extra)
        return 1 if ev else 0

    def _finish(self, box, w: dict, status: str) -> None:
        box.update_watch(w["run_id"], last_status=status, done=True)
        self._next.pop(w["run_id"], None)
        self._nf.pop(w["run_id"], None)
        self._act.pop(w["run_id"], None)

    # -- service loop --------------------------------------------------------------------
    async def run(self) -> None:
        log.info("run watcher started (interval %.1fs)", self.settings.inbox_watch_interval)
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the watcher must never die
                log.exception("run watcher tick failed")
                await asyncio.sleep(5)
            await asyncio.sleep(self.settings.inbox_watch_interval)
