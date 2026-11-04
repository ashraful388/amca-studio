"""A restricted arithmetic/SymPy bridge for evidence-producing calculations.

Model-provided strings are parsed into a small expression tree.  The module
never passes those strings to ``eval``, ``exec`` or ``sympy.sympify``.
"""

from __future__ import annotations

import ast
import math
import operator
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    operation: str
    output: str
    elapsed_seconds: float
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class UnsafeExpressionError(ValueError):
    """Raised when an expression is outside the supported safe subset."""


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MATH_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


class _ExpressionParser:
    def __init__(
        self,
        *,
        max_nodes: int = 128,
        max_power: int = 100,
        max_integer_bits: int = 4096,
    ) -> None:
        self.max_nodes = max_nodes
        self.max_power = max_power
        self.max_integer_bits = max_integer_bits

    def parse(self, expression: str) -> ast.Expression:
        if not expression.strip():
            raise UnsafeExpressionError("Expression cannot be empty")
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise UnsafeExpressionError(f"Invalid expression: {exc.msg}") from exc
        if sum(1 for _ in ast.walk(tree)) > self.max_nodes:
            raise UnsafeExpressionError("Expression is too complex")
        return tree

    def evaluate(self, expression: str, variables: Mapping[str, float] | None = None) -> Any:
        tree = self.parse(expression)
        normalized_variables: dict[str, float] = {}
        for name, value in dict(variables or {}).items():
            if (
                not isinstance(name, str)
                or not name.isidentifier()
                or name.startswith("_")
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise UnsafeExpressionError(
                    "Variables require safe names and finite numeric values"
                )
            if isinstance(value, int) and value.bit_length() > self.max_integer_bits:
                raise UnsafeExpressionError("Variable integer exceeds the safety limit")
            normalized_variables[name] = value
        return self._evaluate_node(tree.body, normalized_variables)

    def _evaluate_node(self, node: ast.AST, variables: Mapping[str, float]) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if isinstance(node.value, int) and node.value.bit_length() > self.max_integer_bits:
                raise UnsafeExpressionError("Integer literal exceeds the safety limit")
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise UnsafeExpressionError("Numeric literals must be finite")
            return node.value
        if isinstance(node, ast.Name):
            if node.id in variables:
                return variables[node.id]
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            raise UnsafeExpressionError(f"Unknown name: {node.id}")
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            return _UNARY_OPERATORS[type(node.op)](self._evaluate_node(node.operand, variables))
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = self._evaluate_node(node.left, variables)
            right = self._evaluate_node(node.right, variables)
            if isinstance(node.op, ast.Pow):
                if abs(right) > self.max_power:
                    raise UnsafeExpressionError("Exponent exceeds the configured safety limit")
                if isinstance(left, int) and isinstance(right, int) and right > 0:
                    estimated_bits = max(1, abs(left).bit_length()) * right
                    if estimated_bits > self.max_integer_bits:
                        raise UnsafeExpressionError(
                            "Power result would exceed the configured safety limit"
                        )
            result = _BINARY_OPERATORS[type(node.op)](left, right)
            if isinstance(result, int) and result.bit_length() > self.max_integer_bits:
                raise UnsafeExpressionError("Integer result exceeds the configured safety limit")
            if isinstance(result, float) and not math.isfinite(result):
                raise UnsafeExpressionError("Numeric result is not finite")
            return result
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = _MATH_FUNCTIONS.get(node.func.id)
            if function is None or node.keywords:
                raise UnsafeExpressionError(f"Unsupported function: {node.func.id}")
            arguments = [self._evaluate_node(item, variables) for item in node.args]
            result = function(*arguments)
            if isinstance(result, float) and not math.isfinite(result):
                raise UnsafeExpressionError("Numeric result is not finite")
            return result
        raise UnsafeExpressionError(f"Unsupported syntax: {type(node).__name__}")

    def to_sympy(self, expression: str, sympy: Any) -> Any:
        tree = self.parse(expression)
        return self._sympy_node(tree.body, sympy)

    def _sympy_node(self, node: ast.AST, sympy: Any) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return sympy.Integer(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            return sympy.Float(node.value)
        if isinstance(node, ast.Name):
            if not node.id.isidentifier() or node.id.startswith("_"):
                raise UnsafeExpressionError(f"Unsupported symbol: {node.id}")
            if node.id == "pi":
                return sympy.pi
            if node.id == "e":
                return sympy.E
            return sympy.Symbol(node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            return self._sympy_node(node.operand, sympy)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self._sympy_node(node.operand, sympy)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = self._sympy_node(node.left, sympy)
            right = self._sympy_node(node.right, sympy)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return sympy.floor(left / right)
            if isinstance(node.op, ast.Mod):
                return sympy.Mod(left, right)
            if isinstance(node.op, ast.Pow):
                if getattr(right, "is_number", False) and abs(float(right)) > self.max_power:
                    raise UnsafeExpressionError("Exponent exceeds the configured safety limit")
                return left**right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            functions = {
                "abs": sympy.Abs,
                "sqrt": sympy.sqrt,
                "sin": sympy.sin,
                "cos": sympy.cos,
                "tan": sympy.tan,
                "asin": sympy.asin,
                "acos": sympy.acos,
                "atan": sympy.atan,
                "exp": sympy.exp,
                "log": sympy.log,
            }
            function = functions.get(node.func.id)
            if function is None:
                raise UnsafeExpressionError(f"Unsupported symbolic function: {node.func.id}")
            return function(*(self._sympy_node(arg, sympy) for arg in node.args))
        raise UnsafeExpressionError(f"Unsupported symbolic syntax: {type(node).__name__}")


class MathTool:
    """Run deterministic arithmetic or optional symbolic operations."""

    def __init__(self) -> None:
        self._parser = _ExpressionParser()

    def execute(
        self,
        operation: str,
        expression: str,
        *,
        symbol: str | None = None,
        variables: Mapping[str, float] | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        try:
            if operation == "evaluate":
                output = repr(self._parser.evaluate(expression, variables))
                engine = "python_ast"
            elif operation in {"simplify", "differentiate", "solve"}:
                output = self._symbolic(operation, expression, symbol)
                engine = "sympy"
            else:
                raise ValueError(f"Unknown math operation: {operation}")
            return ToolResult(
                ok=True,
                operation=operation,
                output=output,
                elapsed_seconds=time.perf_counter() - started,
                metadata={"engine": engine},
            )
        except Exception as exc:  # boundary converts tool failures into evidence
            return ToolResult(
                ok=False,
                operation=operation,
                output="",
                elapsed_seconds=time.perf_counter() - started,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _symbolic(self, operation: str, expression: str, symbol: str | None) -> str:
        try:
            import sympy  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "SymPy is optional; install the 'science' extra to use symbolic operations"
            ) from exc

        if operation == "solve":
            if expression.count("=") > 1:
                raise UnsafeExpressionError("Solve accepts at most one equality")
            if "=" in expression:
                left_text, right_text = expression.split("=", 1)
                parsed = sympy.Eq(
                    self._parser.to_sympy(left_text, sympy),
                    self._parser.to_sympy(right_text, sympy),
                )
            else:
                parsed = self._parser.to_sympy(expression, sympy)
            if not symbol or not symbol.isidentifier() or symbol.startswith("_"):
                raise UnsafeExpressionError("Solve requires a safe symbol name")
            result = sympy.solve(parsed, sympy.Symbol(symbol))
        else:
            parsed = self._parser.to_sympy(expression, sympy)
            if operation == "simplify":
                result = sympy.simplify(parsed)
            else:
                if not symbol or not symbol.isidentifier() or symbol.startswith("_"):
                    raise UnsafeExpressionError("Differentiate requires a safe symbol name")
                result = sympy.diff(parsed, sympy.Symbol(symbol))
        return str(result)
