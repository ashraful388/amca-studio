"""Fail-closed guards for data that AMCA is about to persist.

Credential values are deliberately rejected rather than rewritten.  This keeps
the event envelope immutable, so event digests and idempotency comparisons keep
their existing deterministic meaning.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
import re
from typing import Any


class SensitiveDataError(ValueError):
    """Raised when a credential-like key appears in persistable data."""


_CAMEL_BOUNDARY_1 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY_2 = re.compile(r"([a-z0-9])([A-Z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")

# Singular ``token`` is sensitive, while plural accounting fields such as
# input_tokens/output_tokens remain valid.  Multi-word key material is matched
# as a sequence so public_key and ordinary numeric keys are not blocked.
_SENSITIVE_WORDS = frozenset(
    {
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "passphrase",
        "passwd",
        "password",
        "secret",
        "token",
    }
)
_SENSITIVE_SEQUENCES = (
    ("api", "key"),
    ("access", "token"),
    ("auth", "token"),
    ("bearer", "token"),
    ("client", "secret"),
    ("connection", "string"),
    ("database", "url"),
    ("decryption", "key"),
    ("encryption", "key"),
    ("id", "token"),
    ("private", "key"),
    ("refresh", "token"),
    ("session", "cookie"),
    ("session", "token"),
    ("signing", "key"),
    ("webhook", "secret"),
)


def _key_words(key: object) -> tuple[str, ...]:
    text = str(key)
    text = _CAMEL_BOUNDARY_1.sub(r"\1_\2", text)
    text = _CAMEL_BOUNDARY_2.sub(r"\1_\2", text)
    return tuple(word for word in _NON_WORD.split(text.casefold()) if word)


_NON_SENSITIVE_COMPOUNDS = frozenset(
    {
        ("authorization", "record"),
        ("authorization", "records"),
        ("authorization", "rule"),
        ("authorization", "rules"),
        ("authorization", "reason"),
        ("authorization", "decision"),
        ("authorization", "status"),
    }
)


@lru_cache(maxsize=512)
def _is_sensitive_key_name(name: str) -> bool:
    words = _key_words(name)
    if words in _NON_SENSITIVE_COMPOUNDS:
        return False
    if any(word in _SENSITIVE_WORDS for word in words):
        return True
    pairs = set(zip(words, words[1:]))
    return any(sequence in pairs for sequence in _SENSITIVE_SEQUENCES)


def is_sensitive_key(key: object) -> bool:
    """Return whether *key* names credential material rather than telemetry."""

    return _is_sensitive_key_name(str(key))


def _path_child(path: str, key: object) -> str:
    name = str(key)
    if name.isidentifier():
        return f"{path}.{name}"
    return f"{path}[{json.dumps(name, ensure_ascii=False)}]"


def assert_no_sensitive_keys(value: Any, *, path: str = "data") -> None:
    """Recursively reject credential-like mapping keys without exposing values.

    The traversal accepts the same container shapes used by AMCA's JSON
    conversion.  Shared or cyclic containers are visited once; cycles still
    fail normal AMCA JSON serialization, but cannot hide an unvisited key.
    """

    seen: set[int] = set()

    def visit(item: Any, item_path: str) -> None:
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            for key, nested in item.items():
                if is_sensitive_key(key):
                    child_path = _path_child(item_path, key)
                    raise SensitiveDataError(
                        f"credential-like field {child_path!r} cannot be persisted"
                    )
                if isinstance(nested, (Mapping, list, tuple, set, frozenset)):
                    visit(nested, _path_child(item_path, key))
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            for index, nested in enumerate(item):
                visit(nested, f"{item_path}[{index}]")

    visit(value, path)


def assert_serialized_json_is_safe(
    raw: str | bytes | bytearray, *, path: str = "data"
) -> None:
    """Validate the exact JSON representation that a store intends to write."""

    assert_no_sensitive_keys(json.loads(raw), path=path)


__all__ = [
    "SensitiveDataError",
    "assert_no_sensitive_keys",
    "assert_serialized_json_is_safe",
    "is_sensitive_key",
]
