#!/usr/bin/env python3
"""astra-world runner: keeps Astra playing while the world is up.

Design: vault agents/rs-sdk-jev/docs/runner/RUNNER-DESIGN-2026-11-07.md (v3).

Always on, in the `runner` service (shares the player container's network, so the
bridge is on 127.0.0.1:7790). It never acts in the game itself. It:

  * plays cards: takes one card from <harness>/cards/, starts ONE harness session
    for it as a fresh process (Astra's astra_loop.py), and files the result;
  * enforces STOP: no new session while <owner>/STOP exists, and a running one is
    stopped in phases (SIGTERM -> 10 s -> one POST /cancel -> 5 s -> SIGKILL);
  * treats a bridge stop_failure as final ("needs Ryan"): starts nothing, never retries;
  * mirrors what the bridge sees into <harness>/world/ (read-only GETs only);
  * answers screenshot requests from <harness>/requests/ via GET /screenshot;
  * reports everything as one JSON file per event in <harness>/inbox/.

It writes only: cards/active|done|failed, the top level of inbox/, status.json,
world/, and deletes handled requests/. It never writes reports/, inbox/read/,
cards/queue/ (beyond taking a card out) or any of Astra's code.

Standard library only (plus rsworld from astra-world/kit). Python 3.11+.
"""
from __future__ import annotations

import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rsworld.bridge_client import BridgeClient, BridgeUnavailable
from rsworld.contracts import validate_limits

