"""Stands in for Astra's astra_loop.py in the runner tests.

Behaviour comes from the card's "fake" key:
  {"stop_reason": "goal_reached: ...", "exit": 0, "sleep": 0, "ignore_term": false,
   "report": true, "print": "text to stdout"}
It records its argv to <out>/argv.json so tests can check the flags.
"""
import argparse
import json
import signal
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--goal")
ap.add_argument("--decider")
ap.add_argument("--out")
ap.add_argument("--limits")
ap.add_argument("--stop-file")
a = ap.parse_args()

card = json.loads(Path(a.goal).read_text())
fake = card.get("fake", {})
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
(out / "argv.json").write_text(json.dumps(sys.argv[1:]))
sid = f"s-{time.time_ns()}"


def report(stop_reason):
    if fake.get("report", True):
        (out / f"{sid}.json").write_text(json.dumps({"session_id": sid, "stop_reason": stop_reason}))
        (out / f"{sid}.meta.json").write_text(json.dumps({"world": "fake"}))


def on_term(*_):
    if fake.get("ignore_term"):
        return
    report("sigterm: interrupted by the runner")
    sys.exit(0)


signal.signal(signal.SIGTERM, on_term)
(out / "ready").write_text("")            # tests wait for this before sending signals
if fake.get("print"):
    print(fake["print"], flush=True)
deadline = time.time() + float(fake.get("sleep", 0))
while time.time() < deadline:
    time.sleep(0.02)
if fake.get("exit", 0) == 0:
    report(fake.get("stop_reason", "goal_reached: target reached"))
sys.exit(int(fake.get("exit", 0)))
