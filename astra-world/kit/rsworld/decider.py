"""Stand-ins for Jev, so the harness can be built and tested without paid calls.

The harness asks one question at a time: here is the bounded state, here are the
legal options (opaque ids the harness built from the current snapshot), pick one.

    q = Question(question_id="q1", snapshot_id=snap.snapshot_id, decided_at_tick=snap.tick,
                 policy="Mine iron; avoid other players' rocks.",
                 state={"hp": 10, "inventory_free": 10, ...},
                 options=[Option("rock-1", "Iron rock 2 tiles east"), Option("bank", "Walk to bank")])
    answer = decider.decide(q)          # Answer(choice_id, confidence, provenance, ...)

Every Answer says where it came from (`provenance`). Mock answers are never Jev
results and must never be counted as Jev results (10-31 spec R12).

The real Jev client is not here on purpose. It goes through the protected decision
service (the provider key lives there, never with the harness); its request/response
contract is decided in the Jev-project review (D03/D04). JevDecider below only
marks where it plugs in.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Option:
    option_id: str
    label: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Question:
    question_id: str
    snapshot_id: str
    decided_at_tick: int
    policy: str
    state: dict[str, Any]
    options: list[Option]


@dataclass(frozen=True)
class Answer:
    question_id: str
    choice_id: str
    confidence: float | None
    provenance: str                  # "mock-random", "mock-first", "mock-scripted", "jev:<model>" ...
    distribution: dict[str, float] | None = None
    usage: dict[str, Any] | None = None


class Decider(Protocol):
    def decide(self, q: Question) -> Answer: ...


class DeciderError(Exception):
    """The decider could not answer. The harness must not act on a guess (10-31 §4)."""


def _require_options(q: Question) -> None:
    if not q.options:
        raise DeciderError(f"{q.question_id}: no legal options to choose from")
    ids = [o.option_id for o in q.options]
    if len(set(ids)) != len(ids):
        raise DeciderError(f"{q.question_id}: duplicate option ids")


class RandomDecider:
    """Legal-random baseline. Seeded, so runs are reproducible."""

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def decide(self, q: Question) -> Answer:
        _require_options(q)
        pick = self._rng.choice(q.options)
        return Answer(q.question_id, pick.option_id, 1 / len(q.options), "mock-random",
                      {o.option_id: 1 / len(q.options) for o in q.options})


class FirstOptionDecider:
    """Always the first option: a 'simple rule' baseline when options are ordered by the harness."""

    def decide(self, q: Question) -> Answer:
        _require_options(q)
        return Answer(q.question_id, q.options[0].option_id, None, "mock-first")


class ScriptedDecider:
    """Replays a fixed list of choice ids (for tests). Raises when the script runs out
    or names an option that is not offered, the same way a bad Jev answer would be rejected."""

    def __init__(self, choices: list[str]):
        self._choices = list(choices)

    def decide(self, q: Question) -> Answer:
        _require_options(q)
        if not self._choices:
            raise DeciderError(f"{q.question_id}: scripted decider exhausted")
        choice = self._choices.pop(0)
        return Answer(q.question_id, choice, None, "mock-scripted")


class JevDecider:
    """Placeholder for the real thing. See module docstring."""

    def __init__(self, *_: Any, **__: Any):
        raise NotImplementedError(
            "JevDecider talks to the protected decision service; its contract comes from the "
            "Jev-project review (D03/D04). Use RandomDecider / FirstOptionDecider until then."
        )


def validate_answer(q: Question, a: Answer) -> list[str]:
    """Hard checks before an answer may drive an action (10-31 R06). Empty list = usable.

    The harness adds its own checks (freshness, budget, guards) on top of these.
    """
    problems = []
    ids = {o.option_id for o in q.options}
    if a.question_id != q.question_id:
        problems.append(f"answer is for {a.question_id}, not {q.question_id}")
    if a.choice_id not in ids:
        problems.append(f"choice '{a.choice_id}' is not one of the offered options")
    if a.confidence is not None and not (isinstance(a.confidence, (int, float)) and 0.0 <= a.confidence <= 1.0):
        problems.append("confidence must be in [0, 1] or absent")
    if a.distribution is not None:
        if set(a.distribution) - ids:
            problems.append("distribution names options that were not offered")
        vals = list(a.distribution.values())
        if any(not isinstance(v, (int, float)) or v != v or v < 0 or v > 1 for v in vals):
            problems.append("distribution values must be finite and in [0, 1]")
        elif vals and abs(sum(vals) - 1.0) > 1e-3:
            problems.append(f"distribution sums to {sum(vals):.4f}, not 1")
    if not a.provenance:
        problems.append("missing provenance")
    return problems
