"""Best-effort, dependency-free local hardware profiling.

The profiler never downloads software or starts a model.  It supplies routing
hints; a model installation benchmark remains the authoritative measurement.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


GIB = 1024**3


@dataclass(frozen=True, slots=True)
class GPUProfile:
    """A locally detected graphics adapter."""

    name: str
    vendor: str = "unknown"
    total_vram_bytes: int | None = None
    free_vram_bytes: int | None = None
    driver_version: str | None = None


@dataclass(frozen=True, slots=True)
class LocalComputeProfile:
    """Portable snapshot used by the local-model router."""

    operating_system: str
    architecture: str
    cpu: str
    logical_cores: int
    total_ram_bytes: int | None
    available_ram_bytes: int | None
    disk_path: str
    free_disk_bytes: int
    gpus: tuple[GPUProfile, ...] = field(default_factory=tuple)
    accelerators: tuple[str, ...] = field(default_factory=tuple)
    recommended_local_tier: str = "cpu_small_quantized"
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _windows_memory() -> tuple[int | None, int | None]:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return None, None
    if not ok:
        return None, None
    return int(status.ullTotalPhys), int(status.ullAvailPhys)


def _posix_memory() -> tuple[int | None, int | None]:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None, None
    return int(page_size * total_pages), int(page_size * available_pages)


def _memory() -> tuple[int | None, int | None]:
    if os.name == "nt":
        return _windows_memory()
    return _posix_memory()


def _nvidia_gpus(timeout_seconds: float = 2.0) -> list[GPUProfile]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    command = [
        executable,
        "--query-gpu=name,memory.total,memory.free,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            # A console-subsystem tool must not open a window in the
            # operator's face: the hardware profile is measured while the
            # app is in use.
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
    except (OSError, subprocess.SubprocessError):
        return []

    profiles: list[GPUProfile] = []
    for line in result.stdout.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) != 4:
            continue
        name, total_mib, free_mib, driver = fields
        try:
            total = int(float(total_mib) * 1024**2)
            free = int(float(free_mib) * 1024**2)
        except ValueError:
            total = free = None
        profiles.append(
            GPUProfile(
                name=name,
                vendor="nvidia",
                total_vram_bytes=total,
                free_vram_bytes=free,
                driver_version=driver,
            )
        )
    return profiles


def _recommended_tier(
    total_ram_bytes: int | None, gpus: list[GPUProfile]
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    max_vram = max(
        (gpu.total_vram_bytes or 0 for gpu in gpus),
        default=0,
    )
    ram = total_ram_bytes or 0

    if max_vram >= 24 * GIB and ram >= 32 * GIB:
        return "gpu_large_quantized", warnings
    if max_vram >= 12 * GIB and ram >= 24 * GIB:
        return "gpu_medium_quantized", warnings
    if max_vram >= 8 * GIB and ram >= 16 * GIB:
        return "gpu_small_medium_quantized", warnings
    if max_vram >= 4 * GIB:
        return "gpu_small_quantized", warnings
    if ram >= 32 * GIB:
        warnings.append("No supported discrete GPU was detected; expect CPU-bound inference.")
        return "cpu_medium_quantized", warnings
    if ram and ram < 8 * GIB:
        warnings.append("Available system RAM is suitable only for very small local models.")
    elif not ram:
        warnings.append("System RAM could not be measured; benchmark before installing a model.")
    return "cpu_small_quantized", warnings


def model_fit_verdict(profile: "LocalComputeProfile", size_bytes: int) -> int:
    """DEF-CD2266 single authoritative hardware-fit verdict.

    Returns 0 = conservative fit, 1 = unknown, 2 = likely oversized.

    Shared by discovery/inventory, Model Hub readiness/verification, and the
    chat execution gate so a model can never be advertised as fit while the
    governed run gate would reject it (or vice versa).  The basis is the
    deterministic total-RAM budget (at most half of physical RAM may back
    model weights plus KV/state headroom, required = 1.5x weights) or the
    largest discrete GPU VRAM; volatile available-RAM is deliberately NOT
    used so the verdict cannot flip between a Model Hub listing and a later
    run attempt.  Transient memory pressure is still caught at execution.
    """
    if profile is None or size_bytes <= 0:
        return 1
    max_vram = max(
        (gpu.total_vram_bytes or 0 for gpu in (profile.gpus or ())),
        default=0,
    )
    required = int(size_bytes * 1.5)
    ram_budget = int(profile.total_ram_bytes * 0.5) if profile.total_ram_bytes else 0
    if ram_budget >= required or max_vram >= required:
        return 0
    return 2


def profile_local_compute(path: str | os.PathLike[str] | None = None) -> LocalComputeProfile:
    """Return a conservative hardware snapshot for *path*'s filesystem."""

    disk_path = Path(path or Path.cwd()).resolve()
    disk = shutil.disk_usage(disk_path)
    total_ram, available_ram = _memory()
    gpus = _nvidia_gpus()
    accelerators: list[str] = []
    if gpus:
        accelerators.append("cuda_candidate")
    if platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        accelerators.append("metal_candidate")
    if platform.system() == "Windows":
        accelerators.append("directml_candidate")
    if shutil.which("rocminfo"):
        accelerators.append("rocm_candidate")

    tier, warnings = _recommended_tier(total_ram, gpus)
    cpu = platform.processor().strip() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown")
    return LocalComputeProfile(
        operating_system=f"{platform.system()} {platform.release()}".strip(),
        architecture=platform.machine() or "unknown",
        cpu=cpu,
        logical_cores=os.cpu_count() or 1,
        total_ram_bytes=total_ram,
        available_ram_bytes=available_ram,
        disk_path=str(disk_path),
        free_disk_bytes=disk.free,
        gpus=tuple(gpus),
        accelerators=tuple(accelerators),
        recommended_local_tier=tier,
        warnings=tuple(warnings),
    )

