"""The three r3 contracts between Ryan, Astra and the harness.

  Limits          Ryan writes, nobody else. Spend, session size, enabled goal kinds,
                  allowed areas, safety guards, check-in cadence, quest reading rules.
  Goal            Astra writes. What the character is doing now. Validated against Limits.
  ProgressReport  The harness writes, Astra reads. Facts since the last check-in.

All three are JSON files. Validators return a list of problems; an empty list means
valid. They never repair input. A goal that fails validation must not run (the
harness idles somewhere safe and reports the problems).

Standard library only.
"""
from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = 1

# Skills as the game reports them (state.skills[].name), captured 2026-11-04.
SKILLS = frozenset({
    "Attack", "Defence", "Strength", "Hitpoints", "Ranged", "Prayer", "Magic", "Cooking",
    "Woodcutting", "Fletching", "Fishing", "Firemaking", "Crafting", "Smithing", "Mining",
    "Herblore", "Agility", "Thieving", "Stat18", "Stat19", "Runecraft",
})
TRAINABLE_SKILLS = SKILLS - {"Stat18", "Stat19"}

GOAL_KINDS = ("skill_loop", "combat", "collect", "quest")
CHECKIN_EVENTS = ("goal_reached", "death", "guard_repeat", "stagnation", "rejection_limit", "session_end")
UNTIL_CONDITIONS = ("target_reached", "session_end", "inventory_full", "out_of_supplies")

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_GOAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)) and v == v and v not in (float("inf"), float("-inf"))


class _Check:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def fail(self, path: str, msg: str) -> None:
        self.problems.append(f"{path}: {msg}")

    def obj(self, d: Any, path: str, required: set[str], optional: set[str] = frozenset()) -> bool:
        if not isinstance(d, dict):
            self.fail(path, "must be an object")
            return False
        for k in required - d.keys():
            self.fail(f"{path}.{k}", "missing")
        for k in d.keys() - required - optional:
            self.fail(f"{path}.{k}", "unknown field")
        return True

    def int_in(self, v: Any, path: str, lo: int, hi: int) -> None:
        if not _is_int(v) or not lo <= v <= hi:
            self.fail(path, f"must be an integer in [{lo}, {hi}]")

    def num_in(self, v: Any, path: str, lo: float, hi: float) -> None:
        if not _is_num(v) or not lo <= v <= hi:
            self.fail(path, f"must be a finite number in [{lo}, {hi}]")

    def text(self, v: Any, path: str, max_len: int, allow_empty: bool = False) -> None:
        if not isinstance(v, str) or len(v) > max_len or (not allow_empty and not v.strip()):
            self.fail(path, f"must be a {'string' if allow_empty else 'non-empty string'} of at most {max_len} chars")

    def str_list(self, v: Any, path: str, max_items: int, max_len: int, choices: tuple[str, ...] | None = None) -> None:
        if not isinstance(v, list) or len(v) > max_items:
            self.fail(path, f"must be a list of at most {max_items} strings")
            return
        for i, s in enumerate(v):
            if choices is not None:
                if s not in choices:
                    self.fail(f"{path}[{i}]", f"must be one of {', '.join(choices)}")
            else:
                self.text(s, f"{path}[{i}]", max_len)
        if len(set(map(str, v))) != len(v):
            self.fail(path, "has duplicates")


# --------------------------------------------------------------------------------------
# Limits (Ryan)
# --------------------------------------------------------------------------------------

