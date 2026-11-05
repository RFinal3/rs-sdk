import copy
import json
import unittest
from pathlib import Path

from rsworld.contracts import state_delta, validate_goal, validate_limits, validate_progress

ROOT = Path(__file__).resolve().parents[2]
LIMITS = json.loads((ROOT / "config/limits.example.json").read_text())
GOAL = json.loads((ROOT / "config/goal.example.json").read_text())
FIX = ROOT / "fixtures"


def progress_example():
    return {
        "version": 1, "session_id": "s-1", "goal_id": GOAL["goal_id"],
        "from_ms": 1, "to_ms": 2, "from_tick": 10, "to_tick": 20,
        "position": {"x": 3222, "z": 3222, "level": 0},
        "xp_gained": {"Woodcutting": 625}, "levels": {"Woodcutting": 2},
        "items_gained": {"Logs": 1}, "items_lost": {},
        "actions": {"accepted": 3, "refused": 1, "by_outcome": {"completed": 3}, "by_refusal": {"stale": 1}},
        "deaths": 0, "guards": {"eat": 0},
        "decisions": {"provider": "mock-random", "calls": 3, "rejected_answers": 0, "spend_usd": None},
        "events": [{"tick": 15, "kind": "level_up", "detail": "Woodcutting 2"}],
        "stop_reason": None,
    }


class LimitsTest(unittest.TestCase):
    def test_example_is_valid(self):
        self.assertEqual(validate_limits(LIMITS), [])

    def test_unknown_and_missing_fields(self):
        d = copy.deepcopy(LIMITS); d["surprise"] = 1; del d["session"]
        probs = validate_limits(d)
        self.assertTrue(any("surprise" in p for p in probs))
        self.assertTrue(any("limits.session" in p and "missing" in p for p in probs))

    def test_empty_areas_mean_nowhere(self):
        d = copy.deepcopy(LIMITS); d["allowed_areas"] = []
        self.assertTrue(validate_limits(d))

    def test_cross_field_rules(self):
        d = copy.deepcopy(LIMITS); d["decisions"]["max_spend_usd_per_session"] = 5.0
        self.assertTrue(any("daily cap" in p for p in validate_limits(d)))
        d = copy.deepcopy(LIMITS); d["safety_guards"]["retreat_below_hp_pct"] = 80
        self.assertTrue(any("retreat" in p for p in validate_limits(d)))
        d = copy.deepcopy(LIMITS); d["allowed_areas"][0]["x_min"] = 4000
        self.assertTrue(any("min exceeds max" in p for p in validate_limits(d)))

    def test_bools_are_not_ints(self):
        d = copy.deepcopy(LIMITS); d["session"]["max_minutes"] = True
        self.assertTrue(validate_limits(d))

    def test_nan_spend_rejected(self):
        d = copy.deepcopy(LIMITS); d["decisions"]["max_spend_usd_per_day"] = float("nan")
        self.assertTrue(validate_limits(d))


class GoalTest(unittest.TestCase):
    def test_example_is_valid(self):
        self.assertEqual(validate_goal(GOAL, LIMITS), [])

    def test_kind_must_be_enabled(self):
        g = copy.deepcopy(GOAL); g["kind"] = "combat"
        self.assertTrue(any("not enabled" in p for p in validate_goal(g, LIMITS)))

    def test_area_must_be_allowed(self):
        g = copy.deepcopy(GOAL); g["area"] = "wilderness"
        self.assertTrue(any("goal.area" in p for p in validate_goal(g, LIMITS)))

    def test_levels_and_skills(self):
        g = copy.deepcopy(GOAL); g["target"] = {"levels": {"Woodcutting": 120}}
        self.assertTrue(validate_goal(g, LIMITS))
        g["target"] = {"levels": {"Sailing": 10}}
        self.assertTrue(any("unknown skill" in p for p in validate_goal(g, LIMITS)))
        g = copy.deepcopy(GOAL); g["focus"] = ["Stat18"]
        self.assertTrue(validate_goal(g, LIMITS))

    def test_until_needs_a_stop(self):
        g = copy.deepcopy(GOAL); g["until"] = []
        self.assertTrue(validate_goal(g, LIMITS))

    def test_no_extra_authority_fields(self):
        g = copy.deepcopy(GOAL); g["limits"] = {"max_spend_usd_per_day": 100}
        self.assertTrue(any("unknown field" in p for p in validate_goal(g, LIMITS)))

    def test_quest_script_reading_needs_permission(self):
        lim = copy.deepcopy(LIMITS); lim["goal_kinds_enabled"] = ["quest"]
        g = {"version": 1, "goal_id": "q-cook", "kind": "quest", "area": "lumbridge",
             "target": {"quest": "cook", "read_quest_script": True}, "until": ["target_reached"]}
        self.assertTrue(any("not allowed" in p for p in validate_goal(g, lim)))
        lim["quest_reading"]["quest_scripts_allowed_for"] = ["cook"]
        self.assertEqual(validate_goal(g, lim), [])


class ProgressTest(unittest.TestCase):
    def test_example_is_valid(self):
        self.assertEqual(validate_progress(progress_example()), [])

    def test_unknown_spend_is_null_not_zero(self):
        p = progress_example(); p["decisions"]["spend_usd"] = -1
        self.assertTrue(validate_progress(p))

    def test_time_order(self):
        p = progress_example(); p["from_ms"] = 5
        self.assertTrue(any("after" in x for x in validate_progress(p)))


class DeltaTest(unittest.TestCase):
    def test_delta_from_real_fixtures(self):
        before = json.loads((FIX / "state-02-after-walk.json").read_text())
        after = json.loads((FIX / "state-03-after-chop.json").read_text())
        d = state_delta(before, after)
        self.assertGreater(d["items_gained"].get("Logs", 0), 0)
        self.assertGreater(d["xp_gained"].get("Woodcutting", 0), 0)
        self.assertNotIn("Stat18", d["levels"])


if __name__ == "__main__":
    unittest.main()
