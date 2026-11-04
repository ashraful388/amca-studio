"""Durable, secret-free desktop model profile metadata.

API credentials are intentionally absent from this schema.  A profile ID is
also the opaque lookup name in the operating-system credential vault.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse
from uuid import uuid4


PROVIDER_KINDS = frozenset(
    {"openai", "anthropic", "kimi", "openai_compatible", "tabitoken"}
)
PRICING_BASES = frozenset({"token", "request", "free"})
_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,159}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_endpoint(value: str) -> str:
    endpoint = str(value).strip().rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("cloud model endpoint must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("endpoint cannot contain credentials, a query, or a fragment")
    return endpoint


def _finite_nonnegative(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    return number


@dataclass(frozen=True, slots=True)
class ModelProfile:
    profile_id: str
    provider_kind: str
    label: str
    model: str
    endpoint: str
    pricing_basis: str
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    price_per_request_usd: float = 0.0
    is_builtin: bool = False
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.profile_id):
            raise ValueError("invalid model profile ID")
        if self.provider_kind not in PROVIDER_KINDS:
            raise ValueError("unsupported model provider")
        if not _TEXT_RE.fullmatch(self.label.strip()):
            raise ValueError("model label is required and must not exceed 160 characters")
        if not _MODEL_RE.fullmatch(self.model.strip()):
            raise ValueError("model ID contains unsupported characters")
        object.__setattr__(self, "label", self.label.strip())
        object.__setattr__(self, "model", self.model.strip())
        object.__setattr__(self, "endpoint", _clean_endpoint(self.endpoint))
        if self.pricing_basis not in PRICING_BASES:
            raise ValueError("pricing_basis must be token, request, or free")
        if self.provider_kind == "tabitoken" and self.pricing_basis != "request":
            raise ValueError("Tabitoken profiles require fixed per-request pricing")
        if self.provider_kind in {"anthropic", "kimi"} and self.pricing_basis == "request":
            raise ValueError(
                "Anthropic and Kimi profiles support token or declared-free pricing"
            )
        for field_name in (
            "input_cost_per_million",
            "output_cost_per_million",
            "price_per_request_usd",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite_nonnegative(getattr(self, field_name), field=field_name),
            )
        if self.pricing_basis == "request" and self.price_per_request_usd <= 0:
            raise ValueError("per-request pricing requires a positive request price")
        if self.pricing_basis == "token" and not (
            self.input_cost_per_million > 0 or self.output_cost_per_million > 0
        ):
            raise ValueError("token pricing requires a positive input or output price")
        if self.pricing_basis == "free" and any(
            (
                self.input_cost_per_million,
                self.output_cost_per_million,
                self.price_per_request_usd,
            )
        ):
            raise ValueError("free profiles must use zero prices")

    def to_public_dict(self, *, configured: bool = False) -> dict[str, Any]:
        return {
            "credential_id": self.profile_id,
            "profile_id": self.profile_id,
            "provider": self.provider_kind,
            "model": self.model,
            "label": self.label,
            "endpoint": self.endpoint,
            "pricing_basis": self.pricing_basis,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "price_per_request_usd": self.price_per_request_usd,
            "team_selectable": True,
            "is_builtin": self.is_builtin,
            "configured": configured,
        }


BUILTIN_PROFILES: tuple[ModelProfile, ...] = tuple(
    ModelProfile(
        profile_id=f"tabitoken.{model}",
        provider_kind="tabitoken",
        label=label,
        model=model,
        endpoint="https://tabitoken.com/v1",
        pricing_basis="request",
        price_per_request_usd=price,
        is_builtin=True,
    )
    for model, label, price in (
        ("claude-opus-4-8", "Claude Opus 4.8", 0.50),
        ("claude-opus-4-8-thinking", "Claude Opus 4.8 Thinking", 0.50),
        ("claude-opus-5", "Claude Opus 5", 0.80),
        ("claude-opus-5-thinking", "Claude Opus 5 Thinking", 0.80),
    )
)


class ModelProfileStore:
    """Transactional profile catalog sharing AMCA's desktop SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS desktop_model_profiles (
                    profile_id TEXT PRIMARY KEY,
                    provider_kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    model TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    pricing_basis TEXT NOT NULL,
                    input_cost_per_million REAL NOT NULL,
                    output_cost_per_million REAL NOT NULL,
                    price_per_request_usd REAL NOT NULL,
                    is_builtin INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_desktop_model_profiles_label
                    ON desktop_model_profiles(label COLLATE NOCASE);
                """
            )
            for profile in BUILTIN_PROFILES:
                self._insert(connection, profile, ignore=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ModelProfile:
        return ModelProfile(
            profile_id=row["profile_id"],
            provider_kind=row["provider_kind"],
            label=row["label"],
            model=row["model"],
            endpoint=row["endpoint"],
            pricing_basis=row["pricing_basis"],
            input_cost_per_million=row["input_cost_per_million"],
            output_cost_per_million=row["output_cost_per_million"],
            price_per_request_usd=row["price_per_request_usd"],
            is_builtin=bool(row["is_builtin"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _insert(
        connection: sqlite3.Connection, profile: ModelProfile, *, ignore: bool = False
    ) -> None:
        now = _now()
        connection.execute(
            f"""
            INSERT {'OR IGNORE ' if ignore else ''}INTO desktop_model_profiles (
                profile_id, provider_kind, label, model, endpoint, pricing_basis,
                input_cost_per_million, output_cost_per_million,
                price_per_request_usd, is_builtin, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile.profile_id,
                profile.provider_kind,
                profile.label,
                profile.model,
                profile.endpoint,
                profile.pricing_basis,
                profile.input_cost_per_million,
                profile.output_cost_per_million,
                profile.price_per_request_usd,
                int(profile.is_builtin),
                profile.created_at or now,
                profile.updated_at or now,
            ),
        )

    def create(self, values: Mapping[str, Any]) -> ModelProfile:
        profile = ModelProfile(
            profile_id=f"custom.{uuid4().hex[:24]}",
            provider_kind=str(values.get("provider_kind", "")),
            label=str(values.get("label", "")),
            model=str(values.get("model", "")),
            endpoint=str(values.get("endpoint", "")),
            pricing_basis=str(values.get("pricing_basis", "")),
            input_cost_per_million=values.get("input_cost_per_million", 0),
            output_cost_per_million=values.get("output_cost_per_million", 0),
            price_per_request_usd=values.get("price_per_request_usd", 0),
        )
        with self._transaction() as connection:
            self._insert(connection, profile)
        result = self.get(profile.profile_id)
        assert result is not None
        return result

    def get(self, profile_id: str) -> ModelProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_model_profiles WHERE profile_id = ?",
                (str(profile_id),),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(self) -> list[ModelProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM desktop_model_profiles
                ORDER BY is_builtin DESC, label COLLATE NOCASE, profile_id
                """
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, profile_id: str) -> ModelProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise ValueError("model profile not found")
        if profile.is_builtin:
            raise ValueError("built-in model profiles cannot be deleted")
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM desktop_model_profiles WHERE profile_id = ?", (profile_id,)
            )
        return profile


__all__ = [
    "BUILTIN_PROFILES",
    "ModelProfile",
    "ModelProfileStore",
    "PRICING_BASES",
    "PROVIDER_KINDS",
]