def validate_limits(d: Any) -> list[str]:
    c = _Check()
    if not c.obj(d, "limits", {"version", "session", "decisions", "checkins", "goal_kinds_enabled",
                               "allowed_areas", "safety_guards", "quest_reading", "harness_changes"}):
        return c.problems
    if d.get("version") != SCHEMA_VERSION:
        c.fail("limits.version", f"must be {SCHEMA_VERSION}")

    s = d.get("session")
    if c.obj(s, "limits.session", {"max_minutes", "max_actions"}):
        c.int_in(s.get("max_minutes"), "limits.session.max_minutes", 1, 24 * 60)
        c.int_in(s.get("max_actions"), "limits.session.max_actions", 1, 100_000)

    j = d.get("decisions")
    if c.obj(j, "limits.decisions", {"max_calls_per_session", "max_calls_per_minute", "max_spend_usd_per_session",
                                     "max_spend_usd_per_day", "request_deadline_ms", "max_decision_age_ticks",
                                     "max_rejected_answers_in_a_row"}):
        c.int_in(j.get("max_calls_per_session"), "limits.decisions.max_calls_per_session", 0, 100_000)
        c.int_in(j.get("max_calls_per_minute"), "limits.decisions.max_calls_per_minute", 0, 600)
        c.num_in(j.get("max_spend_usd_per_session"), "limits.decisions.max_spend_usd_per_session", 0, 100)
        c.num_in(j.get("max_spend_usd_per_day"), "limits.decisions.max_spend_usd_per_day", 0, 100)
        c.int_in(j.get("request_deadline_ms"), "limits.decisions.request_deadline_ms", 100, 60_000)
        c.int_in(j.get("max_decision_age_ticks"), "limits.decisions.max_decision_age_ticks", 0, 100)
        c.int_in(j.get("max_rejected_answers_in_a_row"), "limits.decisions.max_rejected_answers_in_a_row", 1, 100)
        if (_is_num(j.get("max_spend_usd_per_session")) and _is_num(j.get("max_spend_usd_per_day"))
                and j["max_spend_usd_per_session"] > j["max_spend_usd_per_day"]):
            c.fail("limits.decisions", "per-session spend cap exceeds the daily cap")

    k = d.get("checkins")
    if c.obj(k, "limits.checkins", {"every_minutes", "on_events"}):
        c.int_in(k.get("every_minutes"), "limits.checkins.every_minutes", 0, 24 * 60)
        c.str_list(k.get("on_events"), "limits.checkins.on_events", len(CHECKIN_EVENTS), 32, CHECKIN_EVENTS)

    c.str_list(d.get("goal_kinds_enabled"), "limits.goal_kinds_enabled", len(GOAL_KINDS), 32, GOAL_KINDS)

    areas = d.get("allowed_areas")
    if not isinstance(areas, list) or not areas:
        c.fail("limits.allowed_areas", "must be a non-empty list (an empty list would mean nowhere)")
    else:
        names = set()
        for i, a in enumerate(areas):
            p = f"limits.allowed_areas[{i}]"
            if c.obj(a, p, {"name", "x_min", "x_max", "z_min", "z_max"}, {"notes"}):
                if not isinstance(a.get("name"), str) or not _ID.match(a["name"]):
                    c.fail(f"{p}.name", "must match [a-z0-9][a-z0-9_-]*")
                elif a["name"] in names:
                    c.fail(f"{p}.name", "duplicate")
                else:
                    names.add(a["name"])
                for key in ("x_min", "x_max", "z_min", "z_max"):
                    c.int_in(a.get(key), f"{p}.{key}", 0, 16_383)
                if all(_is_int(a.get(k)) for k in ("x_min", "x_max", "z_min", "z_max")):
                    if a["x_min"] > a["x_max"] or a["z_min"] > a["z_max"]:
                        c.fail(p, "min exceeds max")
                if "notes" in a:
                    c.text(a["notes"], f"{p}.notes", 500, allow_empty=True)

    g = d.get("safety_guards")
    if c.obj(g, "limits.safety_guards", {"eat_below_hp_pct", "retreat_below_hp_pct", "stop_after_deaths",
                                         "max_guard_firings_per_10min"}):
        c.int_in(g.get("eat_below_hp_pct"), "limits.safety_guards.eat_below_hp_pct", 0, 100)
        c.int_in(g.get("retreat_below_hp_pct"), "limits.safety_guards.retreat_below_hp_pct", 0, 100)
        c.int_in(g.get("stop_after_deaths"), "limits.safety_guards.stop_after_deaths", 1, 100)
        c.int_in(g.get("max_guard_firings_per_10min"), "limits.safety_guards.max_guard_firings_per_10min", 1, 1000)
        if _is_int(g.get("eat_below_hp_pct")) and _is_int(g.get("retreat_below_hp_pct")) \
                and g["retreat_below_hp_pct"] > g["eat_below_hp_pct"]:
            c.fail("limits.safety_guards", "retreat threshold is above the eat threshold")

    q = d.get("quest_reading")
    if c.obj(q, "limits.quest_reading", {"guides", "quest_scripts_allowed_for"}):
        if not isinstance(q.get("guides"), bool):
            c.fail("limits.quest_reading.guides", "must be true or false")
        c.str_list(q.get("quest_scripts_allowed_for"), "limits.quest_reading.quest_scripts_allowed_for", 200, 64)

    h = d.get("harness_changes")
    if c.obj(h, "limits.harness_changes", {"require_owner_approval"}):
        if not isinstance(h.get("require_owner_approval"), bool):
            c.fail("limits.harness_changes.require_owner_approval", "must be true or false")
    return c.problems


