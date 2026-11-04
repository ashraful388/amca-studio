from __future__ import annotations

import unittest

from amca.tools.python_sandbox import (
    SandboxPolicyError,
    run_python_function_cases,
)


class PythonSandboxTests(unittest.TestCase):
    def test_declared_values_and_exceptions_are_checked(self) -> None:
        report = run_python_function_cases(
            """
def divide_or_reject(value, divisor):
    if divisor == 0:
        raise ValueError("zero")
    return value // divisor
""",
            entrypoint="divide_or_reject",
            cases=[
                {
                    "name": "value",
                    "args": {"value": 12, "divisor": 3},
                    "expected": 4,
                },
                {
                    "name": "invalid",
                    "args": {"value": 12, "divisor": 0},
                    "expected_exception": "ValueError",
                },
            ],
            timeout_seconds=2,
        )

        self.assertEqual(report.status, "passed", report.error)
        self.assertTrue(report.passed)
        self.assertEqual([case.name for case in report.cases], ["value", "invalid"])

    def test_network_and_process_imports_are_rejected_before_execution(self) -> None:
        for module in ("socket", "subprocess", "os", "pathlib"):
            with self.subTest(module=module):
                with self.assertRaises(SandboxPolicyError):
                    run_python_function_cases(
                        f"import {module}\ndef answer():\n    return 1\n",
                        entrypoint="answer",
                        cases=[],
                    )

    def test_deadline_stops_infinite_candidate(self) -> None:
        report = run_python_function_cases(
            "def spin():\n    while True:\n        pass\n",
            entrypoint="spin",
            cases=[{"name": "spin", "args": {}, "expected": None}],
            timeout_seconds=0.15,
        )

        self.assertEqual(report.status, "timeout")
        self.assertFalse(report.passed)

    def test_dunder_escape_is_rejected(self) -> None:
        with self.assertRaises(SandboxPolicyError):
            run_python_function_cases(
                "def escape():\n    return (1).__class__.__mro__\n",
                entrypoint="escape",
                cases=[],
            )


if __name__ == "__main__":
    unittest.main()
