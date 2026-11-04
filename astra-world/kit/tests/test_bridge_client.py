"""BridgeClient against a fake bridge (no game needed)."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rsworld.bridge_client import BridgeClient, BridgeUnavailable

ACTS = Path(__file__).resolve().parents[2] / "fixtures" / "acts"


class Fake(BaseHTTPRequestHandler):
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
        if self.headers.get("x-bridge-token") != "t0k":
            return self._send(401, {"error": "unauthorized"})
        if self.path.startswith("/observe"):
            return self._send(200, {"snapshot_id": "r5-t3", "tick": 3, "revision": 5, "captured_ms": 1, "busy": None,
                                    "state": {"tick": 3}})
        self._send(200, {"connected": True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.server.last = body
        if body["verb"] == "walk_to":
            return self._send(200, json.loads((ACTS / "accepted-walk_to.json").read_text()) | {"action_id": body["action_id"]})
        if body["verb"] == "stale":
            return self._send(409, json.loads((ACTS / "refused-stale.json").read_text()))
        return self._send(400, json.loads((ACTS / "refused-target_not_found.json").read_text()))


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = BridgeClient(f"http://127.0.0.1:{cls.srv.server_port}", token="t0k")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_observe(self):
        s = self.c.observe()
        self.assertEqual((s.snapshot_id, s.tick, s.revision), ("r5-t3", 3, 5))

    def test_accepted(self):
        r = self.c.act("walk_to", {"x": 1, "z": 2}, deadline_ms=1000, decided_at_tick=3, max_age_ticks=4)
        self.assertFalse(r.refused)
        self.assertEqual(r.outcome, "completed")
        self.assertTrue(r.sdk_success)
        self.assertEqual(self.srv.last["decided_at_tick"], 3)
        self.assertEqual(self.srv.last["max_age_ticks"], 4)
        self.assertNotIn("expect_revision", self.srv.last)

    def test_refusals(self):
        r = self.c.act("interact_loc", {}, deadline_ms=1000)
        self.assertTrue(r.refused)
        self.assertEqual(r.error_code, "target_not_found")
        self.assertFalse(r.sdk_success)
        r = self.c.act("stale", {}, deadline_ms=1000)
        self.assertEqual((r.refused, r.error_code, r.http_status), (True, "stale", 409))

    def test_bad_token(self):
        c = BridgeClient(self.c.url, token="wrong")
        with self.assertRaises(BridgeUnavailable):
            c.observe()

    def test_unreachable(self):
        with self.assertRaises(BridgeUnavailable):
            BridgeClient("http://127.0.0.1:9", timeout_s=1).health()


if __name__ == "__main__":
    unittest.main()
