"""Provider credentials backed by the operating-system secret store.

Persistent secrets use Windows Credential Manager on the supported desktop
platform.  Explicit constructor values and process environment variables remain
available for ephemeral automation, but no API in this module writes secrets to
AMCA project files or SQLite.
"""

from __future__ import annotations

import ctypes
import base64
import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TARGET_PREFIX = "AMCA/provider/"
_MAX_SECRET_BYTES = 2560
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class CredentialError(RuntimeError):
    """Base class for operating-system credential failures."""


class CredentialNotFound(CredentialError):
    """The requested provider credential does not exist."""


class CredentialStoreUnavailable(CredentialError):
    """The platform has no supported persistent credential store."""


class CredentialStore(Protocol):
    def get(self, provider: str) -> str: ...
    def set(self, provider: str, secret: str) -> None: ...
    def delete(self, provider: str) -> bool: ...


def credential_target(provider: str) -> str:
    normalized = str(provider).strip().lower()
    if not _PROVIDER_RE.fullmatch(normalized):
        raise ValueError(
            "provider credential name must contain only letters, digits, dot, dash, or underscore"
        )
    return f"{_TARGET_PREFIX}{normalized}"


def _secret_bytes(secret: str) -> bytes:
    if not isinstance(secret, str) or not secret or "\x00" in secret:
        raise ValueError("credential must be a non-empty string without NUL characters")
    encoded = secret.encode("utf-8")
    if len(encoded) > _MAX_SECRET_BYTES:
        raise ValueError(f"credential exceeds the {_MAX_SECRET_BYTES}-byte Windows limit")
    return encoded


if sys.platform == "win32":
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


