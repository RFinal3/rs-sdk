"""Runner tests against a fake bridge and a fake harness (no game, no Docker).

    cd astra-world && PYTHONPATH=kit:runner python3 -m unittest discover -s runner/runner_tests -t runner
"""
from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rsworld.bridge_client import BridgeClient

import runner as R

HERE = Path(__file__).resolve().parent
EXAMPLE_LIMITS = HERE.parents[1] / "config" / "limits.example.json"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeBridge:
    """Mutable bridge state served over real HTTP on 127.0.0.1."""

    def __init__(self):
        self.health = {"connected": True, "in_game": True, "busy": None, "stop_failure": None, "tick": 7, "revision": 7}
        self.observe_body = {"snapshot_id": "r7-t7", "captured_ms": 1, "tick": 7, "revision": 7, "busy": None,
                             "state": {"tick": 7, "note": "login via /bot?bot=Astra&password=hunter2"},
                             "summary": "Woodcutting 17. Nearby: Tree, Willow"}
        self.cancel_body = {"cancelled": True}
        self.screenshot = (200, {"tick": 7, "png_base64": base64.b64encode(PNG).decode()})
        self.calls: list[str] = []
        self.down = False
        bridge = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                bridge.calls.append("GET " + self.path)
                if self.path == "/health":
                    return self._send(200, bridge.health)
                if self.path.startswith("/observe"):
                    return self._send(200, bridge.observe_body)
                if self.path == "/verbs":
                    return self._send(200, {"verbs": [{"name": "chop_tree"}]})
                if self.path == "/screenshot":
                    return self._send(*bridge.screenshot)
                return self._send(404, {"error": "not_found"})

            def do_POST(self):
                bridge.calls.append("POST " + self.path)
                if self.path == "/cancel":
                    body = bridge.cancel_body
                    return self._send(503 if body.get("error") else 200, body)
                if self.path == "/act":
                    return self._send(500, {"error": "the runner must never call /act"})
                return self._send(404, {"error": "not_found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def client(self):
        if self.down:
            return BridgeClient("http://127.0.0.1:1", timeout_s=0.5)
        return BridgeClient(self.url, timeout_s=2)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.harness, self.owner = self.tmp / "harness", self.tmp / "owner"
        self.owner.mkdir()
        shutil.copy(EXAMPLE_LIMITS, self.owner / "limits.json")
        self.bridge = FakeBridge()
        cfg = R.Config(harness=self.harness, owner=self.owner,
                       harness_cmd=[sys.executable, str(HERE / "fake_harness.py")],
                       session_log_dir=self.tmp / "logs", poll_s=0.02, mirror_every_s=0.0,
                       term_grace_s=0.3, cancel_grace_s=0.3, bridge_retry_s=0.3,
                       world_unavailable_after_s=0.0, screenshot_every_s=0.0, bad_request_after_s=0.0)
        self.r = R.Runner(cfg, bridge=self.bridge.client())
        self.r.ensure_dirs()

    def tearDown(self):
        if self.r.session is not None:
            self.r.session.proc.kill()
            self.r.session.proc.wait()
        self.bridge.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers -----------------------------------------------------------------
    def card(self, name, **fake):
        p = self.harness / "cards" / "queue" / f"{name}.json"
        p.write_text(json.dumps({"goal_id": name, "fake": fake}))
        return p

    def events(self, kind=None):
        out = [json.loads(p.read_text()) for p in sorted((self.harness / "inbox").glob("*.json"))]
        return [e for e in out if kind is None or e["event"] == kind]

    def kinds(self):
        return [e["event"] for e in self.events()]

    def run_until(self, pred, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            self.r.step()
            if pred():
                return
            time.sleep(0.02)
        self.fail(f"condition not reached; events: {self.kinds()}")

    def idle(self):
        return self.r.session is None

    def started(self, card_id):
        """The fake harness is running and has its SIGTERM handler installed."""
        return lambda: self.r.session is not None and (self.harness / "reports" / card_id / "ready").exists()

    def status(self):
        return json.loads((self.harness / "status.json").read_text())["state"]

    # cards -------------------------------------------------------------------
    def test_two_cards_play_in_order_then_queue_empty(self):
        self.card("a-first")
        self.card("b-second")
        self.run_until(lambda: "queue_empty" in self.kinds())
        self.assertEqual(self.kinds(), ["card_started", "card_ended", "card_started", "card_ended", "queue_empty"])
        self.assertEqual([e["card_id"] for e in self.events("card_started")], ["a-first", "b-second"])
        self.assertTrue((self.harness / "cards" / "done" / "a-first.json").exists())
        outcome = json.loads((self.harness / "cards" / "done" / "a-first.outcome.json").read_text())
        self.assertEqual(outcome["outcome"], "goal_reached")
        self.assertTrue(outcome["report"].startswith("reports/a-first/"))
        self.assertEqual(self.status(), "idle_queue_empty")
        self.assertNotIn("POST /act", self.bridge.calls)

    def test_flags_and_decider_default_first(self):
        self.card("c")
        self.run_until(lambda: "card_ended" in self.kinds())
        argv = json.loads((self.harness / "reports" / "c" / "argv.json").read_text())
        self.assertEqual(argv[argv.index("--decider") + 1], "first")
        self.assertEqual(argv[argv.index("--stop-file") + 1], str(self.owner / "STOP"))
        self.assertEqual(argv[argv.index("--limits") + 1], str(self.owner / "limits.json"))
        self.assertEqual(argv[argv.index("--out") + 1], str(self.harness / "reports" / "c"))

    def test_decider_from_runner_json_and_bad_value_falls_back_to_first(self):
        (self.harness / "runner.json").write_text('{"decider": "random"}')
        self.assertEqual(self.r.decider(), "random")
        (self.harness / "runner.json").write_text('{"decider": "rm -rf"}')
        self.assertEqual(self.r.decider(), "first")
        (self.harness / "runner.json").write_text("not json")
        self.assertEqual(self.r.decider(), "first")

    def test_no_options_is_its_own_event(self):
        self.card("w", stop_reason="no_options: no reachable tree at Woodcutting 17: Willow needs 30")
        self.run_until(lambda: "card_needs_astra" in self.kinds())
        e = self.events("card_needs_astra")[0]
        self.assertIn("Willow needs 30", e["facts"]["stop_reason"])
        self.assertTrue((self.harness / "cards" / "done" / "w.json").exists())

    def test_other_stop_reasons_end_the_card(self):
        self.card("s", stop_reason="stagnation: 6 actions with no XP or item change")
        self.run_until(lambda: "card_ended" in self.kinds())
        self.assertEqual(json.loads((self.harness / "cards" / "done" / "s.outcome.json").read_text())["outcome"], "ended")

    def test_exit_2_rejected_with_stdout_tail_and_scrubbed(self):
        self.card("bad", exit=2, report=False, print="goal invalid, not running:\n  PASSWORD=hunter2 /bot?password=abc")
        self.run_until(lambda: "card_rejected" in self.kinds())
        tail = self.events("card_rejected")[0]["facts"]["output_tail"]
        self.assertIn("goal invalid, not running", tail)
        self.assertNotIn("hunter2", tail)
        self.assertNotIn("password=abc", tail)
        self.assertTrue((self.harness / "cards" / "failed" / "bad.json").exists())

    def test_crash_or_no_report_is_card_failed(self):
        self.card("boom", exit=1, report=False, print="Traceback: boom")
        self.run_until(lambda: "card_failed" in self.kinds())
        self.assertIn("Traceback: boom", self.events("card_failed")[0]["facts"]["output_tail"])
        self.card("silent", report=False)
        self.run_until(lambda: len(self.events("card_failed")) == 2)

    def test_unsafe_card_name_rejected(self):
        (self.harness / "cards" / "queue" / "-x.json").write_text("{}")
        self.run_until(lambda: "card_rejected" in self.kinds())
        self.assertIsNone(self.r.session)

    def test_dotfiles_in_queue_ignored(self):
        (self.harness / "cards" / "queue" / ".half.json.tmp").write_text("{")
        (self.harness / "cards" / "queue" / ".half.json").write_text("{")
        self.run_until(lambda: "queue_empty" in self.kinds())
        self.assertNotIn("card_started", self.kinds())

    def test_replayed_card_keeps_both_outcomes(self):
        self.card("again")
        self.run_until(lambda: len(self.events("card_ended")) == 1)
        self.card("again")
        self.run_until(lambda: len(self.events("card_ended")) == 2)
        self.assertEqual(len(list((self.harness / "cards" / "done").glob("again*.outcome.json"))), 2)

    # STOP --------------------------------------------------------------------
    def test_stop_before_start_runs_nothing(self):
        (self.owner / "STOP").write_text("")
        self.card("x")
        for _ in range(10):
            self.r.step()
        self.assertEqual(self.kinds(), ["stopped_by_owner"])
        self.assertEqual(self.status(), "stopped_by_owner")
        (self.owner / "STOP").unlink()
        self.run_until(lambda: "card_ended" in self.kinds())

    def test_stop_mid_card_polite_then_replay(self):
        self.card("long", sleep=30)
        self.run_until(self.started("long"))
        (self.owner / "STOP").write_text("")
        self.run_until(lambda: "card_interrupted" in self.kinds())
        e = self.events("card_interrupted")[0]
        self.assertEqual(e["facts"]["reason"], "stop")
        self.assertTrue(e["facts"]["stop_reason"].startswith("sigterm"))
        self.assertTrue((self.harness / "cards" / "active" / "long.json").exists(), "a STOPped card must not be filed as done")
        self.assertNotIn("POST /cancel", self.bridge.calls)
        # replay once STOP is gone (make it quick this time)
        (self.harness / "cards" / "active" / "long.json").write_text(json.dumps({"fake": {}}))
        (self.owner / "STOP").unlink()
        self.run_until(lambda: "card_ended" in self.kinds())
        self.assertTrue((self.harness / "cards" / "done" / "long.json").exists())

    def test_stop_cancels_an_action_left_running_by_the_harness(self):
        self.card("chop", sleep=30)
        self.run_until(self.started("chop"))
        self.bridge.health["busy"] = "a-123"          # the harness died, its action did not
        (self.owner / "STOP").write_text("")
        self.run_until(lambda: "card_interrupted" in self.kinds())
        self.assertEqual(self.bridge.calls.count("POST /cancel"), 1)

    def test_stop_mid_card_escalates_cancel_once_then_kill(self):
        self.card("stubborn", sleep=60, ignore_term=True)
        self.run_until(self.started("stubborn"))
        (self.owner / "STOP").write_text("")
        self.run_until(lambda: "card_interrupted" in self.kinds())
        self.assertEqual(self.bridge.calls.count("POST /cancel"), 1)
        e = self.events("card_interrupted")[0]
        self.assertTrue(e["facts"]["killed"])
        self.assertIsNone(e["facts"]["report"])
        self.assertTrue((self.harness / "cards" / "active" / "stubborn.json").exists())

    def test_cancel_stop_unconfirmed_needs_ryan_and_blocks(self):
        self.bridge.cancel_body = {"cancelled": False, "error": "stop_unconfirmed", "stop": {"confirmed": False}}
        self.card("stuck", sleep=60, ignore_term=True)
        self.run_until(self.started("stuck"))
        (self.owner / "STOP").write_text("")
        self.run_until(lambda: "card_interrupted" in self.kinds())
        self.assertIn("needs_ryan", self.kinds())
        self.bridge.health["stop_failure"] = {"confirmed": False}   # what the real bridge now reports
        (self.owner / "STOP").unlink()
        for _ in range(20):
            self.r.step()
        self.assertIsNone(self.r.session)
        self.assertEqual(self.status(), "needs_ryan")
        self.assertEqual(self.bridge.calls.count("POST /cancel"), 1, "never cancel again hoping for a different answer")
        self.assertEqual(len(self.events("needs_ryan")), 1)
        # Ryan restarts the player: bridge is clean, the card plays again
        self.bridge.health["stop_failure"] = None
        (self.harness / "cards" / "active" / "stuck.json").write_text(json.dumps({"fake": {}}))
        self.run_until(lambda: "card_ended" in self.kinds())

    def test_stop_failure_at_start_needs_ryan_once(self):
        self.bridge.health["stop_failure"] = {"confirmed": False}
        self.card("x")
        for _ in range(10):
            self.r.step()
        self.assertEqual(self.kinds(), ["needs_ryan"])
        self.assertIsNone(self.r.session)

    def test_shutdown_mid_card_then_restart_replays(self):
        self.card("s1", sleep=30)
        self.run_until(self.started("s1"))
        self.r.shutting_down = True
        self.run_until(self.idle)
        self.assertNotIn("card_interrupted", self.kinds())          # reported once, at restart
        self.assertTrue((self.harness / "cards" / "active" / "s1.json").exists())
        (self.harness / "cards" / "active" / "s1.json").write_text(json.dumps({"fake": {}}))
        r2 = R.Runner(self.r.cfg, bridge=self.bridge.client())
        r2.recover()
        self.assertEqual(self.events("card_interrupted")[0]["card_id"], "s1")
        self.r = r2
        self.card("a-later")
        self.run_until(lambda: len(self.events("card_ended")) == 1)
        starts = [e["card_id"] for e in self.events("card_started")]
        self.assertEqual(starts[:2], ["s1", "s1"], "the interrupted card plays again before anything new")

    # bridge ------------------------------------------------------------------
    def test_bridge_unavailable_exit_3_keeps_card_and_waits(self):
        self.card("drop", exit=3, report=False)
        self.run_until(lambda: "card_interrupted" in self.kinds())
        self.assertEqual(self.events("card_interrupted")[0]["facts"]["reason"], "bridge_unavailable")
        self.assertTrue((self.harness / "cards" / "active" / "drop.json").exists())
        self.r.step()
        self.assertIsNone(self.r.session)                            # waits before replaying

    def test_waits_for_world_and_busy_bridge(self):
        self.card("x")
        self.bridge.health["in_game"] = False
        self.r.step()
        self.assertEqual(self.status(), "waiting_for_world")
        self.bridge.health.update(in_game=True, busy="a-123")
        self.r.step()
        self.assertEqual(self.status(), "waiting_bridge_busy")
        self.assertIsNone(self.r.session)

    def test_limits_invalid_or_missing_runs_nothing(self):
        (self.owner / "limits.json").write_text('{"version": 1}')
        self.card("x")
        for _ in range(5):
            self.r.step()
        self.assertEqual(self.kinds(), ["limits_invalid"])
        self.assertTrue((self.harness / "cards" / "queue" / "x.json").exists(), "a card is not blamed for bad limits")
        (self.owner / "limits.json").unlink()
        self.r.clear_once("limits")
        self.r.step()
        self.assertIn("does not exist", self.events("limits_invalid")[-1]["facts"]["problems"][0])

    # mirror ------------------------------------------------------------------
    def test_mirror_one_observe_call_for_both_files(self):
        self.r.mirror(force=True)
        w = self.harness / "world"
        obs = json.loads((w / "observe.json").read_text())
        self.assertEqual(obs["tick"], 7)
        self.assertEqual((w / "summary.txt").read_text().strip(), "Woodcutting 17. Nearby: Tree, Willow")
        self.assertTrue(json.loads((w / "health.json").read_text())["reachable"])
        self.assertTrue((w / "verbs.json").exists())
        self.assertEqual(sum(c.startswith("GET /observe") for c in self.bridge.calls), 1)
        self.assertNotIn("hunter2", (w / "observe.json").read_text())

    def test_exits_after_losing_the_bridge_so_docker_reattaches_it(self):
        self.r.cfg.exit_after_unreachable_s = 0.2
        self.assertFalse(self.r.should_exit_unreachable())
        self.bridge.down = True
        self.r.bridge = self.bridge.client()
        self.r.mirror(force=True)
        self.assertFalse(self.r.should_exit_unreachable())          # not yet long enough
        time.sleep(0.3)
        self.r.mirror(force=True)
        self.assertTrue(self.r.should_exit_unreachable())
        self.bridge.down = False                                    # bridge back: no exit
        self.r.bridge = self.bridge.client()
        self.r.mirror(force=True)
        self.assertFalse(self.r.should_exit_unreachable())

    def test_never_exits_for_the_bridge_while_a_session_runs(self):
        self.r.cfg.exit_after_unreachable_s = 0.0
        self.card("busy", sleep=30)
        self.run_until(self.started("busy"))
        self.r._unreachable_since = self.r.clock() - 100
        self.assertFalse(self.r.should_exit_unreachable())

    def test_mirror_bridge_down(self):
        self.bridge.down = True
        self.r.bridge = self.bridge.client()
        self.r.mirror(force=True)
        self.r.mirror(force=True)
        self.assertFalse(json.loads((self.harness / "world" / "health.json").read_text())["reachable"])
        self.assertEqual(self.kinds(), ["world_unavailable"])

    # screenshots -------------------------------------------------------------
    def test_screenshot_ready(self):
        (self.harness / "requests" / ".screenshot-1.json.tmp").write_text("{")
        (self.harness / "requests" / "screenshot-1.json").write_text('{"reason": "which tree?"}')
        self.r.handle_requests()
        self.assertEqual((self.harness / "world" / "screenshots" / "1.png").read_bytes(), PNG)
        self.assertEqual(self.events("screenshot_ready")[0]["facts"]["path"], "world/screenshots/1.png")
        self.assertFalse((self.harness / "requests" / "screenshot-1.json").exists())
        self.assertTrue((self.harness / "requests" / ".screenshot-1.json.tmp").exists(), "dotfiles are not requests")

    def test_screenshot_empty_picture_fails(self):
        self.bridge.screenshot = (422, {"error": "no_picture", "message": "the lite client cannot draw"})
        (self.harness / "requests" / "screenshot-2.json").write_text("{}")
        self.r.handle_requests()
        self.assertEqual(self.events("screenshot_failed")[0]["facts"]["reason"], "the lite client cannot draw")
        self.assertFalse((self.harness / "world" / "screenshots" / "2.png").exists())

    def test_screenshot_not_png_or_no_route_fails(self):
        self.bridge.screenshot = (200, {"png_base64": base64.b64encode(b"GIF89a").decode()})
        (self.harness / "requests" / "screenshot-3.json").write_text("{}")
        self.r.handle_requests()
        self.bridge.screenshot = (404, {"error": "not_found"})
        (self.harness / "requests" / "screenshot-4.json").write_text("{}")
        self.r.handle_requests()
        reasons = [e["facts"]["reason"] for e in self.events("screenshot_failed")]
        self.assertIn("not a PNG", reasons[0])
        self.assertIn("no /screenshot route", reasons[1])

    def test_screenshot_rate_limit_and_bad_json(self):
        self.r.cfg.screenshot_every_s = 60
        (self.harness / "requests" / "screenshot-a.json").write_text("{}")
        (self.harness / "requests" / "screenshot-b.json").write_text("{}")
        self.r.handle_requests()
        self.assertEqual(len(self.events("screenshot_ready")), 1)
        self.assertTrue((self.harness / "requests" / "screenshot-b.json").exists(), "left for later, not dropped")
        self.r.cfg.screenshot_every_s = 0
        (self.harness / "requests" / "screenshot-b.json").write_text("{half")
        self.r.handle_requests()
        self.assertEqual(self.events("screenshot_failed")[0]["facts"]["reason"], "request is not valid JSON")

    # scrub -------------------------------------------------------------------
    def test_scrub(self):
        s = R.scrub("open /bot?bot=Astra&password=s3cret&x=1 PASSWORD=abc x-bridge-token: t0k")
        self.assertNotIn("s3cret", s)
        self.assertNotIn("abc", s)
        self.assertNotIn("t0k", s)
        self.assertIn("&x=1", s)


if __name__ == "__main__":
    unittest.main()
