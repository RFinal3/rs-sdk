"""Exercise the real smoke loop with recording hands; no game is required."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("example_loop", ROOT / "harness/example_loop.py")
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = Path(self.tmp.name)
        self.limits = json.loads((ROOT / "config/limits.example.json").read_text())
        self.acts = []
        self.refused = False
        state = {
            "player": {"worldX": 3222, "worldZ": 3222, "level": 0},
            "skills": [{"name": "Woodcutting", "baseLevel": 1, "experience": 0}],
            "inventory": [],
            "nearbyLocs": [{"id": 1, "name": "Tree", "reachable": True, "x": 3223, "z": 3222,
                            "distance": 1, "optionsWithIndex": [{"text": "Chop down", "index": 1}]}],
        }
        self.hands = SimpleNamespace(
            observe=lambda: SimpleNamespace(state=state, snapshot_id="r1-t1", tick=1),
            act=self.act,
        )

    def act(self, verb, params, **kwargs):
        self.acts.append(verb)
        return SimpleNamespace(refused=self.refused, error_code="stale" if self.refused else None,
                               outcome="completed", sdk_success=not self.refused)

    def run_loop(self):
        (self.owner / "limits.json").write_text(json.dumps(self.limits))
        with patch.object(loop, "BridgeClient", return_value=self.hands), contextlib.redirect_stdout(io.StringIO()):
            report = loop.main(owner=self.owner, reports=self.owner / "reports")
        self.assertEqual(len(list((self.owner / "reports").glob("*.json"))), 1)
        return report

    def test_existing_stop_prevents_every_action(self):
        (self.owner / "STOP").touch()
        report = self.run_loop()
        self.assertEqual(self.acts, [])
        self.assertEqual(report["stop_reason"], "owner STOP file")
        self.assertEqual(report["actions"]["accepted"], 0)

    def test_stop_created_during_decision_prevents_chop(self):
        decide = loop.RandomDecider.decide

        def decide_then_stop(decider, question):
            answer = decide(decider, question)
            (self.owner / "STOP").touch()
            return answer

        with patch.object(loop.RandomDecider, "decide", decide_then_stop):
            report = self.run_loop()
        self.assertEqual(self.acts, ["skip_tutorial"])
        self.assertEqual(report["stop_reason"], "owner STOP file")
        self.assertEqual(report["actions"]["by_outcome"], {"completed": 1})

    def test_tutorial_consumes_action_budget_even_if_refused(self):
        self.limits["session"]["max_actions"] = 1
        for refused in (False, True):
            with self.subTest(refused=refused):
                self.refused = refused
                self.acts.clear()
                for old in (self.owner / "reports").glob("*.json"):
                    old.unlink()
                report = self.run_loop()
                self.assertEqual(self.acts, ["skip_tutorial"])
                self.assertEqual(report["stop_reason"], "session actions")
                self.assertEqual(report["actions"]["refused"], int(refused))
                self.assertEqual(report["actions"]["accepted"], int(not refused))

    def test_tutorial_and_chop_share_the_budget(self):
        self.limits["session"]["max_actions"] = 2
        report = self.run_loop()
        self.assertEqual(self.acts, ["skip_tutorial", "chop_tree"])
        self.assertEqual(report["stop_reason"], "session actions")
        self.assertEqual(report["actions"]["by_outcome"], {"completed": 2})

    def test_session_expires_during_decision(self):
        now = [1000.0]
        decide = loop.RandomDecider.decide

        def delayed_decision(decider, question):
            answer = decide(decider, question)
            now[0] += self.limits["session"]["max_minutes"] * 60
            return answer

        with patch.object(loop.time, "time", side_effect=lambda: now[0]), \
                patch.object(loop.RandomDecider, "decide", delayed_decision):
            report = self.run_loop()
        self.assertEqual(self.acts, ["skip_tutorial"])
        self.assertEqual(report["stop_reason"], "session minutes")


if __name__ == "__main__":
    unittest.main()
