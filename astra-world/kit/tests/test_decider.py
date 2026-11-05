import unittest

from rsworld.decider import (Answer, DeciderError, FirstOptionDecider, JevDecider, Option, Question,
                             RandomDecider, ScriptedDecider, validate_answer)

Q = Question("q1", "r1-t1", 1, "chop trees", {"hp": 10},
             [Option("tree-a", "Tree 2 tiles east"), Option("tree-b", "Tree 5 tiles north"), Option("bank", "Go bank")])


class DeciderTest(unittest.TestCase):
    def test_random_is_seeded_and_legal(self):
        a = [RandomDecider(7).decide(Q).choice_id for _ in range(3)]
        b = [RandomDecider(7).decide(Q).choice_id for _ in range(3)]
        self.assertEqual(a, b)
        ans = RandomDecider(1).decide(Q)
        self.assertEqual(validate_answer(Q, ans), [])
        self.assertEqual(ans.provenance, "mock-random")

    def test_first(self):
        self.assertEqual(FirstOptionDecider().decide(Q).choice_id, "tree-a")

    def test_scripted_bad_choice_is_caught_by_validation(self):
        ans = ScriptedDecider(["fly"]).decide(Q)
        self.assertTrue(validate_answer(Q, ans))

    def test_scripted_exhausted(self):
        d = ScriptedDecider([])
        with self.assertRaises(DeciderError):
            d.decide(Q)

    def test_no_options(self):
        with self.assertRaises(DeciderError):
            RandomDecider().decide(Question("q2", "s", 1, "", {}, []))

    def test_validate_answer_shapes(self):
        bad = Answer("q1", "tree-a", 1.5, "x", {"tree-a": 0.7, "tree-b": 0.7})
        probs = validate_answer(Q, bad)
        self.assertTrue(any("confidence" in p for p in probs))
        self.assertTrue(any("sums" in p for p in probs))
        self.assertTrue(validate_answer(Q, Answer("q9", "tree-a", None, "x")))
        self.assertTrue(validate_answer(Q, Answer("q1", "tree-a", None, "")))
        self.assertTrue(validate_answer(Q, Answer("q1", "tree-a", None, "x", {"ghost": 1.0})))

    def test_jev_placeholder_refuses(self):
        with self.assertRaises(NotImplementedError):
            JevDecider()


if __name__ == "__main__":
    unittest.main()
