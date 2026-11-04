"""Durable local-model discovery, measurement, and routing evidence.

This module is deliberately provider-SDK free.  Discovery and measurement use
only an explicitly configured loopback Ollama endpoint, persist metadata rather
than prompts or model output, and never install or download a model.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeAlias
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .hardware import LocalComputeProfile
from .security import assert_no_sensitive_keys, assert_serialized_json_is_safe


Urlopen: TypeAlias = Callable[..., Any]


class _RejectRedirects(HTTPRedirectHandler):
    """Prevent a validated loopback endpoint from redirecting off-machine."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _urlopen_without_redirects(request: Request, *, timeout: float) -> Any:
    """Open one local-runtime request without following a redirect."""

    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def hardware_profile_sha256(profile: LocalComputeProfile) -> str:
    """Return a stable machine fingerprint without volatile free-capacity fields."""

    identity = {
        "operating_system": profile.operating_system,
        "architecture": profile.architecture,
        "cpu": profile.cpu,
        "logical_cores": profile.logical_cores,
        "total_ram_bytes": profile.total_ram_bytes,
        "disk_path": profile.disk_path,
        "gpus": [
            {
                "name": gpu.name,
                "vendor": gpu.vendor,
                "total_vram_bytes": gpu.total_vram_bytes,
                "driver_version": gpu.driver_version,
            }
            for gpu in profile.gpus
        ],
        "accelerators": list(profile.accelerators),
        "recommended_local_tier": profile.recommended_local_tier,
    }
    return _digest(identity)


