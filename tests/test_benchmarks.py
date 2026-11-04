from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


class BenchmarkFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = Path(__file__).resolve().parents[1] / "benchmarks" / "tasks.json"
        cls.document = json.loads(source.read_text(encoding="utf-8"))
        cls.tasks = cls.document["tasks"]

    def test_phase_zero_fixture_shape_and_domain_balance(self) -> None:
        self.assertEqual(len(self.tasks), 8)
        identifiers = [task["task_id"] for task in self.tasks]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(
            Counter(task["domain"] for task in self.tasks),
            Counter(
                {
                    "mathematics": 2,
                    "scientific_reasoning": 2,
                    "coding": 2,
                    "research_planning": 2,
                }
            ),
        )

    def test_every_fixture_has_a_complete_machine_readable_contract(self) -> None:
        for task in self.tasks:
            with self.subTest(task=task["task_id"]):
                contract = task["response_contract"]
                contract_kind = contract.get("type", contract.get("kind"))
                self.assertIn(contract_kind, {"object", "python_function"})
                if contract_kind == "object":
                    self.assertTrue(contract["required"])
                else:
                    self.assertTrue(contract["entrypoint"])
                    self.assertTrue(contract["signature"])
                    self.assertFalse(contract["network"])
                criteria = task["criteria"]
                self.assertTrue(criteria)
                self.assertEqual(sum(item["weight"] for item in criteria), 100)
                self.assertEqual(
                    len({item["criterion_id"] for item in criteria}), len(criteria)
                )
                self.assertTrue(all("kind" in item for item in criteria))


if __name__ == "__main__":
    unittest.main()
