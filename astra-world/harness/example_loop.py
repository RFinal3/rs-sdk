#!/usr/bin/env python3
"""A smoke test of the whole chain. NOT the harness: that one is Astra's to write.

    docker compose -f astra-world/compose.yaml exec player python3 /harness/example_loop.py [goal.json]

limits (Ryan, read-only) -> goal (validated) -> observe -> build options -> decider
(mock, never Jev) -> validate answer -> act (fresh-decision check) -> confirm from
fresh state -> progress report. Chops trees in the goal's area until a stop condition.

Stops on: Ryan's STOP file (/owner/STOP), session minutes/actions from limits,
the goal's target level, a full inventory, or too many rejected answers in a row.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from rsworld.bridge_client import BridgeClient, new_action_id
from rsworld.contracts import state_delta, validate_goal, validate_limits, validate_progress
from rsworld.decider import Option, Question, RandomDecider, validate_answer

OWNER = Path("/owner")
HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"


def main(*, owner=OWNER, reports=REPORTS, goal_path=None):
    limits = json.loads((owner / "limits.json").read_text())
    if problems := validate_limits(limits):
        sys.exit("limits.json invalid:\n  " + "\n  ".join(problems))
    goal_path = goal_path or HERE.parent / "config" / "goal.example.json"
    if not goal_path.exists():
        goal_path = Path("/opt/rs/astra-world/config/goal.example.json")
    goal = json.loads(goal_path.read_text())
    if problems := validate_goal(goal, limits):
        sys.exit("goal invalid, not running:\n  " + "\n  ".join(problems))

    area = next(a for a in limits["allowed_areas"] if a["name"] == goal["area"])
    in_area = lambda x, z: area["x_min"] <= x <= area["x_max"] and area["z_min"] <= z <= area["z_max"]
    hands, decider = BridgeClient(), RandomDecider(seed=1)
    d = limits["decisions"]
    session_id, start_ms = new_action_id("s"), int(time.time() * 1000)
    first = hands.observe()
    counts = {"accepted": 0, "refused": 0, "by_outcome": {}, "by_refusal": {}}
    events, rejected_in_a_row, calls, stop = [], 0, 0, None

    def action_stop_reason():
        # Recheck immediately before dispatch: observe/decide can take time.
        if (owner / "STOP").exists():
            return "owner STOP file"
        if time.time() * 1000 - start_ms >= limits["session"]["max_minutes"] * 60_000:
            return "session minutes"
        if counts["accepted"] + counts["refused"] >= limits["session"]["max_actions"]:
            return "session actions"
        return None

    def count_action(r):
        if r.refused:
            counts["refused"] += 1
            counts["by_refusal"][r.error_code] = counts["by_refusal"].get(r.error_code, 0) + 1
        else:
            counts["accepted"] += 1
            counts["by_outcome"][r.outcome] = counts["by_outcome"].get(r.outcome, 0) + 1

    stop = action_stop_reason()
    if stop is None:
        count_action(hands.act("skip_tutorial", {}, deadline_ms=120_000))  # no-op if already done

    while stop is None:
        if stop := action_stop_reason():
            break
        if calls >= d["max_calls_per_session"]:
            stop = "decision call cap"; break

        snap = hands.observe()
        st = snap.state
        levels = {s["name"]: s["baseLevel"] for s in st["skills"]}
        if all(levels.get(k, 0) >= v for k, v in goal["target"]["levels"].items()):
            stop = "target_reached"; break
        if len(st["inventory"]) >= 28 and "inventory_full" in goal["until"]:
            stop = "inventory_full"; break

        # Options: the 4 nearest reachable trees inside the allowed area. Opaque ids map to bound params.
        trees = sorted((l for l in st["nearbyLocs"]
                        if l["name"] == "Tree" and l.get("reachable") and in_area(l["x"], l["z"])
                        and any(o["text"] == "Chop down" for o in l["optionsWithIndex"])), key=lambda l: l["distance"])[:4]
        options = [Option(f"tree-{i}", f"Tree {t['distance']} tiles away", {"loc": {"id": t["id"], "x": t["x"], "z": t["z"]}})
                   for i, t in enumerate(trees)]
        if not options:
            stop = "no options (no tree in area)"; break

        q = Question(new_action_id("q"), snap.snapshot_id, snap.tick, goal.get("plan", ""),
                     {"hp": levels.get("Hitpoints"), "inventory_used": len(st["inventory"])}, options)
        answer = decider.decide(q); calls += 1
        if problems := validate_answer(q, answer):
            rejected_in_a_row += 1
            events.append({"tick": snap.tick, "kind": "rejected_answer", "detail": "; ".join(problems)[:500]})
            if rejected_in_a_row >= d["max_rejected_answers_in_a_row"]:
                stop = "rejection_limit"
            continue
        rejected_in_a_row = 0
        chosen = next(o for o in options if o.option_id == answer.choice_id)

        if stop := action_stop_reason():
            break
        r = hands.act("chop_tree", {"loc": chosen.detail["loc"]}, deadline_ms=60_000,
                      decided_at_tick=snap.tick, max_age_ticks=d["max_decision_age_ticks"])
        count_action(r)
        if r.refused:
            continue
        after = hands.observe().state                     # confirm from fresh state, not from r.result
        gained = state_delta(st, after)["items_gained"].get("Logs", 0)
        events.append({"tick": hands.observe().tick, "kind": "chop",
                       "detail": f"{chosen.label}: sdk_success={r.sdk_success} logs_gained={gained} via {answer.provenance}"})

    last = hands.observe()
    delta = state_delta(first.state, last.state)
    p = last.state["player"]
    report = {
        "version": 1, "session_id": session_id, "goal_id": goal["goal_id"],
        "from_ms": start_ms, "to_ms": int(time.time() * 1000), "from_tick": first.tick, "to_tick": last.tick,
        "position": {"x": p["worldX"], "z": p["worldZ"], "level": p["level"]},
        **delta, "actions": counts, "deaths": 0, "guards": {},
        "decisions": {"provider": "mock-random", "calls": calls, "rejected_answers": sum(e["kind"] == "rejected_answer" for e in events),
                      "spend_usd": 0.0},   # mock decider: genuinely free. A real provider reports null when unknown.
        "events": events[-500:], "stop_reason": stop,
    }
    assert not validate_progress(report), validate_progress(report)
    reports.mkdir(exist_ok=True)
    out = reports / f"{session_id}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"stopped: {stop}\nreport: {out}\nxp: {delta['xp_gained']} items: {delta['items_gained']} actions: {counts}")
    return report


if __name__ == "__main__":
    main(goal_path=Path(sys.argv[1]) if len(sys.argv) > 1 else None)