# --------------------------------------------------------------------------------------
# Goal (Astra)
# --------------------------------------------------------------------------------------

def validate_goal(d: Any, limits: dict) -> list[str]:
    """Validate a goal file against already-valid limits."""
    c = _Check()
    if not c.obj(d, "goal", {"version", "goal_id", "kind", "area", "target", "until"},
                 {"focus", "plan", "avoid", "notes_for_harness"}):
        return c.problems
    if d.get("version") != SCHEMA_VERSION:
        c.fail("goal.version", f"must be {SCHEMA_VERSION}")
    if not isinstance(d.get("goal_id"), str) or not _GOAL_ID.match(d["goal_id"]):
        c.fail("goal.goal_id", "must be 1-64 chars of [A-Za-z0-9._-]")

    kind = d.get("kind")
    if kind not in GOAL_KINDS:
        c.fail("goal.kind", f"must be one of {', '.join(GOAL_KINDS)}")
    elif kind not in limits.get("goal_kinds_enabled", []):
        c.fail("goal.kind", f"'{kind}' is not enabled in limits")

    area_names = {a["name"] for a in limits.get("allowed_areas", [])}
    if d.get("area") not in area_names:
        c.fail("goal.area", f"must be one of the allowed areas: {', '.join(sorted(area_names)) or '(none)'}")

    if "focus" in d:
        c.str_list(d["focus"], "goal.focus", 4, 32, tuple(sorted(TRAINABLE_SKILLS)))
    for key, n in (("plan", 2000), ("notes_for_harness", 2000)):
        if key in d:
            c.text(d[key], f"goal.{key}", n, allow_empty=True)
    if "avoid" in d:
        c.str_list(d["avoid"], "goal.avoid", 20, 200)
    c.str_list(d.get("until"), "goal.until", len(UNTIL_CONDITIONS), 32, UNTIL_CONDITIONS)
    if isinstance(d.get("until"), list) and not d["until"]:
        c.fail("goal.until", "must name at least one stop condition")

    t = d.get("target")
    if kind in ("skill_loop", "combat"):
        if not isinstance(t, dict) or not t.get("levels"):
            c.fail("goal.target", "needs {\"levels\": {skill: level}}")
        elif c.obj(t, "goal.target", {"levels"}):
            levels = t["levels"]
            if not isinstance(levels, dict) or not levels:
                c.fail("goal.target.levels", "must be a non-empty object")
            else:
                for skill, lvl in levels.items():
                    if skill not in TRAINABLE_SKILLS:
                        c.fail(f"goal.target.levels.{skill}", "unknown skill")
                    c.int_in(lvl, f"goal.target.levels.{skill}", 2, 99)
    elif kind == "collect":
        if c.obj(t, "goal.target", {"item", "count"}):
            c.text(t.get("item"), "goal.target.item", 64)
            c.int_in(t.get("count"), "goal.target.count", 1, 1_000_000)
    elif kind == "quest":
        if c.obj(t, "goal.target", {"quest"}, {"read_quest_script"}):
            quest = t.get("quest")
            if not isinstance(quest, str) or not _ID.match(quest):
                c.fail("goal.target.quest", "must be a quest id like 'cook' (folder name without 'quest_')")
            if t.get("read_quest_script"):
                if quest not in limits.get("quest_reading", {}).get("quest_scripts_allowed_for", []):
                    c.fail("goal.target.read_quest_script", f"reading the script for '{quest}' is not allowed in limits")
    return c.problems


# --------------------------------------------------------------------------------------
# Progress report (harness -> Astra)
# --------------------------------------------------------------------------------------