def _loopback_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http":
        raise ValueError("local runtime URL must use http")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("local runtime URL cannot contain credentials, query, or fragment")
    if (parsed.hostname or "").casefold() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("local runtime URL must resolve to an explicit loopback host")
    if parsed.path not in {"", "/"}:
        raise ValueError("local runtime URL cannot contain a path")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class LocalModelRecord:
    provider: str
    model: str
    digest: str
    size_bytes: int
    modified_at: str | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    model_format: str | None = None
    present: bool = True
    observed_at: str = ""

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("local model provider and model are required")
        if self.size_bytes < 0:
            raise ValueError("local model size cannot be negative")
        if self.digest and len(self.digest) > 256:
            raise ValueError("local model digest is unreasonably long")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalModelMeasurement:
    id: str
    provider: str
    model: str
    hardware_profile_sha256: str
    prompt_sha256: str
    started_at: str
    completed_at: str
    wall_seconds: float
    prompt_tokens: int
    output_tokens: int
    tokens_per_second: float | None
    success: bool
    response_sha256: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        if self.wall_seconds < 0 or not math.isfinite(self.wall_seconds):
            raise ValueError("measurement wall time must be finite and non-negative")
        if self.prompt_tokens < 0 or self.output_tokens < 0:
            raise ValueError("measurement token counts cannot be negative")
        if self.tokens_per_second is not None and (
            self.tokens_per_second < 0 or not math.isfinite(self.tokens_per_second)
        ):
            raise ValueError("tokens_per_second must be finite and non-negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalRuntimeSnapshot:
    hardware_profile: LocalComputeProfile | None
    hardware_profile_sha256: str | None
    models: tuple[LocalModelRecord, ...]
    measurements: tuple[LocalModelMeasurement, ...]
    inventory_providers: tuple[str, ...] = ()

    def model(self, provider: str, model: str) -> LocalModelRecord | None:
        return next(
            (
                item
                for item in self.models
                if item.present and item.provider == provider and item.model == model
            ),
            None,
        )

    def latest_successful_measurement(
        self, provider: str, model: str
    ) -> LocalModelMeasurement | None:
        return next(
            (
                item
                for item in self.measurements
                if item.provider == provider and item.model == model and item.success
            ),
            None,
        )

    def fit_penalty(self, provider: str, model: str) -> int:
        """Return 0 for a conservative fit, 1 unknown, and 2 likely oversized.

        DEF-CD2266: delegates to the single authoritative evaluator.
        """
        from .hardware import model_fit_verdict

        installed = self.model(provider, model)
        return model_fit_verdict(self.hardware_profile, installed.size_bytes if installed else 0)

    def routing_rank(
        self, provider: str, model: str
    ) -> tuple[int, int, int, float, int, str]:
        """Return a stable rank; smaller values are preferred.

        Installed models rank before merely configured local adapters.  A
        successful measurement on the current hardware ranks before an
        unmeasured model, then higher measured throughput and smaller storage
        footprint break ties.  These are routing hints, not quality claims.
        """

        installed = self.model(provider, model)
        measurement = self.latest_successful_measurement(provider, model)
        current_measurement = bool(
            measurement
            and self.hardware_profile_sha256
            and measurement.hardware_profile_sha256 == self.hardware_profile_sha256
        )
        throughput = measurement.tokens_per_second if measurement else None
        return (
            0 if installed else 1,
            self.fit_penalty(provider, model),
            0 if current_measurement else 1,
            -(throughput or 0.0),
            installed.size_bytes if installed else 2**63 - 1,
            model,
        )


class LocalRuntimeStore:
    """SQLite persistence for hardware snapshots and local runtime telemetry."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:" and not self.path.startswith("file:"):
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
            uri=self.path.startswith("file:"),
        )
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_hardware_profiles (
                    profile_sha256 TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_local_hardware_captured
                    ON local_hardware_profiles(captured_at DESC);

                CREATE TABLE IF NOT EXISTS local_model_inventory (
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    modified_at TEXT,
                    family TEXT,
                    parameter_size TEXT,
                    quantization_level TEXT,
                    model_format TEXT,
                    present INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (provider, model)
                );

                CREATE TABLE IF NOT EXISTS local_inventory_refreshes (
                    provider TEXT PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    model_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_model_measurements (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    hardware_profile_sha256 TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    wall_seconds REAL NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    tokens_per_second REAL,
                    success INTEGER NOT NULL,
                    response_sha256 TEXT,
                    error_type TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_local_measurement_model
                    ON local_model_measurements(provider, model, completed_at DESC);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "LocalRuntimeStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save_hardware_profile(
        self, profile: LocalComputeProfile, *, captured_at: str | None = None
    ) -> str:
        document = profile.to_dict()
        assert_no_sensitive_keys(document, path="hardware_profile")
        payload = _canonical(document)
        assert_serialized_json_is_safe(payload, path="hardware_profile")
        profile_sha256 = hardware_profile_sha256(profile)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO local_hardware_profiles
                    (profile_sha256, profile_json, captured_at)
                VALUES (?, ?, ?)
                ON CONFLICT(profile_sha256) DO UPDATE SET
                    profile_json=excluded.profile_json,
                    captured_at=excluded.captured_at
                """,
                (profile_sha256, payload, captured_at or _now()),
            )
        return profile_sha256

    def latest_hardware_profile(self) -> tuple[str, LocalComputeProfile] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT profile_sha256, profile_json FROM local_hardware_profiles
                ORDER BY captured_at DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row["profile_json"])
        from .hardware import GPUProfile

        data["gpus"] = tuple(GPUProfile(**item) for item in data.get("gpus", ()))
        data["accelerators"] = tuple(data.get("accelerators", ()))
        data["warnings"] = tuple(data.get("warnings", ()))
        return str(row["profile_sha256"]), LocalComputeProfile(**data)

    def replace_inventory(
        self,
        provider: str,
        models: Sequence[LocalModelRecord],
        *,
        observed_at: str | None = None,
    ) -> tuple[LocalModelRecord, ...]:
        if not provider.strip():
            raise ValueError("inventory provider is required")
        observed = observed_at or _now()
        normalized: list[LocalModelRecord] = []
        names: set[str] = set()
        for model in models:
            if model.provider != provider:
                raise ValueError("inventory record provider does not match refresh provider")
            if model.model in names:
                raise ValueError(f"duplicate local model {model.model!r}")
            names.add(model.model)
            normalized.append(
                LocalModelRecord(
                    provider=model.provider,
                    model=model.model,
                    digest=model.digest,
                    size_bytes=model.size_bytes,
                    modified_at=model.modified_at,
                    family=model.family,
                    parameter_size=model.parameter_size,
                    quantization_level=model.quantization_level,
                    model_format=model.model_format,
                    present=True,
                    observed_at=observed,
                )
            )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO local_inventory_refreshes (provider, observed_at, model_count)
                VALUES (?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    model_count=excluded.model_count
                """,
                (provider, observed, len(normalized)),
            )
            self._connection.execute(
                "UPDATE local_model_inventory SET present = 0, observed_at = ? WHERE provider = ?",
                (observed, provider),
            )
            for model in normalized:
                document = model.to_dict()
                assert_no_sensitive_keys(document, path="local_model")
                self._connection.execute(
                    """
                    INSERT INTO local_model_inventory (
                        provider, model, digest, size_bytes, modified_at, family,
                        parameter_size, quantization_level, model_format, present,
                        observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(provider, model) DO UPDATE SET
                        digest=excluded.digest,
                        size_bytes=excluded.size_bytes,
                        modified_at=excluded.modified_at,
                        family=excluded.family,
                        parameter_size=excluded.parameter_size,
                        quantization_level=excluded.quantization_level,
                        model_format=excluded.model_format,
                        present=1,
                        observed_at=excluded.observed_at
                    """,
                    (
                        model.provider,
                        model.model,
                        model.digest,
                        model.size_bytes,
                        model.modified_at,
                        model.family,
                        model.parameter_size,
                        model.quantization_level,
                        model.model_format,
                        model.observed_at,
                    ),
                )
        return tuple(normalized)

    def list_models(
        self, *, provider: str | None = None, present_only: bool = True
    ) -> tuple[LocalModelRecord, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)
        if present_only:
            clauses.append("present = 1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM local_model_inventory"
                + where
                + " ORDER BY provider, model",
                params,
            ).fetchall()
        return tuple(
            LocalModelRecord(
                provider=str(row["provider"]),
                model=str(row["model"]),
                digest=str(row["digest"]),
                size_bytes=int(row["size_bytes"]),
                modified_at=row["modified_at"],
                family=row["family"],
                parameter_size=row["parameter_size"],
                quantization_level=row["quantization_level"],
                model_format=row["model_format"],
                present=bool(row["present"]),
                observed_at=str(row["observed_at"]),
            )
            for row in rows
        )

    def record_measurement(
        self, measurement: LocalModelMeasurement
    ) -> LocalModelMeasurement:
        document = measurement.to_dict()
        assert_no_sensitive_keys(document, path="local_model_measurement")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO local_model_measurements (
                    id, provider, model, hardware_profile_sha256, prompt_sha256,
                    started_at, completed_at, wall_seconds, prompt_tokens,
                    output_tokens, tokens_per_second, success, response_sha256,
                    error_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    measurement.id,
                    measurement.provider,
                    measurement.model,
                    measurement.hardware_profile_sha256,
                    measurement.prompt_sha256,
                    measurement.started_at,
                    measurement.completed_at,
                    measurement.wall_seconds,
                    measurement.prompt_tokens,
                    measurement.output_tokens,
                    measurement.tokens_per_second,
                    int(measurement.success),
                    measurement.response_sha256,
                    measurement.error_type,
                ),
            )
        return measurement

    def list_measurements(
        self, *, provider: str | None = None, model: str | None = None, limit: int = 200
    ) -> tuple[LocalModelMeasurement, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("measurement limit must be between 1 and 1000")
        clauses: list[str] = []
        params: list[Any] = []
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)
        if model is not None:
            clauses.append("model = ?")
            params.append(model)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM local_model_measurements"
                + where
                + " ORDER BY completed_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return tuple(
            LocalModelMeasurement(
                id=str(row["id"]),
                provider=str(row["provider"]),
                model=str(row["model"]),
                hardware_profile_sha256=str(row["hardware_profile_sha256"]),
                prompt_sha256=str(row["prompt_sha256"]),
                started_at=str(row["started_at"]),
                completed_at=str(row["completed_at"]),
                wall_seconds=float(row["wall_seconds"]),
                prompt_tokens=int(row["prompt_tokens"]),
                output_tokens=int(row["output_tokens"]),
                tokens_per_second=(
                    float(row["tokens_per_second"])
                    if row["tokens_per_second"] is not None
                    else None
                ),
                success=bool(row["success"]),
                response_sha256=row["response_sha256"],
                error_type=row["error_type"],
            )
            for row in rows
        )

    def snapshot(self) -> LocalRuntimeSnapshot:
        hardware = self.latest_hardware_profile()
        with self._lock:
            provider_rows = self._connection.execute(
                "SELECT provider FROM local_inventory_refreshes ORDER BY provider"
            ).fetchall()
        return LocalRuntimeSnapshot(
            hardware_profile=hardware[1] if hardware else None,
            hardware_profile_sha256=hardware[0] if hardware else None,
            models=self.list_models(),
            measurements=self.list_measurements(limit=1000),
            inventory_providers=tuple(str(row["provider"]) for row in provider_rows),
        )


def discover_ollama_models(
    base_url: str = "http://127.0.0.1:11434",
    *,
    timeout: float = 5.0,
    urlopen_fn: Urlopen | None = None,
) -> tuple[LocalModelRecord, ...]:
    """Read Ollama's local inventory without starting or downloading a model."""

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    base = _loopback_base_url(base_url)
    opener = urlopen_fn or _urlopen_without_redirects
    request = Request(f"{base}/api/tags", method="GET")
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw_models = payload.get("models") if isinstance(payload, Mapping) else None
    if not isinstance(raw_models, list):
        raise ValueError("Ollama inventory response is missing models")
    observed = _now()
    models: list[LocalModelRecord] = []
    for item in raw_models:
        if not isinstance(item, Mapping):
            raise ValueError("Ollama inventory contains an invalid model record")
        details = item.get("details") if isinstance(item.get("details"), Mapping) else {}
        name = item.get("name") or item.get("model")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Ollama inventory model is missing a name")
        model_format = str(details.get("format") or "").strip()
        # Ollama may expose cloud-backed catalog stubs through the same tags
        # endpoint.  They have no local GGUF format and commonly use a
        # ``-cloud`` tag; Offline inventory must fail closed rather than offer
        # one as a local model.
        if not model_format or name.casefold().endswith(("-cloud", ":cloud")):
            continue
        size = item.get("size", 0)
        if not isinstance(size, int) or size < 0:
            raise ValueError("Ollama inventory model size is invalid")
        models.append(
            LocalModelRecord(
                provider="ollama",
                model=name,
                digest=str(item.get("digest") or ""),
                size_bytes=size,
                modified_at=(str(item["modified_at"]) if item.get("modified_at") else None),
                family=(str(details["family"]) if details.get("family") else None),
                parameter_size=(
                    str(details["parameter_size"]) if details.get("parameter_size") else None
                ),
                quantization_level=(
                    str(details["quantization_level"])
                    if details.get("quantization_level")
                    else None
                ),
                model_format=model_format,
                observed_at=observed,
            )
        )
    return tuple(models)


def measure_ollama_model(
    model: str,
    profile: LocalComputeProfile,
    *,
    prompt: str = "Return exactly the word READY.",
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 120.0,
    urlopen_fn: Urlopen | None = None,
) -> LocalModelMeasurement:
    """Run one bounded local measurement and retain hashes, never content."""

    if not model.strip():
        raise ValueError("model is required")
    if not prompt or len(prompt.encode("utf-8")) > 16_384:
        raise ValueError("measurement prompt must be between 1 and 16384 UTF-8 bytes")
    if not 1 <= timeout <= 600:
        raise ValueError("measurement timeout must be between 1 and 600 seconds")
    base = _loopback_base_url(base_url)
    opener = urlopen_fn or _urlopen_without_redirects
    profile_sha256 = hardware_profile_sha256(profile)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    started_at = _now()
    started = time.perf_counter()
    response_text = ""
    prompt_tokens = output_tokens = 0
    tokens_per_second: float | None = None
    success = False
    error_type: str | None = None
    try:
        body = _canonical(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "keep_alive": "5m",
                "options": {"temperature": 0, "num_predict": 16},
            }
        ).encode("utf-8")
        request = Request(
            f"{base}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Ollama measurement response is invalid")
        response_text = str(payload.get("response") or "")
        prompt_tokens = max(0, int(payload.get("prompt_eval_count") or 0))
        output_tokens = max(0, int(payload.get("eval_count") or 0))
        eval_duration_ns = max(0, int(payload.get("eval_duration") or 0))
        if output_tokens and eval_duration_ns:
            tokens_per_second = output_tokens / (eval_duration_ns / 1_000_000_000)
        success = bool(payload.get("done", True))
        if not success:
            error_type = "incomplete_response"
    except Exception as exc:
        error_type = type(exc).__name__
    wall_seconds = time.perf_counter() - started
    completed_at = _now()
    return LocalModelMeasurement(
        id=uuid4().hex,
        provider="ollama",
        model=model,
        hardware_profile_sha256=profile_sha256,
        prompt_sha256=prompt_sha256,
        started_at=started_at,
        completed_at=completed_at,
        wall_seconds=wall_seconds,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        tokens_per_second=tokens_per_second,
        success=success,
        response_sha256=(
            hashlib.sha256(response_text.encode("utf-8")).hexdigest()
            if success
            else None
        ),
        error_type=error_type,
    )


__all__ = [
    "LocalModelMeasurement",
    "LocalModelRecord",
    "LocalRuntimeSnapshot",
    "LocalRuntimeStore",
    "discover_ollama_models",
    "hardware_profile_sha256",
    "measure_ollama_model",
]