class WindowsCredentialStore:
    """Minimal ctypes binding to Windows generic credentials."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise CredentialStoreUnavailable(
                "Windows Credential Manager is available only on Windows"
            )
        self._api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._configure()

    @staticmethod
    def is_available() -> bool:
        return sys.platform == "win32"

    def _configure(self) -> None:
        pointer = ctypes.POINTER(_CREDENTIALW)
        self._api.CredReadW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.POINTER(pointer),
        ]
        self._api.CredReadW.restype = ctypes.c_int
        self._api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), ctypes.c_ulong]
        self._api.CredWriteW.restype = ctypes.c_int
        self._api.CredDeleteW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        self._api.CredDeleteW.restype = ctypes.c_int
        self._api.CredFree.argtypes = [ctypes.c_void_p]
        self._api.CredFree.restype = None

    @staticmethod
    def _last_error(action: str) -> CredentialError:
        code = ctypes.get_last_error()
        if code == _ERROR_NOT_FOUND:
            return CredentialNotFound(action)
        message = ctypes.FormatError(code).strip()
        return CredentialError(f"{action} failed with Windows error {code}: {message}")

    def get(self, provider: str) -> str:
        target = credential_target(provider)
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self._api.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            raise self._last_error(f"credential {target!r} was not found")
        try:
            credential = pointer.contents
            raw = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CredentialError("stored AMCA credential is not valid UTF-8") from exc
        finally:
            self._api.CredFree(pointer)

    def set(self, provider: str, secret: str) -> None:
        target = credential_target(provider)
        raw = _secret_bytes(secret)
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = _CREDENTIALW()
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "AMCA provider API credential"
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "AMCA"
        if not self._api.CredWriteW(ctypes.byref(credential), 0):
            raise self._last_error(f"could not store credential {target!r}")

    def delete(self, provider: str) -> bool:
        target = credential_target(provider)
        if self._api.CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
            return True
        error = self._last_error(f"could not delete credential {target!r}")
        if isinstance(error, CredentialNotFound):
            return False
        raise error


class WindowsDpapiCredentialStore:
    """Per-user DPAPI-encrypted fallback file for Windows desktop sessions."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | os.PathLike[str]) -> None:
        if sys.platform != "win32":
            raise CredentialStoreUnavailable("Windows DPAPI is available only on Windows")
        self.path = Path(path).resolve()
        self._lock = threading.RLock()
        self._api = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
        self._kernel = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        self._configure()

    def _configure(self) -> None:
        self._api.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_wchar_p,
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_DATA_BLOB),
        ]
        self._api.CryptProtectData.restype = ctypes.c_int
        self._api.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_DATA_BLOB),
        ]
        self._api.CryptUnprotectData.restype = ctypes.c_int
        self._kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel.LocalFree.restype = ctypes.c_void_p

    @staticmethod
    def _input_blob(data: bytes) -> tuple[_DATA_BLOB, object]:
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        return (
            _DATA_BLOB(
                len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
            ),
            buffer,
        )

    @staticmethod
    def _last_dpapi_error(action: str) -> CredentialError:
        code = ctypes.get_last_error()
        return CredentialError(
            f"{action} failed with Windows error {code}: {ctypes.FormatError(code).strip()}"
        )

    def _protect(self, value: bytes) -> bytes:
        source, source_buffer = self._input_blob(value)
        output = _DATA_BLOB()
        if not self._api.CryptProtectData(
            ctypes.byref(source),
            "AMCA desktop credential",
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            raise self._last_dpapi_error("could not protect credential")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel.LocalFree(output.pbData)
            del source_buffer

    def _unprotect(self, value: bytes) -> bytes:
        source, source_buffer = self._input_blob(value)
        output = _DATA_BLOB()
        description = ctypes.c_wchar_p()
        if not self._api.CryptUnprotectData(
            ctypes.byref(source),
            ctypes.byref(description),
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            raise self._last_dpapi_error("could not unprotect credential")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel.LocalFree(output.pbData)
            if description:
                self._kernel.LocalFree(ctypes.cast(description, ctypes.c_void_p))
            del source_buffer

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialError("encrypted credential file is unreadable") from exc
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != self.SCHEMA_VERSION
            or not isinstance(document.get("entries"), dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in document["entries"].items()
            )
        ):
            raise CredentialError("encrypted credential file has an invalid schema")
        return dict(document["entries"])

    def _save(self, entries: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(
            {"schema_version": self.SCHEMA_VERSION, "entries": entries},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)

    def get(self, provider: str) -> str:
        target = credential_target(provider)
        with self._lock:
            encoded = self._load().get(target)
            if encoded is None:
                raise CredentialNotFound(f"credential {target!r} was not found")
            try:
                protected = base64.b64decode(encoded, validate=True)
                return self._unprotect(protected).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise CredentialError("encrypted credential entry is invalid") from exc

    def set(self, provider: str, secret: str) -> None:
        target = credential_target(provider)
        raw = _secret_bytes(secret)
        with self._lock:
            entries = self._load()
            entries[target] = base64.b64encode(self._protect(raw)).decode("ascii")
            self._save(entries)

    def delete(self, provider: str) -> bool:
        target = credential_target(provider)
        with self._lock:
            entries = self._load()
            removed = entries.pop(target, None) is not None
            if removed:
                self._save(entries)
            return removed


@dataclass(slots=True)
class ResilientCredentialStore:
    """Prefer Credential Manager and fail over to per-user DPAPI storage."""

    primary: CredentialStore | None
    fallback: CredentialStore

    def get(self, provider: str) -> str:
        if self.primary is not None:
            try:
                return self.primary.get(provider)
            except CredentialError:
                pass
        return self.fallback.get(provider)

    def set(self, provider: str, secret: str) -> None:
        if self.primary is not None:
            try:
                self.primary.set(provider, secret)
            except CredentialError:
                pass
            else:
                self.fallback.delete(provider)
                return
        self.fallback.set(provider, secret)

    def delete(self, provider: str) -> bool:
        removed = False
        if self.primary is not None:
            try:
                removed = self.primary.delete(provider)
            except CredentialError:
                pass
        return self.fallback.delete(provider) or removed


def desktop_credential_store(directory: str | os.PathLike[str]) -> CredentialStore:
    """Return the Windows desktop store with a DPAPI fallback."""

    if sys.platform != "win32":
        raise CredentialStoreUnavailable("persistent desktop credentials require Windows")
    try:
        primary: CredentialStore | None = WindowsCredentialStore()
    except CredentialError:
        primary = None
    fallback = WindowsDpapiCredentialStore(Path(directory) / "credentials.dpapi.json")
    return ResilientCredentialStore(primary, fallback)


@dataclass(slots=True)
class MemoryCredentialStore:
    """Non-persistent store for tests and embedded callers."""

    _values: dict[str, str] = field(default_factory=dict, repr=False)

    def get(self, provider: str) -> str:
        target = credential_target(provider)
        try:
            return self._values[target]
        except KeyError as exc:
            raise CredentialNotFound(f"credential {target!r} was not found") from exc

    def set(self, provider: str, secret: str) -> None:
        self._values[credential_target(provider)] = _secret_bytes(secret).decode("utf-8")

    def delete(self, provider: str) -> bool:
        return self._values.pop(credential_target(provider), None) is not None


@dataclass(frozen=True, slots=True)
class ProviderCredentialResolver:
    """Resolve explicit, process-local, then OS-persisted credentials."""

    store: CredentialStore | None = None

    def resolve(
        self,
        *,
        provider: str,
        environment_variable: str,
        explicit: str | None = None,
    ) -> str | None:
        if explicit is not None:
            return explicit
        injected = os.environ.get(environment_variable)
        if injected:
            return injected
        store = self.store
        if store is None and WindowsCredentialStore.is_available():
            store = WindowsCredentialStore()
        if store is None:
            return None
        try:
            return store.get(provider)
        except CredentialNotFound:
            return None


__all__ = [
    "CredentialError",
    "CredentialNotFound",
    "CredentialStore",
    "CredentialStoreUnavailable",
    "MemoryCredentialStore",
    "ProviderCredentialResolver",
    "ResilientCredentialStore",
    "WindowsCredentialStore",
    "WindowsDpapiCredentialStore",
    "credential_target",
    "desktop_credential_store",
]