DECIDERS = ("first", "random", "jev")
DEFAULT_DECIDER = "first"           # never `random` on a real character by default
EXIT_INVALID = 2                    # astra_loop: limits or goal invalid
EXIT_BRIDGE = 3                     # astra_loop: bridge unavailable
OUTPUT_TAIL_LINES = 40
SCREENSHOT_KEEP = 200
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_SECRET_PATTERNS = (
    re.compile(r"(password=)[^&\s\"'<>]+", re.IGNORECASE),
    re.compile(r"(PASSWORD\s*[=:]\s*)\S+"),
    re.compile(r"(x-bridge-token\s*[=:]\s*)\S+", re.IGNORECASE),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scrub(text: str) -> str:
    """Remove passwords/tokens from any text copied into Astra's folder."""
    for p in _SECRET_PATTERNS:
        text = p.sub(r"\1<redacted>", text)
    return text


def _scrub_obj(v: Any) -> Any:
    if isinstance(v, str):
        return scrub(v)
    if isinstance(v, dict):
        return {k: _scrub_obj(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub_obj(x) for x in v]
    return v


def write_atomic(path: Path, data: bytes | str) -> None:
    """Write to a dotfile beside the target, then rename: a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_bytes(data.encode() if isinstance(data, str) else data)
    os.replace(tmp, path)


def write_json(path: Path, obj: Any) -> None:
    write_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


@dataclass
class Config:
    harness: Path = Path(os.environ.get("ASTRA_HARNESS", "/harness"))
    owner: Path = Path(os.environ.get("ASTRA_OWNER", "/owner"))
    harness_cmd: list[str] = field(default_factory=lambda: [sys.executable, "/harness/astra_loop.py"])
    session_log_dir: Path = Path(os.environ.get("RUNNER_LOG_DIR", "/tmp/runner"))  # inside the container, not her folder
    poll_s: float = 1.0
    mirror_every_s: float = 5.0
    term_grace_s: float = 10.0
    cancel_grace_s: float = 5.0
    bridge_retry_s: float = 10.0          # minimum wait before replaying a card after the bridge dropped
    world_unavailable_after_s: float = 30.0
    screenshot_every_s: float = 10.0
    bad_request_after_s: float = 30.0


@dataclass
class Session:
    card_id: str
    card_path: Path
    proc: subprocess.Popen
    log_path: Path
    started: float
    session_tag: str
    stop_reason_runner: str | None = None   # "stop" | "shutdown" when the runner ended it
    term_at: float | None = None
    cancel_at: float | None = None
    killed: bool = False


class Runner:
    def __init__(self, cfg: Config, bridge: BridgeClient | None = None, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.bridge = bridge or BridgeClient(timeout_s=8.0)
        self.clock = clock
        h = cfg.harness
        self.queue, self.active = h / "cards" / "queue", h / "cards" / "active"
        self.done, self.failed = h / "cards" / "done", h / "cards" / "failed"
        self.inbox, self.world, self.requests = h / "inbox", h / "world", h / "requests"
        self.reports = h / "reports"
        self.stop_path, self.limits_path = cfg.owner / "STOP", cfg.owner / "limits.json"
        self.session: Session | None = None
        self.needs_ryan: dict | None = None
        self.shutting_down = False
        self._seq = 0
        self._once: set[str] = set()          # events written once until the condition clears
        self._last_mirror = -1e9
        self._verbs_written = False
        self._unreachable_since: float | None = None
        self._last_health: dict | None = None
        self._last_screenshot = -1e9
        self._bridge_retry_until = 0.0
        self._status: dict = {}

    # ---------------------------------------------------------------- files ---
    def ensure_dirs(self) -> None:
        for d in (self.queue, self.active, self.done, self.failed, self.inbox, self.world, self.requests,
                  self.cfg.session_log_dir):
            d.mkdir(parents=True, exist_ok=True)

    def event(self, kind: str, card_id: str | None = None, session_id: str | None = None, **facts: Any) -> None:
        self._seq = (self._seq + 1) % 10000
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        body = {"event": kind, "at": utc_now(), "card_id": card_id, "session_id": session_id,
                "facts": _scrub_obj(facts)}
        write_json(self.inbox / f"{stamp}-{self._seq:04d}-{kind}.json", body)
        print(f"[runner] event {kind} {card_id or ''} {json.dumps(_scrub_obj(facts))[:300]}", flush=True)

    def event_once(self, key: str, kind: str, **kw: Any) -> None:
        if key not in self._once:
            self._once.add(key)
            self.event(kind, **kw)

    def clear_once(self, *keys: str) -> None:
        self._once.difference_update(keys)

    def status(self, state: str, **extra: Any) -> None:
        body = {"state": state, **_scrub_obj(extra)}
        if body != self._status:
            self._status = body
            write_json(self.cfg.harness / "status.json", {**body, "at": utc_now()})

    # --------------------------------------------------------------- bridge ---
    def health(self) -> dict | None:
        try:
            h = self.bridge.health()
        except BridgeUnavailable:
            return None
        self._last_health = h
        return h

    def mirror(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_mirror < self.cfg.mirror_every_s:
            return
        self._last_mirror = now
        h = self.health()
        if h is None:
            write_json(self.world / "health.json", {"reachable": False, "at": utc_now()})
            if self._unreachable_since is None:
                self._unreachable_since = now
            elif now - self._unreachable_since >= self.cfg.world_unavailable_after_s:
                self.event_once("world_unavailable", "world_unavailable")
            return
        self._unreachable_since = None
        self.clear_once("world_unavailable")
        write_json(self.world / "health.json", {"reachable": True, **h, "at": utc_now()})
        try:
            status, body = self.bridge.get_raw("/observe?summary=1")
            if status == 200:
                write_json(self.world / "observe.json", _scrub_obj(body))
                write_atomic(self.world / "summary.txt", scrub(str(body.get("summary") or "")) + "\n")
            if not self._verbs_written:
                vs, vb = self.bridge.get_raw("/verbs")
                if vs == 200:
                    write_json(self.world / "verbs.json", vb)
                    self._verbs_written = True
        except BridgeUnavailable:
            pass

    def check_stop_failure(self, h: dict | None) -> bool:
        """True if the bridge reports an unconfirmed stop. Latches until /health is clean."""
        if h is not None and h.get("stop_failure"):
            if self.needs_ryan is None:
                self.needs_ryan = {"stop": h.get("stop_failure")}
                self.event("needs_ryan", card_id=self.session.card_id if self.session else self._active_card_id(),
                           reason="bridge stop_unconfirmed: inspect the game, then `docker compose -f "
                                  "astra-world/compose.yaml restart player`",
                           stop=h.get("stop_failure"))
            return True
        if h is not None and self.needs_ryan is not None:
            self.needs_ryan = None          # player was restarted and the bridge is clean again
        return False

    # ----------------------------------------------------------------- cards ---
    def _cards(self, d: Path) -> list[Path]:
        return sorted(p for p in d.glob("*.json") if not p.name.startswith(".") and p.is_file())

    def _active_card_id(self) -> str | None:
        a = self._cards(self.active)
        return a[0].stem if a else None

    def limits_problems(self) -> list[str]:
        try:
            limits = json.loads(self.limits_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [f"{self.limits_path} does not exist (copy astra-world/config/limits.example.json)"]
        except (OSError, json.JSONDecodeError) as e:
            return [f"{self.limits_path} unreadable: {e}"]
        return validate_limits(limits)

    def decider(self) -> str:
        try:
            d = json.loads((self.cfg.harness / "runner.json").read_text(encoding="utf-8")).get("decider")
        except (OSError, json.JSONDecodeError, AttributeError):
            return DEFAULT_DECIDER
        return d if d in DECIDERS else DEFAULT_DECIDER

    def next_card(self) -> Path | None:
        """A card already in active/ (interrupted earlier) is played first; then the queue in name order."""
        active = self._cards(self.active)
        if active:
            return active[0]
        for p in self._cards(self.queue):
            if not SAFE_NAME.match(p.stem):
                self.move(p, self.failed)
                self.event("card_rejected", card_id=p.stem[:60], output_tail="card file name must match "
                           "[A-Za-z0-9][A-Za-z0-9._-]*")
                continue
            dest = self.active / p.name
            os.replace(p, dest)
            return dest
        return None

    def move(self, card: Path, to: Path, outcome: dict | None = None) -> None:
        to.mkdir(parents=True, exist_ok=True)
        dest = to / card.name
        if dest.exists():   # replayed card with an older outcome: keep both
            dest = to / f"{card.stem}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        os.replace(card, dest)
        if outcome is not None:
            write_json(dest.with_suffix(".outcome.json"), _scrub_obj(outcome))

    # -------------------------------------------------------------- sessions ---
    def start_session(self, card: Path) -> None:
        card_id = card.stem
        decider = self.decider()
        out_dir = self.reports / card_id
        tag = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{card_id}"
        log_path = self.cfg.session_log_dir / f"{tag}.log"
        cmd = [*self.cfg.harness_cmd, "--goal", str(card), "--decider", decider, "--out", str(out_dir),
               "--limits", str(self.limits_path), "--stop-file", str(self.stop_path)]
        log = open(log_path, "wb")
        proc = subprocess.Popen(cmd, cwd=str(self.cfg.harness), stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        log.close()
        self.session = Session(card_id, card, proc, log_path, time.time(), tag)
        self.event("card_started", card_id=card_id, decider=decider, pid=proc.pid)
        self.status("playing", card_id=card_id, decider=decider, pid=proc.pid)

    def begin_stop(self, why: str) -> None:
        s = self.session
        if s is None or s.term_at is not None:
            return
        s.stop_reason_runner = why
        s.term_at = self.clock()
        print(f"[runner] stopping session for {s.card_id} ({why}): SIGTERM", flush=True)
        try:
            s.proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
        self.status("stopping", card_id=s.card_id, why=why)

    def drive_stop(self) -> None:
        s = self.session
        if s is None or s.term_at is None or s.proc.poll() is not None:
            return
        now = self.clock()
        if s.cancel_at is None and now - s.term_at >= self.cfg.term_grace_s:
            s.cancel_at = now
            print(f"[runner] {s.card_id}: still running after SIGTERM, POST /cancel (once)", flush=True)
            try:
                body = self.bridge.cancel()
            except BridgeUnavailable as e:
                body = {"error": "bridge_unavailable", "message": str(e)}
            if body.get("error") == "stop_unconfirmed":
                self.check_stop_failure({"stop_failure": body.get("stop") or body})
        elif s.cancel_at is not None and not s.killed and now - s.cancel_at >= self.cfg.cancel_grace_s:
            print(f"[runner] {s.card_id}: SIGKILL", flush=True)
            s.killed = True
            try:
                s.proc.kill()
            except ProcessLookupError:
                pass

    def _report(self, s: Session) -> tuple[Path | None, dict | None]:
        d = self.reports / s.card_id
        if not d.is_dir():
            return None, None
        cands = [p for p in d.glob("*.json") if not p.name.endswith(".meta.json") and not p.name.startswith(".")
                 and p.stat().st_mtime >= s.started - 1]
        # Newest first; a report is a JSON object with a session_id (other files may sit beside it).
        for p in sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(body, dict) and "session_id" in body:
                return p, body
        return None, None

    def _tail(self, s: Session) -> str:
        try:
            lines = s.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return scrub("\n".join(deque(lines, maxlen=OUTPUT_TAIL_LINES)))

    def finish_session(self) -> None:
        s = self.session
        assert s is not None
        code = s.proc.returncode
        self.session = None
        report_path, report = self._report(s)
        sr = str((report or {}).get("stop_reason") or "")
        rel = str(report_path.relative_to(self.cfg.harness)) if report_path else None
        common = {"exit_code": code, "stop_reason": sr or None, "report": rel, "session_tag": s.session_tag}
        sid = (report or {}).get("session_id")

        # 1. The runner's own state decides first.
        if s.stop_reason_runner is not None:
            # The harness is gone, but an action it started may still be running in the game
            # (found in the 2026-11-07 live test). Stop the character too: one /cancel, if busy.
            if s.cancel_at is None:
                h = self.health()
                if h is not None and h.get("busy"):
                    s.cancel_at = self.clock()
                    print(f"[runner] {s.card_id}: bridge still busy ({h.get('busy')}), POST /cancel (once)", flush=True)
                    try:
                        body = self.bridge.cancel()
                    except BridgeUnavailable as e:
                        body = {"error": "bridge_unavailable", "message": str(e)}
                    if body.get("error") == "stop_unconfirmed":
                        self.check_stop_failure({"stop_failure": body.get("stop") or body})
            # On shutdown, recover() reports it at the next start; don't report one interruption twice.
            if s.stop_reason_runner != "shutdown":
                self.event("card_interrupted", card_id=s.card_id, session_id=sid, reason=s.stop_reason_runner,
                           killed=s.killed, **common)
            return                                   # card stays in active/ and is played again first
        # 2. Exit codes.
        if code == EXIT_INVALID:
            self.move(s.card_path, self.failed, {"outcome": "rejected", **common})
            self.event("card_rejected", card_id=s.card_id, session_id=sid, output_tail=self._tail(s), **common)
            return
        if code == EXIT_BRIDGE or sr.startswith("bridge_unavailable"):
            if not self.check_stop_failure(self.health()):
                self._bridge_retry_until = self.clock() + self.cfg.bridge_retry_s
                self.event("card_interrupted", card_id=s.card_id, session_id=sid, reason="bridge_unavailable", **common)
            return                                   # stays in active/
        if code != 0 or report is None:
            self.move(s.card_path, self.failed, {"outcome": "failed", **common})
            self.event("card_failed", card_id=s.card_id, session_id=sid, output_tail=self._tail(s), **common)
            return
        # 3. A session that ended by itself: sort by her report.
        if sr.startswith("stop_file") or sr.startswith("sigterm"):
            self.event("card_interrupted", card_id=s.card_id, session_id=sid, reason="stop_file", **common)
            return
        if sr.startswith("goal_reached"):
            self.move(s.card_path, self.done, {"outcome": "goal_reached", **common})
            self.event("card_ended", card_id=s.card_id, session_id=sid, **common)
        elif sr.startswith("no_options"):
            self.move(s.card_path, self.done, {"outcome": "needs_astra", **common})
            self.event("card_needs_astra", card_id=s.card_id, session_id=sid, **common)
        else:
            self.move(s.card_path, self.done, {"outcome": "ended", **common})
            self.event("card_ended", card_id=s.card_id, session_id=sid, **common)

    # ------------------------------------------------------------ screenshots ---
    def handle_requests(self) -> None:
        now = self.clock()
        for req in sorted(self.requests.glob("*.json")):
            if req.name.startswith("."):
                continue
            rid = req.stem.removeprefix("screenshot-")
            if not SAFE_NAME.match(rid):
                rid = "bad-name"
            try:
                json.loads(req.read_text(encoding="utf-8") or "{}")
            except (OSError, json.JSONDecodeError):
                try:
                    age = time.time() - req.stat().st_mtime
                except OSError:
                    continue
                if age >= self.cfg.bad_request_after_s:
                    req.unlink(missing_ok=True)
                    self.event("screenshot_failed", id=rid, reason="request is not valid JSON")
                continue
            if now - self._last_screenshot < self.cfg.screenshot_every_s:
                return                              # rate limit: leave it for later
            self._last_screenshot = now
            self.take_screenshot(rid)
            req.unlink(missing_ok=True)

    def take_screenshot(self, rid: str) -> None:
        try:
            status, body = self.bridge.get_raw("/screenshot", timeout_s=20)  # bridge waits up to 10 s for the client
        except BridgeUnavailable as e:
            self.event("screenshot_failed", id=rid, reason=f"bridge unavailable: {e}")
            return
        b64 = body.get("png_base64") if status == 200 else None
        if not b64:
            reason = body.get("message") or body.get("error") or f"HTTP {status}"
            if status == 404:
                reason = "this bridge has no /screenshot route (rebuild the player image)"
            self.event("screenshot_failed", id=rid, reason=reason)
            return
        try:
            png = base64.b64decode(b64, validate=True)
        except ValueError:
            self.event("screenshot_failed", id=rid, reason="bridge returned invalid base64")
            return
        if not png.startswith(b"\x89PNG"):
            self.event("screenshot_failed", id=rid, reason="bridge returned something that is not a PNG")
            return
        shots = self.world / "screenshots"
        path = shots / f"{rid}.png"
        write_atomic(path, png)
        for old in sorted(shots.glob("*.png"), key=lambda p: p.stat().st_mtime)[:-SCREENSHOT_KEEP]:
            old.unlink(missing_ok=True)
        self.event("screenshot_ready", id=rid, tick=body.get("tick"),
                   path=str(path.relative_to(self.cfg.harness)), bytes=len(png))

    # ------------------------------------------------------------------ loop ---
    def recover(self) -> None:
        """At startup: a card left in active/ was interrupted by a restart."""
        for card in self._cards(self.active):
            self.event("card_interrupted", card_id=card.stem,
                       reason="runner restarted (docker compose down/up, restart or reboot); playing it again")

    def step(self) -> None:
        """One pass of the loop. Never raises for expected conditions."""
        self.mirror()
        if self.requests.is_dir():
            self.handle_requests()

        s = self.session
        if s is not None:
            if s.proc.poll() is not None:
                self.finish_session()
            else:
                if self.shutting_down:
                    self.begin_stop("shutdown")
                elif self.stop_path.exists():
                    self.begin_stop("stop")
                self.drive_stop()
                return

        if self.shutting_down:
            return
        if self.stop_path.exists():
            self.event_once("stop", "stopped_by_owner")
            self.status("stopped_by_owner")
            return
        self.clear_once("stop")

        h = self.health()
        if self.check_stop_failure(h):
            self.status("needs_ryan", stop=self.needs_ryan)
            return
        if h is None or not h.get("connected") or not h.get("in_game"):
            self.status("world_unavailable" if h is None else "waiting_for_world", health=h)
            return
        if h.get("busy"):
            self.status("waiting_bridge_busy", busy=h.get("busy"))
            return
        if self.clock() < self._bridge_retry_until:
            self.status("waiting_after_bridge_drop")
            return

        problems = self.limits_problems()
        if problems:
            self.event_once("limits", "limits_invalid", problems=problems)
            self.status("limits_invalid", problems=problems)
            return
        self.clear_once("limits")

        card = self.next_card()
        if card is None:
            self.event_once("queue_empty", "queue_empty")
            self.status("idle_queue_empty")
            return
        self.clear_once("queue_empty")
        self.start_session(card)

    def run_forever(self) -> None:
        self.ensure_dirs()
        self.recover()
        self.status("starting")

        def on_signal(signum, _frame):
            print(f"[runner] signal {signum}: shutting down", flush=True)
            self.shutting_down = True

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        print(f"[runner] up: harness={self.cfg.harness} owner={self.cfg.owner}", flush=True)
        while True:
            try:
                self.step()
            except Exception as e:                  # the runner must outlive anything unexpected
                print(f"[runner] error in loop: {e!r}", flush=True)
            if self.shutting_down and self.session is None:
                self.status("shut_down")
                print("[runner] shut down", flush=True)
                return
            time.sleep(self.cfg.poll_s)


def main() -> int:
    Runner(Config()).run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
