#!/usr/bin/env python3
"""Live smoke test for the bridge: run against a real (private!) world.

    python3 astra-world/kit/live_check.py            # bridge at $BRIDGE_URL or 127.0.0.1:7790

Moves the character a few tiles and chops one tree if one is near. Each check
confirms the effect from a fresh observe(), not from the SDK's success message.
Exit code 0 only if every check passes.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rsworld.bridge_client import BridgeClient  # noqa: E402

hands = BridgeClient()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))


def pos(snap):
    p = snap.state["player"]
    return p["worldX"], p["worldZ"]


def settle():
    hands.act("wait_idle", {"timeout_ms": 5000}, deadline_ms=6000)
    return hands.observe()


# 0. a brand-new character starts in the tutorial
r = hands.act("skip_tutorial", {}, deadline_ms=120_000)
check("skip_tutorial", not r.refused and r.outcome == "completed", f"sdk={r.result}")

# 1. connected
h = hands.health()
check("bridge connected and in game", bool(h.get("connected") and h.get("in_game")), str(h))

# 2. exact walk, confirmed from fresh state
s0 = settle()
x0, z0 = pos(s0)
target = (3222, 3222)  # Lumbridge spawn tile: known walkable (relative offsets can land on blocked tiles)
r = hands.act("walk_to", {"x": target[0], "z": target[1], "tolerance": 0}, deadline_ms=30_000, decided_at_tick=s0.tick)
s1 = settle()
check("walk_to tolerance 0 lands on the tile", not r.refused and pos(s1) == target,
      f"sdk={r.result} outcome={r.outcome} at={pos(s1)} target={target}")

# 3. refusal: target not in state -> nothing sent
r = hands.act("interact_loc", {"loc": {"id": 1, "x": 0, "z": 0}, "op": 1}, deadline_ms=5000)
check("unknown target refused before sending", r.refused and r.error_code == "target_not_found", f"{r.error_code}: {r.message}")

# 4. refusal: unknown verb, unknown param
r = hands.act("run_code", {"code": "x"}, deadline_ms=5000)
check("unknown verb refused", r.refused and r.error_code == "unknown_verb", str(r.error_code))
r = hands.act("walk_to", {"x": 1, "z": 1, "speed": 9}, deadline_ms=5000)
check("unknown param refused", r.refused and r.error_code == "bad_params", str(r.message))

# 5. stale decision refused; fresh one within max age accepted
old = hands.observe()
time.sleep(2.5)  # ~6 ticks
r = hands.act("wait_idle", {"timeout_ms": 1000}, deadline_ms=2000, decided_at_tick=old.tick, max_age_ticks=3)
check("decision older than max_age_ticks refused", r.refused and r.error_code == "stale", str(r.raw.get("message")))
fresh = hands.observe()
r = hands.act("wait_idle", {"timeout_ms": 1000}, deadline_ms=2000, decided_at_tick=fresh.tick, max_age_ticks=5)
check("fresh decision accepted", not r.refused, f"outcome={r.outcome}")

# 6. busy + cancel on a long walk
s2 = settle()
x2, z2 = pos(s2)
far = (x2 - 12, z2 - 12)
box: dict = {}
t = threading.Thread(target=lambda: box.setdefault("r", hands.act("walk_to", {"x": far[0], "z": far[1]}, deadline_ms=60_000, action_id="long-walk")))
t.start()
time.sleep(1.2)
busy = hands.act("wait_idle", {}, deadline_ms=2000)
check("second action refused while one is in flight", busy.refused and busy.error_code == "busy", str(busy.raw))
c = hands.cancel("long-walk")
t.join(timeout=20)
lw = box.get("r")
check("cancel stops the walk", bool(c.get("cancelled")) and c.get("stop", {}).get("confirmed") is True and lw is not None and lw.outcome == "cancelled",
      f"cancel={c} outcome={getattr(lw, 'outcome', None)}")
# Do not issue wait_idle or another action that could hide continued movement.
s3 = hands.observe()
stop_pos = pos(s3)
last_tick, observed_ticks, stayed = s3.tick, 0, True
until = time.monotonic() + 5
while observed_ticks < 4 and time.monotonic() < until:
    time.sleep(0.1)
    fresh = hands.observe()
    if fresh.tick > last_tick:
        observed_ticks += 1
        last_tick = fresh.tick
        stayed = stayed and pos(fresh) == stop_pos
check("character remains stopped across four later ticks", observed_ticks == 4 and stayed and stop_pos != far,
      f"at={stop_pos} far={far} fresh_ticks={observed_ticks} stationary={stayed}")

# 7. a real skilling effect, confirmed from inventory
s4 = hands.observe()
trees = [l for l in s4.state["nearbyLocs"] if l["name"] == "Tree" and any(o["text"] == "Chop down" for o in l["optionsWithIndex"])]
if not trees:
    check("chop a tree (skipped: no tree nearby)", True)
else:
    tree = min(trees, key=lambda l: l["distance"])
    logs_before = sum(i["count"] for i in s4.state["inventory"] if i["name"] == "Logs")
    r = hands.act("chop_tree", {"loc": {"id": tree["id"], "x": tree["x"], "z": tree["z"]}}, deadline_ms=60_000, decided_at_tick=s4.tick)
    s5 = settle()
    logs_after = sum(i["count"] for i in s5.state["inventory"] if i["name"] == "Logs")
    wc = {s["name"]: s["experience"] for s in s5.state["skills"]}.get("Woodcutting")
    check("chop_tree adds logs to inventory", logs_after > logs_before,
          f"sdk={r.result} logs {logs_before}->{logs_after} woodcutting xp={wc}")

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