def validate_progress(d: Any) -> list[str]:
    c = _Check()
    if not c.obj(d, "progress", {"version", "session_id", "goal_id", "from_ms", "to_ms", "from_tick", "to_tick",
                                 "position", "xp_gained", "levels", "items_gained", "items_lost", "actions",
                                 "deaths", "guards", "decisions", "events", "stop_reason"}):
        return c.problems
    if d.get("version") != SCHEMA_VERSION:
        c.fail("progress.version", f"must be {SCHEMA_VERSION}")
    for k in ("session_id", "goal_id"):
        c.text(d.get(k), f"progress.{k}", 64)
    for k in ("from_ms", "to_ms", "from_tick", "to_tick"):
        c.int_in(d.get(k), f"progress.{k}", 0, 2**53)
    if _is_int(d.get("from_ms")) and _is_int(d.get("to_ms")) and d["from_ms"] > d["to_ms"]:
        c.fail("progress", "from_ms is after to_ms")
    pos = d.get("position")
    if c.obj(pos, "progress.position", {"x", "z", "level"}):
        for k in ("x", "z"):
            c.int_in(pos.get(k), f"progress.position.{k}", 0, 16_383)
        c.int_in(pos.get("level"), "progress.position.level", 0, 3)
    for k in ("xp_gained", "levels", "items_gained", "items_lost"):
        m = d.get(k)
        if not isinstance(m, dict):
            c.fail(f"progress.{k}", "must be an object of name -> integer")
            continue
        for name, v in m.items():
            c.int_in(v, f"progress.{k}.{name}", 0, 2**31)
    a = d.get("actions")
    if c.obj(a, "progress.actions", {"accepted", "refused", "by_outcome", "by_refusal"}):
        c.int_in(a.get("accepted"), "progress.actions.accepted", 0, 10**9)
        c.int_in(a.get("refused"), "progress.actions.refused", 0, 10**9)
        for k in ("by_outcome", "by_refusal"):
            if not isinstance(a.get(k), dict) or not all(_is_int(v) and v >= 0 for v in a[k].values()):
                c.fail(f"progress.actions.{k}", "must be an object of name -> non-negative integer")
    c.int_in(d.get("deaths"), "progress.deaths", 0, 10**6)
    g = d.get("guards")
    if not isinstance(g, dict) or not all(_is_int(v) and v >= 0 for v in g.values()):
        c.fail("progress.guards", "must be an object of guard name -> firing count")
    j = d.get("decisions")
    if c.obj(j, "progress.decisions", {"provider", "calls", "rejected_answers", "spend_usd"}):
        c.text(j.get("provider"), "progress.decisions.provider", 64)
        c.int_in(j.get("calls"), "progress.decisions.calls", 0, 10**9)
        c.int_in(j.get("rejected_answers"), "progress.decisions.rejected_answers", 0, 10**9)
        # null means unknown. Unknown spend is never reported as zero.
        if j.get("spend_usd") is not None:
            c.num_in(j.get("spend_usd"), "progress.decisions.spend_usd", 0, 1000)
    ev = d.get("events")
    if not isinstance(ev, list) or len(ev) > 500:
        c.fail("progress.events", "must be a list of at most 500 events")
    else:
        for i, e in enumerate(ev):
            if c.obj(e, f"progress.events[{i}]", {"tick", "kind", "detail"}):
                c.int_in(e.get("tick"), f"progress.events[{i}].tick", 0, 2**53)
                c.text(e.get("kind"), f"progress.events[{i}].kind", 32)
                c.text(e.get("detail"), f"progress.events[{i}].detail", 500, allow_empty=True)
    sr = d.get("stop_reason")
    if sr is not None:
        c.text(sr, "progress.stop_reason", 200)
    return c.problems


# --------------------------------------------------------------------------------------
# Facts from two snapshots (helps the harness fill a progress report honestly)
# --------------------------------------------------------------------------------------

def state_delta(before: dict, after: dict) -> dict:
    """XP gained, levels now, and inventory items gained/lost between two game states.

    Uses only observed state. Items moved to the bank show up as 'lost' from the
    inventory; the harness should account for banking separately if it matters.
    """
    xp0 = {s["name"]: s["experience"] for s in before.get("skills", [])}
    xp1 = {s["name"]: s["experience"] for s in after.get("skills", [])}
    lv1 = {s["name"]: s["baseLevel"] for s in after.get("skills", [])}
    xp_gained = {k: xp1[k] - xp0.get(k, 0) for k in xp1 if xp1[k] > xp0.get(k, 0)}

    def inv(state: dict) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in state.get("inventory", []):
            out[item["name"]] = out.get(item["name"], 0) + item["count"]
        return out

    i0, i1 = inv(before), inv(after)
    gained = {k: i1[k] - i0.get(k, 0) for k in i1 if i1[k] > i0.get(k, 0)}
    lost = {k: i0[k] - i1.get(k, 0) for k in i0 if i0[k] > i1.get(k, 0)}
    return {"xp_gained": xp_gained, "levels": {k: v for k, v in lv1.items() if k in TRAINABLE_SKILLS},
            "items_gained": gained, "items_lost": lost}
