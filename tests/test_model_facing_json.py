"""A tool result handed to the model must always be valid JSON.

The result used to be cut at a raw character offset, which left a
half-written JSON object in the model's tool channel - a result the model
could not read at all.  A result that is too long is now REDUCED, and
the reduction says so.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.governed_chat import model_facing_json


class ModelFacingJsonTests(unittest.TestCase):
    def test_a_short_result_is_returned_unchanged(self) -> None:
        value = {"query": "needle", "matches": [{"name": "a.txt"}]}
        text = model_facing_json(value)
        self.assertEqual(json.loads(text), value)

    def test_a_long_result_is_still_valid_json(self) -> None:
        value = {"query": "needle", "matches": [{"name": f"file-{i}.txt"} for i in range(2000)]}
        text = model_facing_json(value)
        self.assertLessEqual(len(text), 6000)
        # The whole point: the model must be able to READ it.
        parsed = json.loads(text)
        self.assertIn("_truncated", parsed)

    def test_a_long_result_that_cannot_be_reduced_is_still_valid_json(self) -> None:
        # A single enormous string cannot be reduced by trimming a list.
        value = {"blob": "x" * 50_000}
        text = model_facing_json(value)
        self.assertLessEqual(len(text), 6000)
        parsed = json.loads(text)
        self.assertIn("_truncated", parsed)

    def test_a_non_dict_result_is_still_valid_json(self) -> None:
        text = model_facing_json(["a", "b", "c"])
        self.assertEqual(json.loads(text), ["a", "b", "c"])
        long_text = model_facing_json([{"x": "y" * 20000} for _ in range(10)])
        self.assertLessEqual(len(long_text), 6000)
        json.loads(long_text)

    def test_the_reduction_says_what_happened(self) -> None:
        value = {"matches": [{"name": f"file-{i}.txt"} for i in range(2000)]}
        parsed = json.loads(model_facing_json(value))
        self.assertIn("too long", str(parsed["_truncated"]).lower())


if __name__ == "__main__":
    unittest.main()

    def test_two_medium_strings_keep_some_content(self) -> None:
        # Two strings of 3500 characters are 7000 characters in all.  The
        # reduction must trim them PROPORTIONALLY, not throw the whole
        # result away: the model needs to see that there were two fields
        # and what was in them.
        value = {"a": "x" * 3500, "b": "y" * 3500}
        parsed = json.loads(model_facing_json(value))
        self.assertLessEqual(len(json.dumps(parsed)), 6000)
        self.assertNotIn("a", parsed, "the fields must not be dropped whole")
        self.assertTrue(parsed.get("a"), "some of the first field must survive")
        self.assertTrue(parsed.get("b"), "some of the second field must survive")

    def test_a_long_list_keeps_as_many_entries_as_fit(self) -> None:
        value = {"entries": [{"n": i} for i in range(2000)]}
        parsed = json.loads(model_facing_json(value))
        self.assertGreater(
            len(parsed.get("entries") or []), 1,
            "a long list must keep more than one entry",
        )

