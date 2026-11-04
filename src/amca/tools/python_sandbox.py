"""Bounded, network-disabled execution for benchmark Python functions.

This is deliberately narrower than a general Python tool. Candidate source is
first constrained by an AST policy, then executed in an isolated interpreter
inside a temporary directory. The child receives no environment variables,
has a wall-clock deadline, and is assigned a Windows Job Object (or POSIX
resource limits) when the platform supports it.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_SOURCE_BYTES = 64 * 1024
MAX_AST_NODES = 12_000
MAX_RESULT_BYTES = 2 * 1024 * 1024
DEFAULT_MEMORY_BYTES = 256 * 1024 * 1024
SAFE_IMPORTS = frozenset(
    {
        "bisect",
        "collections",
        "dataclasses",
        "functools",
        "heapq",
        "itertools",
        "math",
        "operator",
        "typing",
    }
)
FORBIDDEN_CALLS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "open",
        "print",
        "setattr",
        "vars",
    }
)


class SandboxPolicyError(ValueError):
    """Raised before execution when candidate source exceeds the safe subset."""


@dataclass(frozen=True, slots=True)
class PythonCaseResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PythonSandboxReport:
    status: str
    cases: tuple[PythonCaseResult, ...]
    elapsed_seconds: float
    memory_limit_enforced: bool
    source_features: tuple[str, ...]
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed" and all(case.passed for case in self.cases)

    def case(self, name: str) -> PythonCaseResult | None:
        return next((case for case in self.cases if case.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "cases": [case.to_dict() for case in self.cases],
            "elapsed_seconds": self.elapsed_seconds,
            "memory_limit_enforced": self.memory_limit_enforced,
            "source_features": list(self.source_features),
            "error": self.error,
        }


class _SourcePolicy(ast.NodeVisitor):
    def __init__(self) -> None:
        self.features: set[str] = set()
        self.node_count = 0

    def generic_visit(self, node: ast.AST) -> None:
        self.node_count += 1
        if self.node_count > MAX_AST_NODES:
            raise SandboxPolicyError(
                f"candidate source exceeds the {MAX_AST_NODES}-node AST limit"
            )
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root not in SAFE_IMPORTS:
                raise SandboxPolicyError(f"import of {root!r} is not allowed")
            self.features.add(f"import:{root}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or node.module is None:
            raise SandboxPolicyError("relative imports are not allowed")
        root = node.module.split(".", 1)[0]
        if root not in SAFE_IMPORTS:
            raise SandboxPolicyError(f"import of {root!r} is not allowed")
        self.features.add(f"import:{root}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise SandboxPolicyError("private and dunder attribute access is not allowed")
        self.features.add(f"attribute:{node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise SandboxPolicyError("dunder names are not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                raise SandboxPolicyError(f"call to {node.func.id!r} is not allowed")
            self.features.add(f"call:{node.func.id}")
        elif isinstance(node.func, ast.Attribute):
            self.features.add(f"call:{node.func.attr}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and len(node.value) > 16_384:
            raise SandboxPolicyError("string literal exceeds the 16384-character limit")
        if isinstance(node.value, (bytes, bytearray)) and len(node.value) > 16_384:
            raise SandboxPolicyError("bytes literal exceeds the 16384-byte limit")
        if isinstance(node.value, int) and node.value.bit_length() > 4096:
            raise SandboxPolicyError("integer literal exceeds the 4096-bit limit")
        self.generic_visit(node)


def validate_python_source(source: str, *, entrypoint: str) -> tuple[str, ...]:
    """Validate source and return stable structural features for rubric checks."""

    encoded = source.encode("utf-8")
    if not source.strip():
        raise SandboxPolicyError("candidate source is empty")
    if len(encoded) > MAX_SOURCE_BYTES:
        raise SandboxPolicyError(
            f"candidate source exceeds the {MAX_SOURCE_BYTES}-byte limit"
        )
    if not entrypoint.isidentifier() or entrypoint.startswith("_"):
        raise SandboxPolicyError("entrypoint must be a public Python identifier")
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as error:
        raise SandboxPolicyError(
            f"candidate source has invalid syntax at line {error.lineno}"
        ) from error
    policy = _SourcePolicy()
    policy.visit(tree)
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if entrypoint not in functions:
        raise SandboxPolicyError(f"candidate does not define {entrypoint!r}")
    if any(isinstance(node, ast.AsyncFunctionDef) and node.name == entrypoint for node in tree.body):
        raise SandboxPolicyError("async benchmark entrypoints are not supported")
    return tuple(sorted(policy.features))


_HARNESS = r'''from __future__ import annotations
import contextlib
import io
import json
import runpy
import sys
import time
import traceback

candidate_path, request_path, result_path = sys.argv[1:4]
request = json.loads(open(request_path, "r", encoding="utf-8").read())
started = time.perf_counter()
results = []
status = "passed"
error = None
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        namespace = runpy.run_path(candidate_path, run_name="amca_candidate")
    function = namespace.get(request["entrypoint"])
    if not callable(function):
        raise TypeError("entrypoint is not callable")
    for case in request["cases"]:
        name = str(case.get("name", "unnamed"))
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                actual = function(**case.get("args", {}))
            expected_exceptions = []
            if case.get("expected_exception"):
                expected_exceptions.append(str(case["expected_exception"]))
            expected_exceptions.extend(str(x) for x in case.get("expected_exception_any", []))
            if expected_exceptions:
                results.append({"name": name, "passed": False, "detail": "expected exception was not raised"})
                status = "failed"
            elif actual == case.get("expected"):
                results.append({"name": name, "passed": True, "detail": "returned the declared expected value"})
            else:
                results.append({"name": name, "passed": False, "detail": "return value did not match the declared expected value"})
                status = "failed"
        except BaseException as exc:
            expected_exceptions = []
            if case.get("expected_exception"):
                expected_exceptions.append(str(case["expected_exception"]))
            expected_exceptions.extend(str(x) for x in case.get("expected_exception_any", []))
            passed = type(exc).__name__ in expected_exceptions
            results.append({
                "name": name,
                "passed": passed,
                "detail": ("raised declared " if passed else "raised unexpected ") + type(exc).__name__,
            })
            if not passed:
                status = "failed"
except BaseException as exc:
    status = "error"
    error = f"{type(exc).__name__}: {exc}"[:1000]

document = {
    "status": status,
    "cases": results,
    "elapsed_seconds": time.perf_counter() - started,
    "error": error,
}
open(result_path, "w", encoding="utf-8").write(json.dumps(document, allow_nan=False, separators=(",", ":")))
'''


def _posix_limit(memory_bytes: int):
    def apply() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES, MAX_RESULT_BYTES))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))

    return apply


def _assign_windows_job(process: subprocess.Popen[bytes], memory_bytes: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMITS),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = EXTENDED_LIMITS()
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x100 | 0x8
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = memory_bytes
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            kernel32.CloseHandle(job)
            return None
        handle = kernel32.OpenProcess(0x0100 | 0x0200 | 0x0001, False, process.pid)
        if not handle:
            kernel32.CloseHandle(job)
            return None
        assigned = bool(kernel32.AssignProcessToJobObject(job, handle))
        kernel32.CloseHandle(handle)
        if not assigned:
            kernel32.CloseHandle(job)
            return None
        return int(job)
    except (AttributeError, OSError, ValueError):
        return None


def _close_windows_job(handle: int | None) -> None:
    if handle is None or os.name != "nt":
        return
    import ctypes

    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def run_python_function_cases(
    source: str,
    *,
    entrypoint: str,
    cases: Sequence[Mapping[str, Any]],
    timeout_seconds: float = 5.0,
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
) -> PythonSandboxReport:
    """Execute declared cases with bounded resources and return structured evidence."""

    if not 0.05 <= float(timeout_seconds) <= 30.0:
        raise SandboxPolicyError("timeout must be between 0.05 and 30 seconds")
    if not 32 * 1024 * 1024 <= int(memory_bytes) <= 1024 * 1024 * 1024:
        raise SandboxPolicyError("memory limit must be between 32 MiB and 1 GiB")
    features = validate_python_source(source, entrypoint=entrypoint)
    request = {"entrypoint": entrypoint, "cases": [dict(case) for case in cases]}
    with tempfile.TemporaryDirectory(prefix="amca-python-") as directory:
        root = Path(directory)
        candidate_path = root / "candidate.py"
        request_path = root / "request.json"
        result_path = root / "result.json"
        error_path = root / "child-error.txt"
        harness_path = root / "harness.py"
        candidate_path.write_text(source, encoding="utf-8")
        request_path.write_text(
            json.dumps(request, allow_nan=False, ensure_ascii=False), encoding="utf-8"
        )
        harness_path.write_text(_HARNESS, encoding="utf-8")
        interpreter = Path(getattr(sys, "_base_executable", sys.executable))
        if not interpreter.stem.casefold().startswith("python"):
            raise SandboxPolicyError(
                "a standalone Python interpreter is required for sandbox execution"
            )
        command = [
            str(interpreter),
            "-I",
            "-S",
            str(harness_path),
            str(candidate_path),
            str(request_path),
            str(result_path),
        ]
        creationflags = 0
        preexec_fn = None
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            preexec_fn = _posix_limit(int(memory_bytes))
        child_environment = {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONHASHSEED": "0",
            "TEMP": str(root),
            "TMP": str(root),
        }
        if os.name == "nt":
            # Windows needs its loader root to initialize the interpreter. Only
            # these non-secret OS locations cross the sandbox boundary.
            child_environment["SYSTEMROOT"] = os.environ.get(
                "SYSTEMROOT", r"C:\Windows"
            )
            child_environment["WINDIR"] = os.environ.get("WINDIR", r"C:\Windows")
        with error_path.open("wb") as error_stream:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=child_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=error_stream,
                creationflags=creationflags,
                preexec_fn=preexec_fn,
            )
        job = _assign_windows_job(process, int(memory_bytes))
        memory_enforced = job is not None if os.name == "nt" else True
        try:
            process.wait(timeout=float(timeout_seconds))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            _close_windows_job(job)
            return PythonSandboxReport(
                status="timeout",
                cases=(),
                elapsed_seconds=float(timeout_seconds),
                memory_limit_enforced=memory_enforced,
                source_features=features,
                error="candidate exceeded the wall-clock deadline",
            )
        finally:
            if process.poll() is not None:
                _close_windows_job(job)
        if process.returncode != 0 or not result_path.exists():
            child_error = ""
            if error_path.exists():
                child_error = error_path.read_text(
                    encoding="utf-8", errors="replace"
                )[:1000].strip()
            return PythonSandboxReport(
                status="error",
                cases=(),
                elapsed_seconds=0.0,
                memory_limit_enforced=memory_enforced,
                source_features=features,
                error=(
                    f"sandbox exited with status {process.returncode}"
                    + (f": {child_error}" if child_error else "")
                ),
            )
        if result_path.stat().st_size > MAX_RESULT_BYTES:
            return PythonSandboxReport(
                status="error",
                cases=(),
                elapsed_seconds=0.0,
                memory_limit_enforced=memory_enforced,
                source_features=features,
                error="sandbox result exceeded the output limit",
            )
        try:
            document = json.loads(result_path.read_text(encoding="utf-8"))
            parsed_cases = tuple(
                PythonCaseResult(
                    name=str(item["name"]),
                    passed=bool(item["passed"]),
                    detail=str(item["detail"])[:500],
                )
                for item in document.get("cases", [])
                if isinstance(item, Mapping)
            )
            return PythonSandboxReport(
                status=str(document.get("status", "error")),
                cases=parsed_cases,
                elapsed_seconds=float(document.get("elapsed_seconds", 0.0)),
                memory_limit_enforced=memory_enforced,
                source_features=features,
                error=(str(document["error"])[:1000] if document.get("error") else None),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return PythonSandboxReport(
                status="error",
                cases=(),
                elapsed_seconds=0.0,
                memory_limit_enforced=memory_enforced,
                source_features=features,
                error=f"invalid sandbox result: {error}",
            )


__all__ = [
    "PythonCaseResult",
    "PythonSandboxReport",
    "SandboxPolicyError",
    "run_python_function_cases",
    "validate_python_source",
]
