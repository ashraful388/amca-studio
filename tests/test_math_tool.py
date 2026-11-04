from __future__ import annotations

import unittest

from amca.tools import MathTool


class MathToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = MathTool()

    def test_evaluates_restricted_arithmetic(self) -> None:
        result = self.tool.execute("evaluate", "sqrt(81) + 2 ** 3")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output, "17.0")
        self.assertEqual(result.metadata["engine"], "python_ast")

    def test_rejects_import_or_attribute_access(self) -> None:
        result = self.tool.execute("evaluate", "__import__('os').getcwd()")
        self.assertFalse(result.ok)
        self.assertIn("Unsupported", result.error or "")

    def test_rejects_pathological_exponent(self) -> None:
        result = self.tool.execute("evaluate", "2 ** 10000")
        self.assertFalse(result.ok)
        self.assertIn("Exponent", result.error or "")

    def test_rejects_nonnumeric_variables(self) -> None:
        result = self.tool.execute(
            "evaluate", "x * 1000000000", variables={"x": "abc"}
        )
        self.assertFalse(result.ok)
        self.assertIn("finite numeric", result.error or "")

    def test_rejects_power_before_allocating_huge_integer(self) -> None:
        result = self.tool.execute("evaluate", "(2 ** 100) ** 100")
        self.assertFalse(result.ok)
        self.assertIn("would exceed", result.error or "")

    def test_rejects_nonfinite_numeric_result(self) -> None:
        result = self.tool.execute("evaluate", "1e309")
        self.assertFalse(result.ok)
        self.assertIn("finite", result.error or "")


if __name__ == "__main__":
    unittest.main()
