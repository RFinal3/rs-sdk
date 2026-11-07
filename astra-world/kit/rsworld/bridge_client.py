"""Python client for the astra-world bridge (the harness's hands).

Standard library only. Python 3.11+.

    from rsworld.bridge_client import BridgeClient
    hands = BridgeClient()                       # http://127.0.0.1:7790
    snap = hands.observe()                       # Snapshot(snapshot_id, tick, revision, state, ...)
    r = hands.act("walk_to", {"x": 3222, "z": 3218}, deadline_ms=30_000,
                  decided_at_tick=snap.tick, max_age_ticks=5)
    if r.refused: ...                            # nothing was sent to the game
    fresh = hands.observe()                      # confirm the effect from fresh state

Rules worth keeping in mind (HANDOFF.md has the full list):
  * ActResult.result is the SDK's claim. Count an effect only from a later observe().
  * Pass decided_at_tick=snap.tick from the snapshot your decision was made on,
    with max_age_ticks (default 5, one tick = 400 ms). A 'stale' refusal means
    the decision is too old: re-observe and rebuild your options. (`revision`
    advances every tick even when nothing happens; expect_revision is a strict
    mode that will refuse almost anything Jev decides.)
  * One action at a time. A 'busy' refusal means another action is in flight.
"""
from __future__ import annotations

import itertools
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:7790")

_counter = itertools.count(1)


def new_action_id(prefix: str = "a") -> str:
    """Unique, bridge-valid action id: prefix-<ms>-<n>."""
    return f"{prefix}-{int(time.time() * 1000)}-{next(_counter)}"


class BridgeUnavailable(Exception):
    """The bridge could not be reached or returned something unusable."""


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    tick: int
    revision: int | None
    captured_ms: int
    busy: str | None
    state: dict[str, Any]
    summary: str | None = None


@dataclass(frozen=True)
class ActResult:
    """What happened to one /act call.

    refused  -> the bridge sent nothing to the game (bad params, unknown verb,
                stale revision, busy, target not in current state).
    outcome  -> for accepted actions: completed | deadline | cancelled | error.
    """
    action_id: str
    verb: str
    http_status: int
    refused: bool
    error_code: str | None
    message: str | None
    outcome: str | None = None
    result: dict[str, Any] | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    elapsed_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def sdk_success(self) -> bool:
        """The SDK *says* it succeeded. Not proof of effect: confirm with observe()."""
        return bool(self.result and self.result.get("success"))


class BridgeClient:
    def __init__(self, url: str = DEFAULT_URL, token: str | None = None, timeout_s: float = 10.0):
        self.url = url.rstrip("/")
        self.token = token if token is not None else os.environ.get("BRIDGE_TOKEN") or None
        self.timeout_s = timeout_s

    # -- transport -----------------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None, timeout_s: float | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("content-type", "application/json")
        if self.token:
            req.add_header("x-bridge-token", self.token)
        # The bridge is local; never route it through a proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout_s or self.timeout_s) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except json.JSONDecodeError as je:
                raise BridgeUnavailable(f"HTTP {e.code} with non-JSON body") from je
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise BridgeUnavailable(str(e)) from e

    # -- API -----------------------------------------------------------------------
    def health(self) -> dict:
        _, body = self._request("GET", "/health")
        return body

    def get_raw(self, path: str, timeout_s: float | None = None) -> tuple[int, dict]:
        """Any read-only GET, returning (HTTP status, JSON body) untouched (the runner's mirror)."""
        return self._request("GET", path, timeout_s=timeout_s)

    def verbs(self) -> dict:
        _, body = self._request("GET", "/verbs")
        return body

    def observe(self, summary: bool = False) -> Snapshot:
        status, body = self._request("GET", "/observe?summary=1" if summary else "/observe")
        if status != 200:
            raise BridgeUnavailable(f"observe failed: {status} {body}")
        return Snapshot(
            snapshot_id=body["snapshot_id"], tick=body["tick"], revision=body.get("revision"),
            captured_ms=body["captured_ms"], busy=body.get("busy"), state=body["state"],
            summary=body.get("summary"),
        )

    def act(self, verb: str, params: dict | None = None, *, deadline_ms: int,
            decided_at_tick: int | None = None, max_age_ticks: int | None = None,
            expect_revision: int | None = None, action_id: str | None = None) -> ActResult:
        action_id = action_id or new_action_id()
        body: dict[str, Any] = {"action_id": action_id, "verb": verb, "params": params or {}, "deadline_ms": deadline_ms}
        if decided_at_tick is not None:
            body["decided_at_tick"] = decided_at_tick
        if max_age_ticks is not None:
            body["max_age_ticks"] = max_age_ticks
        if expect_revision is not None:
            body["expect_revision"] = expect_revision
        # HTTP timeout must outlast the game deadline.
        status, resp = self._request("POST", "/act", body, timeout_s=deadline_ms / 1000 + 15)
        if status == 200:
            return ActResult(
                action_id=action_id, verb=verb, http_status=status, refused=False,
                error_code=None, message=resp.get("error"), outcome=resp.get("outcome"),
                result=resp.get("result"), before=resp.get("before"), after=resp.get("after"),
                elapsed_ms=resp.get("elapsed_ms"), raw=resp,
            )
        return ActResult(
            action_id=action_id, verb=verb, http_status=status, refused=True,
            error_code=resp.get("error"), message=resp.get("message"), raw=resp,
        )

    def cancel(self, action_id: str | None = None) -> dict:
        _, body = self._request("POST", "/cancel", {"action_id": action_id} if action_id else {})
        return body
